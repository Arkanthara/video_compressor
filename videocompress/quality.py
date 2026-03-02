"""Quality metrics and automatic encoding parameter optimization.

This module provides:

- :func:`run_metric` — compute VMAF / SSIM / PSNR between two video files
- :func:`build_default_candidates` — generate the candidate grid for search
- :func:`optimize_encoding_params` — full parameter search with quality gating

The optimizer extracts representative segments from the input, encodes each
with every candidate configuration, measures quality via frame-level metrics,
and picks the smallest file whose worst-segment score passes both the average
threshold and the P10 (10th-percentile) floor.
"""

from __future__ import annotations

import json as _json
import re
import shutil
import statistics
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from videocompress.models import (
    CandidateReport,
    JobError,
    MetricResult,
    OptimizeCandidate,
    OptimizeResult,
    TargetCodec,
)

ProgressCallback = Callable[[float, str], None]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def _supports_filter(filter_name: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    proc = _run([ffmpeg, "-hide_banner", "-filters"])
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return filter_name in out


def metric_available(metric_name: str) -> bool:
    mapping = {"ssim": "ssim", "psnr": "psnr", "vmaf": "libvmaf"}
    filter_name = mapping[metric_name]
    return _supports_filter(filter_name)


def _escape_filter_path(path: Path) -> str:
    """Escape a file path for use in ffmpeg lavfi filter option values.

    On Windows the drive-letter colon (e.g. ``C:``) clashes with ffmpeg's
    colon-separated option syntax.  A double-backslash before the colon
    (``\\:``) is needed so ffmpeg's filter parser treats it as a literal
    colon rather than an option separator.
    """
    return str(path).replace("\\", "/").replace(":", "\\\\:")


def default_threshold_for_metric(metric_name: str) -> float:
    """Return a sensible default quality threshold for the given metric."""
    return {"ssim": 0.97, "psnr": 40.0, "vmaf": 95.0}.get(metric_name, 0.97)


def run_metric(input_ref: Path, encoded: Path, metric_name: str) -> MetricResult:
    """Compute the overall average quality metric between two video files."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return MetricResult(name=metric_name, value=None, available=False, raw="ffmpeg unavailable")

    if not metric_available(metric_name):
        return MetricResult(
            name=metric_name,
            value=None,
            available=False,
            raw=f"{metric_name} metric filter unavailable",
        )

    if metric_name == "ssim":
        filter_expr = "[0:v][1:v]ssim"
        # Summary line format: "SSIM Y:x.xxx U:x.xxx V:x.xxx All:x.xxx (xx.xx)"
        # Per-frame format:    "n:1 Y:x.xxx ... All:x.xxx (xx.xx)"
        # We want the summary line which is last — use findall and take the last match.
        parse = re.compile(r"All:([0-9.]+)")
    elif metric_name == "psnr":
        filter_expr = "[0:v][1:v]psnr"
        # Summary: "PSNR ... psnr_avg:xx.xx" or "average:xx.xx" at end
        parse = re.compile(r"average:([0-9.]+)")
    else:
        filter_expr = "[0:v][1:v]libvmaf"
        parse = re.compile(r"VMAF score:\s*([0-9.]+)")

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(input_ref),
        "-i",
        str(encoded),
        "-lavfi",
        filter_expr,
        "-f",
        "null",
        "-",
    ]

    proc = _run(cmd)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    # Use findall + last to get the summary value (per-frame lines appear first)
    matches = parse.findall(out)

    if not matches:
        return MetricResult(name=metric_name, value=None, available=True, raw=out)

    return MetricResult(name=metric_name, value=float(matches[-1]), available=True, raw=out)


def _run_vmaf_with_frame_stats(
    ffmpeg: str,
    input_ref: Path,
    encoded: Path,
    tmp_dir: Path | None = None,
) -> tuple[float | None, float | None]:
    """Run VMAF with per-frame JSON log and return (average, p10) scores.

    Uses libvmaf JSON log output to get per-frame VMAF scores, enabling
    P10 (10th percentile) computation for catching individual bad frames.
    Falls back to parsing stderr summary if JSON log fails.
    """
    cleanup_tmp = False
    if tmp_dir is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vmaf-"))
        cleanup_tmp = True

    try:
        log_path = tmp_dir / "vmaf_log.json"
        # Remove stale log from a previous segment evaluation
        if log_path.exists():
            log_path.unlink()

        log_path_escaped = _escape_filter_path(log_path)

        filter_expr = f"[0:v][1:v]libvmaf=log_fmt=json:log_path={log_path_escaped}:n_threads=4"

        cmd = [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(input_ref),
            "-i",
            str(encoded),
            "-lavfi",
            filter_expr,
            "-f",
            "null",
            "-",
        ]

        proc = _run(cmd)

        # Try to parse JSON log for per-frame scores
        if log_path.exists():
            try:
                data = _json.loads(log_path.read_text(encoding="utf-8"))
                frame_scores = [f["metrics"]["vmaf"] for f in data.get("frames", [])]
                if frame_scores:
                    avg = statistics.mean(frame_scores)
                    sorted_scores = sorted(frame_scores)
                    p10_idx = max(0, int(len(sorted_scores) * 0.10) - 1)
                    p10 = sorted_scores[p10_idx]
                    return avg, p10

                # Try pooled_metrics as fallback
                pooled = data.get("pooled_metrics", {}).get("vmaf", {})
                if "mean" in pooled:
                    score = pooled["mean"]
                    return score, pooled.get("min", score)

            except (KeyError, ValueError, _json.JSONDecodeError):
                pass

        # Fallback: parse stderr for summary score (covers both execution
        # success without JSON log and retries after JSON-log-path failures)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        match = re.search(r"VMAF score:\s*([0-9.]+)", out)
        if match:
            score = float(match.group(1))
            return score, score

        # If the JSON-log command failed entirely (e.g. path escaping issue),
        # retry with a simple filter that only writes to stderr.
        if proc.returncode != 0:
            simple_cmd = [
                ffmpeg,
                "-hide_banner",
                "-i",
                str(input_ref),
                "-i",
                str(encoded),
                "-lavfi",
                "[0:v][1:v]libvmaf=n_threads=4",
                "-f",
                "null",
                "-",
            ]
            proc2 = _run(simple_cmd)
            out2 = (proc2.stdout or "") + "\n" + (proc2.stderr or "")
            match2 = re.search(r"VMAF score:\s*([0-9.]+)", out2)
            if match2:
                score = float(match2.group(1))
                return score, score

        return None, None

    finally:
        if cleanup_tmp:
            import shutil as _shutil

            _shutil.rmtree(tmp_dir, ignore_errors=True)


def _run_metric_with_frame_stats(
    input_ref: Path,
    encoded: Path,
    metric_name: str,
    tmp_dir: Path | None = None,
) -> tuple[float | None, float | None]:
    """Return (average_score, p10_score) — the overall average and the 10th-percentile
    per-frame score.  The P10 catches individual bad frames that the average hides.
    Returns (None, None) if the metric is unavailable.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not metric_available(metric_name):
        return None, None

    if metric_name == "vmaf":
        return _run_vmaf_with_frame_stats(ffmpeg, input_ref, encoded, tmp_dir)

    if metric_name == "ssim":
        filter_expr = "[0:v][1:v]ssim"
        frame_re = re.compile(r"^n:\d+\s.*\bAll:([0-9.]+)", re.MULTILINE)
        summary_re = re.compile(r"SSIM\s+Y:.*\bAll:([0-9.]+)")
    elif metric_name == "psnr":
        filter_expr = "[0:v][1:v]psnr"
        frame_re = re.compile(r"^n:\d+\s.*\baverage:([0-9.]+)", re.MULTILINE)
        summary_re = re.compile(r"average:([0-9.]+)")
    else:
        return None, None

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(input_ref),
        "-i",
        str(encoded),
        "-lavfi",
        filter_expr,
        "-f",
        "null",
        "-",
    ]
    proc = _run(cmd)
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # Collect per-frame scores
    frame_scores = [float(m) for m in frame_re.findall(out)]

    # Summary average
    summary_matches = summary_re.findall(out)
    if summary_matches:
        avg = float(summary_matches[-1])
    elif frame_scores:
        avg = statistics.mean(frame_scores)
    else:
        return None, None

    # P10: 10th-percentile (worst 10% of frames must still pass)
    if frame_scores:
        frame_scores_sorted = sorted(frame_scores)
        p10_idx = max(0, int(len(frame_scores_sorted) * 0.10) - 1)
        p10 = frame_scores_sorted[p10_idx]
    else:
        p10 = avg

    return avg, p10


def _nvenc_extra_args(bit_depth: int) -> list[str]:
    """Build optimized NVENC HEVC encoding args for maximum compression quality.

    Parameters tuned for visually lossless compression with minimum file size:
    - B-frames with middle reference mode for better inter-prediction
    - 32-frame lookahead for smarter rate control decisions
    - Spatial + temporal AQ for perceptual quality in complex regions
    - Weighted prediction for smooth handling of fades
    - GOP size 250 for efficient keyframe spacing
    """
    args = [
        "-bf",
        "3",  # 3 B-frames for better compression
        "-b_ref_mode",
        "middle",  # use B-frames as references
        "-rc-lookahead",
        "32",  # 32-frame lookahead for rate control
        "-spatial-aq",
        "1",  # spatial adaptive quantization
        "-temporal-aq",
        "1",  # temporal adaptive quantization
        "-aq-strength",
        "8",  # moderate AQ strength (1-15)
        # NOTE: -weighted_pred is NOT used because NVENC does not support
        # weighted prediction when B-frames are enabled.
        "-g",
        "250",  # GOP size (keyframe interval)
    ]
    if bit_depth == 10:
        args += ["-profile:v", "main10", "-pix_fmt", "p010le"]
    return args


def _x265_extra_args(bit_depth: int) -> list[str]:
    """Build optimized x265 encoding args for maximum compression quality.

    Uses aggressive psycho-visual optimizations and motion estimation settings:
    - aq-mode=3: auto-variance AQ (best perceptual quality distribution)
    - psy-rd=2.0: strong psycho-visual rate-distortion optimization
    - psy-rdoq=1.0: psycho-visual RDO for quantization
    - sao=0: disable SAO (often better quality at same bitrate for sharp content)
    - deblock=-1,-1: slight deblocking reduction (preserves sharpness)
    - bframes=8: up to 8 B-frames for better compression
    - ref=5: 5 reference frames
    - rc-lookahead=60: 60-frame lookahead
    - me=umh: uneven multi-hex motion estimation (better than default)
    - subme=4: subpel motion estimation quality
    """
    x265_params = (
        "aq-mode=3:"
        "psy-rd=2.0:"
        "psy-rdoq=1.0:"
        "sao=0:"
        "deblock=-1,-1:"
        "bframes=8:"
        "ref=5:"
        "rc-lookahead=60:"
        "me=umh:"
        "subme=4"
    )
    args = ["-x265-params", x265_params]
    if bit_depth == 10:
        args += ["-pix_fmt", "yuv420p10le", "-profile:v", "main10"]
    return args


def _libaom_extra_args(bit_depth: int, cpu_used: int) -> list[str]:
    """Build libaom-av1 tuning args.

    ``cpu_used`` controls the speed/quality tradeoff (lower = better quality,
    higher = faster). Row-based multi-threading improves throughput on modern
    CPUs without changing quality policy.
    """
    args = ["-cpu-used", str(cpu_used), "-row-mt", "1"]
    if bit_depth == 10:
        args += ["-pix_fmt", "yuv420p10le"]
    return args


def build_default_candidates(codec: TargetCodec, encoder: str = "") -> list[OptimizeCandidate]:
    """Return a comprehensive set of candidates for parameter search.

    Candidates are designed to find the MINIMUM file size while preserving
    visual quality (visually lossless compression). They cover:

    - Multiple presets (p5/p6/p7 for nvenc; slow/slower for x265)
    - Wide CQ/CRF range for thorough size/quality exploration
    - VBR and ConstQP rate control modes (VBR generally wins for quality/size)
    - 8-bit and 10-bit encoding (10-bit is more efficient for gradients)
    - Optimized encoder params: AQ, B-frames, GOP, lookahead, psy-rd, etc.

    The optimizer tests all candidates on multiple reference segments using VMAF
    (or SSIM fallback), enforces both an average-score threshold and a per-frame
    P10 floor, applies early abort on quality failure, then picks the candidate
    with the smallest total size that still passes both checks.
    """
    candidates: list[OptimizeCandidate] = []

    if codec == TargetCodec.AV1:
        if "av1_nvenc" in encoder:
            # ── AV1 NVENC candidates ───────────────────────────────────
            for preset in ["p7", "p6", "p5"]:
                cq_values = (
                    [38, 34, 30, 26, 22]
                    if preset == "p7"
                    else [34, 30, 26]
                    if preset == "p6"
                    else [30, 26]
                )
                for cq in cq_values:
                    candidates.append(
                        OptimizeCandidate(
                            preset=preset,
                            quality=cq,
                            rc_mode="vbr",
                            bit_depth=10,
                            extra_args=["-bf", "3", "-g", "250"],
                            label=f"VBR-{preset}-cq{cq}-10bit",
                        )
                    )

        elif "libaom-av1" in encoder:
            # ── AV1 CPU (libaom) candidates ────────────────────────────
            for cpu_used, crf_values in [
                (8, [40, 36, 32]),
                (6, [36, 32, 28, 24]),
                (4, [32, 28, 24, 20]),
            ]:
                for crf in crf_values:
                    candidates.append(
                        OptimizeCandidate(
                            preset=f"cpu{cpu_used}",
                            quality=crf,
                            rc_mode="crf",
                            bit_depth=10,
                            extra_args=_libaom_extra_args(10, cpu_used),
                            label=f"CRF-cpu{cpu_used}-crf{crf}-10bit",
                        )
                    )

            for crf in [32, 28, 24]:
                candidates.append(
                    OptimizeCandidate(
                        preset="cpu6",
                        quality=crf,
                        rc_mode="crf",
                        bit_depth=8,
                        extra_args=_libaom_extra_args(8, 6),
                        label=f"CRF-cpu6-crf{crf}-8bit",
                    )
                )

        else:
            for crf in [34, 30, 26]:
                candidates.append(
                    OptimizeCandidate(
                        preset="cpu6",
                        quality=crf,
                        rc_mode="crf",
                        bit_depth=8,
                        extra_args=[],
                        label=f"CRF-cpu6-crf{crf}",
                    )
                )

    elif "libx265" in encoder or "x265" in encoder:
        # ── CPU x265 candidates ────────────────────────────────────────
        for preset in ["slow", "slower"]:
            crf_values = [28, 26, 24, 22, 20, 18] if preset == "slow" else [28, 26, 24, 22]
            for crf in crf_values:
                # 10-bit with full psycho-visual optimization
                candidates.append(
                    OptimizeCandidate(
                        preset=preset,
                        quality=crf,
                        rc_mode="crf",
                        bit_depth=10,
                        extra_args=_x265_extra_args(10),
                        label=f"CRF-{preset}-crf{crf}-10bit",
                    )
                )

        # 8-bit comparison at key CRF levels
        for crf in [26, 24, 22]:
            candidates.append(
                OptimizeCandidate(
                    preset="slow",
                    quality=crf,
                    rc_mode="crf",
                    bit_depth=8,
                    extra_args=_x265_extra_args(8),
                    label=f"CRF-slow-crf{crf}-8bit",
                )
            )

    else:
        # ── HEVC NVENC candidates (default for GPU) ────────────────────
        # Sorted from most aggressive (highest CQ = smallest file) to most
        # conservative (lowest CQ = highest quality).  The optimizer tests all
        # candidates, then picks the smallest file whose quality passes.

        # p7 (best compression preset) with wide CQ sweep
        for cq in [32, 30, 28, 26, 24, 22, 20, 18]:
            candidates.append(
                OptimizeCandidate(
                    preset="p7",
                    quality=cq,
                    rc_mode="vbr",
                    bit_depth=10,
                    extra_args=_nvenc_extra_args(10),
                    label=f"VBR-p7-cq{cq}-10bit",
                )
            )

        # p6 preset at key CQ levels
        for cq in [30, 28, 26, 24, 22, 20]:
            candidates.append(
                OptimizeCandidate(
                    preset="p6",
                    quality=cq,
                    rc_mode="vbr",
                    bit_depth=10,
                    extra_args=_nvenc_extra_args(10),
                    label=f"VBR-p6-cq{cq}-10bit",
                )
            )

        # p5 preset at moderate CQ levels
        for cq in [28, 26, 24, 22]:
            candidates.append(
                OptimizeCandidate(
                    preset="p5",
                    quality=cq,
                    rc_mode="vbr",
                    bit_depth=10,
                    extra_args=_nvenc_extra_args(10),
                    label=f"VBR-p5-cq{cq}-10bit",
                )
            )

        # 8-bit variants at key CQ levels for comparison
        for cq in [28, 26, 24, 22]:
            candidates.append(
                OptimizeCandidate(
                    preset="p7",
                    quality=cq,
                    rc_mode="vbr",
                    bit_depth=8,
                    extra_args=_nvenc_extra_args(8),
                    label=f"VBR-p7-cq{cq}-8bit",
                )
            )

        # ConstQP mode comparison (fixed QP, no rate control overhead)
        for cq in [28, 26, 24, 22]:
            candidates.append(
                OptimizeCandidate(
                    preset="p7",
                    quality=cq,
                    rc_mode="constqp",
                    bit_depth=10,
                    extra_args=_nvenc_extra_args(10),
                    label=f"CQP-p7-qp{cq}-10bit",
                )
            )

    # Absolute fallback if no candidates generated
    if not candidates:
        candidates = [
            OptimizeCandidate(preset="p5", quality=24, label="fallback-p5-cq24"),
            OptimizeCandidate(preset="p6", quality=28, label="fallback-p6-cq28"),
            OptimizeCandidate(preset="p7", quality=30, label="fallback-p7-cq30"),
        ]

    return candidates


def _choose_balanced_fallback(candidates: list[OptimizeCandidate]) -> OptimizeCandidate:
    """Pick a balanced fallback candidate when search cannot score candidates.

    Preference order:
    1) quality-based modes (vbr/crf) over constqp/cbr
    2) 10-bit over 8-bit
    3) quality closest to 28 (middle-of-the-road compression level)
    """
    preferred = [c for c in candidates if c.rc_mode in {"vbr", "crf"}] or list(candidates)
    return min(
        preferred,
        key=lambda c: (
            0 if c.bit_depth == 10 else 1,
            abs(c.quality - 28),
        ),
    )


def _extract_segment_lossless(
    ffmpeg: str,
    input_path: Path,
    out_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> bool:
    """Extract a reference segment with frame-accurate seek and lossless re-encode.

    Using -ss *after* -i forces ffmpeg to decode every frame up to the target position
    (slow but frame-accurate).  Output is encoded with libx264 -qp 0 (lossless) so every
    decoded pixel is preserved without any stream-copy alignment issues.
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(input_path),
        "-ss",
        f"{start_seconds:.3f}",  # after -i → frame-accurate seek
        "-t",
        str(duration_seconds),
        "-map",
        "0:v:0",
        "-an",
        # libx264 lossless — always available; produces byte-identical decoded frames
        "-c:v",
        "libx264",
        "-qp",
        "0",
        "-preset",
        "ultrafast",
        str(out_path),
    ]
    proc = _run(cmd)
    return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _encode_segment(
    ffmpeg: str,
    ref_path: Path,
    out_path: Path,
    encoder: str,
    candidate: OptimizeCandidate,
) -> bool:
    """Re-encode *ref_path* with the candidate's full parameter set.

    Rate control mode, bit depth, AQ, B-frames, GOP, and all other encoding
    parameters are taken from the candidate object.  This ensures that segment
    tests use the exact same parameters as the final full-file encode.
    """
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        str(ref_path),
        "-map",
        "0:v:0",
        "-an",
        "-c:v",
        encoder,
    ]

    if "libaom-av1" not in encoder:
        cmd += ["-preset", candidate.preset]

    # Rate control parameters
    if "nvenc" in encoder:
        if candidate.rc_mode == "constqp":
            cmd += ["-rc", "constqp", "-qp", str(candidate.quality)]
        elif candidate.rc_mode == "cbr":
            cmd += ["-rc", "cbr", "-b:v", f"{candidate.quality}k"]
        else:  # vbr (default, best quality/size ratio)
            cmd += ["-rc", "vbr", "-cq", str(candidate.quality), "-b:v", "0"]
    elif "libaom-av1" in encoder:
        cmd += ["-crf", str(candidate.quality), "-b:v", "0"]
    else:
        # libx265, libaom-av1, etc.
        cmd += ["-crf", str(candidate.quality)]

    # Extra optimized encoding args (AQ, B-frames, profile, pix_fmt, x265-params, etc.)
    cmd += list(candidate.extra_args)

    cmd.append(str(out_path))
    proc = _run(cmd)
    ok = proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    if not ok:
        # Log stderr so users can diagnose encoder failures
        stderr = (proc.stderr or "").strip()
        if stderr:
            # Extract just the most relevant error line(s)
            err_lines = [
                line
                for line in stderr.splitlines()
                if "Error" in line or "failed" in line.lower() or "not supported" in line.lower()
            ]
            if err_lines:
                import sys

                print(f"    encode-error: {err_lines[0].strip()}", file=sys.stderr)
    return ok


def _sample_positions(dur: float, segment_duration: float) -> list[float]:
    """Return seek positions that give representative coverage of the file.

    - Avoids the first 5% (may be title/intro) and last 5% (credits).
    - Uses 5 positions for long files, 3 for medium, 1 for short clips.
    - Every position is clamped so the segment fits entirely within the file.
    """
    usable_start = dur * 0.05
    usable_end = max(usable_start, dur * 0.95 - segment_duration)

    if usable_end <= usable_start:
        return [0.0]

    span = usable_end - usable_start
    if dur >= 300:  # 5+ minutes → 5 samples
        fracs = [0.0, 0.20, 0.42, 0.65, 0.88]
    elif dur >= 90:  # 1.5–5 minutes → 3 samples
        fracs = [0.0, 0.40, 0.80]
    else:  # short clip → 1 sample mid-way
        fracs = [0.5]

    return [usable_start + f * span for f in fracs]


def _build_candidate_encode_args(candidate: OptimizeCandidate, encoder: str) -> list[str]:
    """Build the full ffmpeg encoding args for a candidate (excluding -c:v and -preset).

    These args are stored in the OptimizeResult and used by the final full-file encode
    so that the production encode uses exactly the same parameters as the winning test.
    """
    args: list[str] = []
    if "nvenc" in encoder:
        if candidate.rc_mode == "constqp":
            args += ["-rc", "constqp", "-qp", str(candidate.quality)]
        elif candidate.rc_mode == "cbr":
            args += ["-rc", "cbr", "-b:v", f"{candidate.quality}k"]
        else:
            args += ["-rc", "vbr", "-cq", str(candidate.quality), "-b:v", "0"]
    elif "libaom-av1" in encoder:
        args += ["-crf", str(candidate.quality), "-b:v", "0"]
    else:
        args += ["-crf", str(candidate.quality)]
    args += list(candidate.extra_args)
    return args


def optimize_encoding_params(
    *,
    input_path: Path,
    encoder: str,
    metric_name: str,
    threshold: float,
    initial_candidates: list[OptimizeCandidate],
    container_suffix: str,
    input_duration_seconds: float = 0.0,
    progress_callback: ProgressCallback | None = None,
) -> OptimizeResult:
    """Find encoding params that minimise output size while keeping quality >= threshold.

    Algorithm
    ---------
    1. Extract N reference segments spread across the file using frame-accurate seek +
       lossless re-encode (libx264 -qp 0).
    2. For each candidate, encode every reference segment and compute per-frame
       quality statistics (average + P10, the 10th-percentile frame score).
    3. **Early abort**: if ANY segment scores below the threshold for a candidate,
       that candidate is immediately rejected and testing moves to the next one.
       This dramatically speeds up the search by skipping hopeless candidates.
    4. A candidate *passes* only when BOTH:
         * worst-segment average score  >= threshold
         * worst-segment P10 score      >= p10_floor
    5. Among all passing candidates, pick the one with the **smallest total encoded size**.
    6. A detailed search report is printed showing all tested parameters, quality scores,
       pass/fail/early-abort status, and the winning configuration.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise JobError("optimizer-missing-runtime", "ffmpeg not found in PATH")

    # ── Determine effective metric — fall back to SSIM if VMAF unavailable ──
    effective_metric = metric_name
    effective_threshold = threshold
    if metric_name == "vmaf" and not metric_available("vmaf"):
        print("  WARNING: VMAF not available in this ffmpeg build — falling back to SSIM")
        effective_metric = "ssim"
        effective_threshold = default_threshold_for_metric("ssim")

    # P10 floor: metric-dependent offset below threshold
    if effective_metric == "vmaf":
        p10_floor = effective_threshold - 3.0  # VMAF: 3-point margin (e.g. 95 -> 92)
    elif effective_metric == "ssim":
        p10_floor = effective_threshold - 0.02  # SSIM: 0.02 margin
    else:
        p10_floor = effective_threshold - 3.0  # PSNR: 3 dB margin

    segment_duration = 20  # seconds
    dur = input_duration_seconds
    positions = _sample_positions(dur, segment_duration)

    # ── Print search configuration ──────────────────────────────────────
    print(f"\n{'=' * 72}")
    print("  PARAMETER SEARCH — Visually Lossless Compression")
    print(f"{'=' * 72}")
    print(f"  Metric:          {effective_metric.upper()}")
    print(f"  Threshold:       {effective_threshold} (avg)  /  {p10_floor:.2f} (P10 floor)")
    print(f"  Segments:        {len(positions)} x {segment_duration}s")
    print(f"  Candidates:      {len(initial_candidates)}")
    print(f"  Encoder:         {encoder}")
    print("  Strategy:        Test all candidates, pick smallest passing one")
    print("  Early abort:     YES (reject candidate on first failing segment)")
    print(f"{'=' * 72}\n")

    best: OptimizeResult | None = None
    search_report: list[CandidateReport] = []

    with tempfile.TemporaryDirectory(prefix="videocompress-opt-") as tmp:
        tmp_dir = Path(tmp)

        # ── Step 1: extract frame-accurate lossless reference segments ──
        ref_clips: list[Path] = []
        for i, pos in enumerate(positions):
            ref = tmp_dir / f"ref_{i}.mp4"
            if _extract_segment_lossless(ffmpeg, input_path, ref, pos, segment_duration):
                ref_clips.append(ref)

        # Ultimate fallback: try a single segment at the 25% mark
        if not ref_clips:
            ref = tmp_dir / "ref_fallback.mp4"
            pos = max(0.0, dur * 0.25 - segment_duration / 2) if dur > 0 else 0.0
            if _extract_segment_lossless(ffmpeg, input_path, ref, pos, segment_duration):
                ref_clips.append(ref)

        if not ref_clips:
            fallback = _choose_balanced_fallback(initial_candidates)
            print("  Could not extract reference segments — using balanced fallback candidate")
            return OptimizeResult(
                preset=fallback.preset,
                quality=fallback.quality,
                score=None,
                similarity_metric=None,
                similarity_value=None,
                sample_size_bytes=None,
            )

        print(f"  Extracted {len(ref_clips)} reference segment(s)\n")

        segments_total = len(ref_clips)
        total_steps = max(1, len(initial_candidates) * max(1, segments_total))
        completed_steps = 0

        def _emit_progress(message: str, *, steps: int = 0) -> None:
            nonlocal completed_steps
            if steps:
                completed_steps = min(completed_steps + steps, total_steps)
            if progress_callback:
                pct = min((completed_steps / total_steps) * 100.0, 99.9)
                progress_callback(pct, message)

        # Track which preset groups already have a passing candidate.
        # Key = (preset, rc_mode, bit_depth).  Once one candidate passes for
        # a group, lower-CQ candidates in the same group only produce bigger
        # files with even better quality, so skip them.
        passed_groups: set[tuple[str, str, int]] = set()

        # ── Step 2: evaluate each candidate with early abort ────────────
        n_skipped = 0
        for idx, candidate in enumerate(initial_candidates, 1):
            label = candidate.label or f"{candidate.preset}-q{candidate.quality}"
            status_prefix = f"Optimizing quality... {idx}/{len(initial_candidates)}"

            # ── GROUP SKIP: already found smallest passing file for this group ──
            group_key = (candidate.preset, candidate.rc_mode, candidate.bit_depth)
            if group_key in passed_groups:
                n_skipped += 1
                print(
                    f"  [{idx:2d}/{len(initial_candidates)}]"
                    f" {label:40s} SKIPPED (group already passed)"
                )
                _emit_progress(
                    f"{status_prefix} (skipped)",
                    steps=max(1, segments_total),
                )
                search_report.append(
                    CandidateReport(
                        label=label,
                        passed=False,
                        early_aborted=False,
                        avg_score=None,
                        worst_score=None,
                        p10_score=None,
                        total_size_bytes=0,
                        segments_tested=0,
                        segments_total=len(ref_clips),
                    )
                )
                continue

            print(f"  [{idx:2d}/{len(initial_candidates)}] {label:40s} ", end="", flush=True)

            total_size = 0
            seg_avgs: list[float] = []
            seg_p10s: list[float] = []
            encode_failed = False
            early_aborted = False
            segments_tested = 0

            for i, ref_clip in enumerate(ref_clips):
                ext = container_suffix or "mkv"
                enc_out = tmp_dir / f"enc_{idx}_{i}.{ext}"

                if not _encode_segment(ffmpeg, ref_clip, enc_out, encoder, candidate):
                    encode_failed = True
                    segments_tested += 1
                    _emit_progress(
                        f"{status_prefix} (segment {segments_tested}/{segments_total})",
                        steps=1,
                    )
                    break

                total_size += enc_out.stat().st_size

                avg, p10 = _run_metric_with_frame_stats(
                    ref_clip,
                    enc_out,
                    effective_metric,
                    tmp_dir,
                )

                if avg is not None:
                    seg_avgs.append(avg)
                if p10 is not None:
                    seg_p10s.append(p10)

                segments_tested += 1
                _emit_progress(
                    f"{status_prefix} (segment {segments_tested}/{segments_total})",
                    steps=1,
                )

                # ── EARLY ABORT: reject candidate immediately on quality failure ──
                if avg is not None and avg < effective_threshold:
                    early_aborted = True
                    break
                if p10 is not None and p10 < p10_floor:
                    early_aborted = True
                    break

                # Clean up encoded file to save disk space in temp dir
                if enc_out.exists():
                    enc_out.unlink()

            if segments_tested < segments_total:
                _emit_progress(
                    f"{status_prefix} (skipped {segments_total - segments_tested} segments)",
                    steps=segments_total - segments_tested,
                )

            if encode_failed:
                print("ENCODE FAILED")
                search_report.append(
                    CandidateReport(
                        label=label,
                        passed=False,
                        early_aborted=False,
                        avg_score=None,
                        worst_score=None,
                        p10_score=None,
                        total_size_bytes=0,
                        segments_tested=0,
                        segments_total=len(ref_clips),
                    )
                )
                continue

            if early_aborted:
                worst_avg = min(seg_avgs) if seg_avgs else None
                worst_p10 = min(seg_p10s) if seg_p10s else None
                score_str = (
                    f"{effective_metric}={worst_avg:.2f}" if worst_avg is not None else "N/A"
                )
                p10_str = f"p10={worst_p10:.2f}" if worst_p10 is not None else ""
                print(
                    f"EARLY ABORT  {score_str}  {p10_str}  (seg {len(seg_avgs)}/{len(ref_clips)})"
                )
                search_report.append(
                    CandidateReport(
                        label=label,
                        passed=False,
                        early_aborted=True,
                        avg_score=worst_avg,
                        worst_score=worst_avg,
                        p10_score=worst_p10,
                        total_size_bytes=total_size,
                        segments_tested=len(seg_avgs),
                        segments_total=len(ref_clips),
                    )
                )
                continue

            # Quality gate: worst-case across all segments must pass both checks
            if seg_avgs:
                worst_avg = min(seg_avgs)
                worst_p10 = min(seg_p10s) if seg_p10s else worst_avg
                passed = (worst_avg >= effective_threshold) and (worst_p10 >= p10_floor)
                representative_score = worst_avg
            else:
                worst_avg = None
                worst_p10 = None
                passed = True
                representative_score = None

            size_mb = total_size / 1_048_576
            score_str = f"{effective_metric}={worst_avg:.2f}" if worst_avg is not None else "N/A"
            p10_str = f"p10={worst_p10:.2f}" if worst_p10 is not None else ""
            if passed:
                print(f"PASS  {score_str}  {p10_str}  size={size_mb:.1f}MB")
            else:
                print(f"FAIL  {score_str}  {p10_str}  size={size_mb:.1f}MB")

            search_report.append(
                CandidateReport(
                    label=label,
                    passed=passed,
                    early_aborted=False,
                    avg_score=statistics.mean(seg_avgs) if seg_avgs else None,
                    worst_score=worst_avg,
                    p10_score=worst_p10,
                    total_size_bytes=total_size,
                    segments_tested=len(seg_avgs),
                    segments_total=len(ref_clips),
                )
            )

            if not passed:
                continue

            # Mark this group as done — all remaining candidates in the same
            # (preset, rc_mode, bit_depth) group have lower CQ → bigger files.
            passed_groups.add(group_key)

            # Build the full encoding args for this candidate
            encode_args = _build_candidate_encode_args(candidate, encoder)

            candidate_result = OptimizeResult(
                preset=candidate.preset,
                quality=candidate.quality,
                score=representative_score,
                similarity_metric=effective_metric if seg_avgs else None,
                similarity_value=representative_score,
                sample_size_bytes=total_size,
                rc_mode=candidate.rc_mode,
                bit_depth=candidate.bit_depth,
                extra_args=encode_args,
                search_report=search_report,
                total_candidates=len(initial_candidates),
                segments_count=len(ref_clips),
                segment_duration=segment_duration,
            )

            if best is None or total_size < (best.sample_size_bytes or total_size + 1):
                best = candidate_result

        # ── Search summary ──────────────────────────────────────────────
        n_passed = sum(1 for r in search_report if r.passed)
        n_early = sum(1 for r in search_report if r.early_aborted)
        n_failed = sum(1 for r in search_report if not r.passed and not r.early_aborted)

        print(f"\n{'~' * 72}")
        print(
            f"  Search complete: {n_passed} passed, {n_failed} failed, "
            f"{n_early} early-aborted, {n_skipped} group-skipped"
        )
        print(f"  out of {len(initial_candidates)} candidates tested")

        if best is not None:
            best.search_report = search_report
            best.total_candidates = len(initial_candidates)
            best.segments_count = len(ref_clips)
            best.segment_duration = segment_duration
            size_str = (
                f"{best.sample_size_bytes / 1_048_576:.1f}MB" if best.sample_size_bytes else "N/A"
            )
            score_str = (
                f"{effective_metric}={best.similarity_value:.2f}"
                if best.similarity_value is not None
                else f"{effective_metric}=N/A"
            )
            print(
                f"  >> Winner: preset={best.preset} CQ={best.quality} "
                f"rc={best.rc_mode} {best.bit_depth}-bit  "
                f"{score_str}  "
                f"sample_size={size_str}"
            )
            print(f"{'~' * 72}\n")
            if progress_callback:
                progress_callback(100.0, "Optimization complete")
            return best

        # Nothing passed — return a balanced fallback candidate.
        fallback = _choose_balanced_fallback(initial_candidates)
        print(
            f"  No candidate passed quality threshold — using fallback: "
            f"{fallback.preset} q={fallback.quality}"
        )
        print(f"{'~' * 72}\n")

        encode_args = _build_candidate_encode_args(fallback, encoder)
        if progress_callback:
            progress_callback(100.0, "Optimization complete")
        return OptimizeResult(
            preset=fallback.preset,
            quality=fallback.quality,
            score=None,
            similarity_metric=None,
            similarity_value=None,
            sample_size_bytes=None,
            rc_mode=fallback.rc_mode,
            bit_depth=fallback.bit_depth,
            extra_args=encode_args,
            search_report=search_report,
            total_candidates=len(initial_candidates),
            segments_count=len(ref_clips),
            segment_duration=segment_duration,
        )
