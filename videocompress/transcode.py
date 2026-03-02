"""FFmpeg encoding pipeline and job runner.

Orchestrates the full video compression workflow:

1. Probe host capabilities (GPU encoders, decoders)
2. Inspect the input file (codec, duration, streams)
3. Select the optimal encoding path (GPU vs CPU, fallback logic)
4. Optionally run the parameter optimizer for auto-search mode
5. Build the FFmpeg command with all resolved parameters
6. Execute FFmpeg with real-time progress parsing
7. Validate output quality (optional post-encode check)
8. Return a :class:`~videocompress.models.JobOutcome`

The public entry point is :func:`run_job`.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from videocompress.capabilities import ensure_gpu_probe_or_raise, probe_capabilities
from videocompress.ffprobe_info import InputInfo, inspect_input
from videocompress.models import (
    Container,
    FallbackMode,
    JobError,
    JobOptions,
    JobOutcome,
    ProbeResult,
    SelectedPath,
    TargetCodec,
)
from videocompress.quality import (
    _sample_positions,
    build_default_candidates,
    default_threshold_for_metric,
    optimize_encoding_params,
    run_metric,
)

ProgressCallback = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Codec / path selection
# ---------------------------------------------------------------------------


def _encoder_for(codec: TargetCodec, use_gpu: bool, lossless: bool) -> str:
    if lossless:
        # hevc_nvenc is the only NVENC encoder with true lossless mode (-tune lossless)
        return "hevc_nvenc" if use_gpu else "libx265"
    if codec == TargetCodec.AV1:
        return "av1_nvenc" if use_gpu else "libaom-av1"
    return "hevc_nvenc" if use_gpu else "libx265"


def _codec_suffix(codec: TargetCodec, lossless: bool) -> str:
    if lossless:
        return "hevc"  # lossless always uses hevc_nvenc
    return "av1" if codec == TargetCodec.AV1 else "hevc"


def _select_codec_path(
    opts: JobOptions,
    probe: ProbeResult,
    diagnostics: list[str],
) -> SelectedPath:
    ensure_gpu_probe_or_raise(probe)

    requested = opts.codec
    selected = requested

    if requested == TargetCodec.AUTO:
        # In lossless mode AV1 NVENC has no lossless support → always pick HEVC
        if opts.lossless:
            selected = TargetCodec.HEVC
        else:
            selected = TargetCodec.AV1 if probe.av1_nvenc else TargetCodec.HEVC

    # av1_nvenc has no -tune lossless → silently fall back to hevc lossless
    if opts.lossless and selected == TargetCodec.AV1:
        diagnostics.append("lossless-mode:av1-has-no-lossless-support-switched-to-hevc")
        selected = TargetCodec.HEVC

    if not opts.enable_gpu_optimization:
        return SelectedPath(
            codec_request=requested,
            effective_codec=selected,
            encoder=_encoder_for(selected, use_gpu=False, lossless=opts.lossless),
            used_gpu=False,
            fallback_used=True,
            fallback_reason="gpu-optimization-disabled",
        )

    # For lossless we need hevc_nvenc specifically
    gpu_supported = (
        probe.hevc_nvenc
        if opts.lossless
        else (probe.av1_nvenc if selected == TargetCodec.AV1 else probe.hevc_nvenc)
    )

    if gpu_supported:
        return SelectedPath(
            codec_request=requested,
            effective_codec=selected,
            encoder=_encoder_for(selected, use_gpu=True, lossless=opts.lossless),
            used_gpu=True,
            fallback_used=False,
        )

    if opts.fallback_mode == FallbackMode.FAIL_FAST:
        raise JobError(
            "unsupported-gpu-path",
            "Required GPU encoder (hevc_nvenc) is not available on this system.",
        )

    if (
        opts.fallback_mode == FallbackMode.FALLBACK_CODEC
        and not opts.lossless
        and selected == TargetCodec.AV1
        and probe.hevc_nvenc
    ):
        return SelectedPath(
            codec_request=requested,
            effective_codec=TargetCodec.HEVC,
            encoder="hevc_nvenc",
            used_gpu=True,
            fallback_used=True,
            fallback_reason="av1-unavailable-switched-to-hevc",
        )

    return SelectedPath(
        codec_request=requested,
        effective_codec=selected,
        encoder=_encoder_for(selected, use_gpu=False, lossless=opts.lossless),
        used_gpu=False,
        fallback_used=True,
        fallback_reason="gpu-path-unavailable-used-cpu",
    )


# ---------------------------------------------------------------------------
# Container / output path resolution
# ---------------------------------------------------------------------------


def _resolve_container(
    requested: Container,
    subtitle_codecs: list[str],
    diagnostics: list[str],
) -> Container:
    if requested == Container.MKV and any(c == "mov_text" for c in subtitle_codecs):
        diagnostics.append("container-adjusted:mkv-to-mp4-for-mov_text-subtitle-copy")
        return Container.MP4
    return requested


def _output_path(
    input_path: Path,
    output_dir: Path,
    container: Container,
    codec_suffix: str,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{input_path.stem}.{codec_suffix}.{container.value}"


# ---------------------------------------------------------------------------
# FFmpeg command building
# ---------------------------------------------------------------------------


def _build_command(
    opts: JobOptions,
    selected: SelectedPath,
    output_path: Path,
    probe: ProbeResult,
    input_info: InputInfo,
) -> list[str]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("probe-missing-runtime", "ffmpeg not found in PATH")

    # Zero-copy GPU pipeline: NVDEC (h264_cuvid) → CUDA frames → NVENC
    # Only when: GPU encode is active, h264_cuvid available, input is h264
    use_cuvid = selected.used_gpu and probe.h264_cuvid and input_info.video.codec_name == "h264"

    cmd = [ffmpeg, "-hide_banner", "-y" if opts.overwrite else "-n"]
    encoder_args = list(opts.encoder_extra_args or [])

    if use_cuvid:
        # Full GPU zero-copy transcode path
        cmd += [
            "-hwaccel",
            "cuda",
            "-hwaccel_output_format",
            "cuda",
            "-c:v",
            "h264_cuvid",
        ]
    elif selected.used_gpu:
        # GPU encode only (software decode, GPU upload)
        cmd += ["-hwaccel", "cuda"]

    cmd += [
        "-i",
        str(opts.input_path.resolve()),
        "-map",
        "0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c:a",
        "copy",
        "-c:s",
        "copy",
    ]

    if opts.lossless:
        if selected.used_gpu:
            # hevc_nvenc true lossless mode: -tune lossless forces qp=0 + lossless profile
            cmd += ["-c:v", "hevc_nvenc", "-tune", "lossless", "-preset", "p7"]
        else:
            # libx265 lossless via x265-params
            cmd += ["-c:v", "libx265", "-x265-params", "lossless=1", "-preset", "medium"]
    else:
        if use_cuvid and encoder_args:
            try:
                pix_fmt_idx = encoder_args.index("-pix_fmt")
            except ValueError:
                pix_fmt_idx = -1

            if pix_fmt_idx >= 0 and pix_fmt_idx + 1 < len(encoder_args):
                pix_fmt = encoder_args[pix_fmt_idx + 1]
                del encoder_args[pix_fmt_idx : pix_fmt_idx + 2]
                cmd += ["-vf", f"scale_cuda=format={pix_fmt}"]

        cmd += ["-c:v", selected.encoder, "-preset", opts.preset]
        if encoder_args:
            # Use optimized encoding parameters from parameter search
            cmd += encoder_args
        elif selected.used_gpu and "nvenc" in selected.encoder:
            # VBR: quality-constrained variable bitrate — better efficiency than CQ-only
            cmd += ["-rc", "vbr", "-cq", str(opts.quality), "-b:v", "0"]
        else:
            cmd += ["-crf", str(opts.quality)]

    cmd.append(str(output_path))
    return cmd


# ---------------------------------------------------------------------------
# FFmpeg subprocess with real-time progress
# ---------------------------------------------------------------------------


def _run_ffmpeg(
    cmd: list[str],
    duration_s: float,
    progress_callback: ProgressCallback | None,
    stop_event: threading.Event | None,
) -> tuple[int, str]:
    """Run ffmpeg, parse -progress output, stream stderr in a thread. Returns (rc, stderr)."""
    # Inject -progress pipe:1 -nostats before the output path argument
    prog_cmd = cmd[:-1] + ["-progress", "pipe:1", "-nostats", cmd[-1]]

    proc = subprocess.Popen(
        prog_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stderr_chunks: list[str] = []

    def _drain_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_chunks.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    last_frame = 0
    speed = ""
    try:
        for raw_line in proc.stdout:  # type: ignore[union-attr]
            if stop_event and stop_event.is_set():
                proc.terminate()
                break
            line = raw_line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key == "out_time_us":
                try:
                    us = int(val)
                    pct = (
                        min(us / (duration_s * 1_000_000) * 100.0, 99.9) if duration_s > 0 else 0.0
                    )
                    if progress_callback:
                        label = f"Encoding… {pct:.1f}%  frame={last_frame}"
                        if speed:
                            label += f"  speed={speed}"
                        progress_callback(pct, label)
                except ValueError:
                    pass
            elif key == "frame":
                try:
                    last_frame = int(val)
                except ValueError:
                    pass
            elif key == "speed":
                speed = val
    finally:
        proc.wait()
        stderr_thread.join(timeout=5)

    if progress_callback and not (stop_event and stop_event.is_set()):
        progress_callback(100.0, f"Encoding complete — {last_frame} frames")

    return proc.returncode, "".join(stderr_chunks)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_job(
    opts: JobOptions,
    progress_callback: ProgressCallback | None = None,
    stop_event: threading.Event | None = None,
) -> JobOutcome:
    opts.validate()

    def _log(msg: str) -> None:
        if progress_callback:
            progress_callback(-1.0, msg)

    _log("Probing environment…")
    probe = probe_capabilities()
    input_info = inspect_input(opts.input_path)

    diagnostics = list(probe.diagnostics)
    selected = _select_codec_path(opts, probe, diagnostics)

    mode_str = "lossless" if opts.lossless else f"quality={opts.quality}"
    cuvid = selected.used_gpu and probe.h264_cuvid and input_info.video.codec_name == "h264"
    gpu_str = f"{'cuvid→' if cuvid else ''}{selected.encoder}"
    _log(f"Encoder: {gpu_str}  mode: {mode_str}  GPU: {selected.used_gpu}")

    optimization = None
    if not opts.lossless and opts.quality_mode and opts.auto_search_best:
        seg_positions = _sample_positions(input_info.duration_seconds, 20.0)
        n_segs = len(seg_positions)
        _log(
            f"Searching best encoding parameters…  "
            f"({n_segs} segment{'s' if n_segs != 1 else ''} × 20 s, "
            f"{opts.quality_metric} threshold={opts.quality_threshold:.2f})"
        )
        if progress_callback:
            progress_callback(0.0, "Optimizing quality parameters…")
        candidates = build_default_candidates(selected.effective_codec, selected.encoder)
        # Filter candidates to only the presets selected by the user (if not "all")
        if opts.search_presets:
            candidates = [c for c in candidates if c.preset in opts.search_presets]
            _log(
                f"Preset filter active: searching only "
                f"{opts.search_presets} ({len(candidates)} candidates)"
            )
        optimization = optimize_encoding_params(
            input_path=opts.input_path,
            encoder=selected.encoder,
            metric_name=opts.quality_metric,
            threshold=opts.quality_threshold,
            initial_candidates=candidates,
            container_suffix=opts.output_container.value,
            input_duration_seconds=input_info.duration_seconds,
        )
        opts.preset = optimization.preset
        opts.quality = optimization.quality
        opts.encoder_extra_args = optimization.extra_args
        if optimization.similarity_value is not None:
            score_str = (
                f"  {opts.quality_metric}="
                f"{optimization.similarity_value:.4f}"
                f"  (p10 floor="
                f"{opts.quality_threshold - 0.06:.2f})"
            )
            _log(
                f"Best params: preset={optimization.preset}"
                f"  quality={optimization.quality}"
                f"{score_str}"
            )
        else:
            _log(
                f"Best params: preset={optimization.preset}  quality={optimization.quality}"
                "  (metric unavailable — used safest candidate)"
            )

    effective_container = _resolve_container(
        requested=opts.output_container,
        subtitle_codecs=input_info.subtitle_codecs,
        diagnostics=diagnostics,
    )

    suffix = _codec_suffix(selected.effective_codec, opts.lossless)
    output_path = _output_path(opts.input_path, opts.output_dir, effective_container, suffix)
    command = _build_command(opts, selected, output_path, probe, input_info)

    _log(f"Output: {output_path}")
    if opts.dry_run:
        _log(f"DRY RUN command: {' '.join(command)}")

    start = time.perf_counter()
    if not opts.dry_run:
        if progress_callback:
            progress_callback(0.0, "Starting encode…")
        returncode, stderr_text = _run_ffmpeg(
            command,
            input_info.duration_seconds,
            progress_callback,
            stop_event,
        )
        if stop_event and stop_event.is_set():
            raise JobError("encode-cancelled", "Job was cancelled by user")
        if returncode != 0:
            raise JobError("encode-failed", stderr_text.strip())
    elapsed = time.perf_counter() - start

    input_size = opts.input_path.stat().st_size
    output_size = output_path.stat().st_size if output_path.exists() else 0
    compression_ratio = (output_size / input_size) if input_size else 0.0

    if not opts.dry_run and output_size > 0 and output_size >= input_size:
        diagnostics.append(
            f"warning:output-not-smaller "
            f"({output_size // 1_048_576} MB >= "
            f"{input_size // 1_048_576} MB) "
            "— source may already be efficiently "
            "compressed; consider a higher CQ "
            "or different codec"
        )

    metrics: dict = {}
    if opts.validate_quality and not opts.dry_run and output_path.exists():
        _log("Running post-encode quality validation...")
        val_metric_name = opts.quality_metric
        val_threshold = opts.quality_threshold
        metric = run_metric(opts.input_path, output_path, val_metric_name)
        # Fall back to SSIM if VMAF is unavailable
        if not metric.available and val_metric_name == "vmaf":
            val_metric_name = "ssim"
            val_threshold = default_threshold_for_metric("ssim")
            metric = run_metric(opts.input_path, output_path, val_metric_name)
            _log(f"VMAF unavailable for validation — fell back to SSIM (threshold={val_threshold})")
        metrics[val_metric_name] = metric
        if metric.available and metric.value is not None and metric.value < val_threshold:
            diagnostics.append(
                f"quality-failed:{val_metric_name}={metric.value:.4f}<threshold={val_threshold}"
            )
        elif metric.available and metric.value is not None:
            _log(f"Quality check: {val_metric_name}={metric.value:.4f} OK")

    if selected.fallback_used and selected.fallback_reason:
        diagnostics.append(f"fallback:{selected.fallback_reason}")

    return JobOutcome(
        input_path=opts.input_path,
        output_path=output_path,
        selected_path=selected,
        probe=probe,
        duration_seconds=elapsed if not opts.dry_run else input_info.duration_seconds,
        input_size=input_size,
        output_size=output_size,
        compression_ratio=compression_ratio,
        optimization=optimization,
        metrics=metrics,
        command=command,
        diagnostics=diagnostics,
        taxonomy=None,
    )
