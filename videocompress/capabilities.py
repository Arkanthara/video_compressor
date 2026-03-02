"""GPU and FFmpeg capability detection.

Probes the host system for:
  - ``ffmpeg`` / ``ffprobe`` availability in PATH
  - NVIDIA NVENC encoder support (``hevc_nvenc``, ``av1_nvenc``)
  - Hardware decoder availability (``h264_cuvid``)

The resulting :class:`~videocompress.models.ProbeResult` is used by the
transcoding pipeline to select the optimal encoding path.
"""

from __future__ import annotations

import shutil
import subprocess

from videocompress.models import JobError, ProbeResult


def _run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing stdout and stderr as text."""
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def probe_capabilities() -> ProbeResult:
    """Detect installed FFmpeg build capabilities and GPU encoder support.

    Returns a :class:`ProbeResult` populated with booleans for each
    encoder/decoder and a ``diagnostics`` list of human-readable notes.
    """
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")

    if not ffmpeg or not ffprobe:
        diagnostics: list[str] = []
        if not ffmpeg:
            diagnostics.append("ffmpeg was not found in PATH")
        if not ffprobe:
            diagnostics.append("ffprobe was not found in PATH")
        return ProbeResult(
            ffmpeg_found=bool(ffmpeg),
            ffprobe_found=bool(ffprobe),
            hevc_nvenc=False,
            av1_nvenc=False,
            diagnostics=diagnostics,
        )

    encoders = _run_cmd([ffmpeg, "-hide_banner", "-encoders"])
    text = (encoders.stdout or "") + "\n" + (encoders.stderr or "")

    decoders = _run_cmd([ffmpeg, "-hide_banner", "-decoders"])
    dec_text = (decoders.stdout or "") + "\n" + (decoders.stderr or "")

    hevc_nvenc = "hevc_nvenc" in text
    av1_nvenc = "av1_nvenc" in text
    h264_cuvid = "h264_cuvid" in dec_text
    diagnostics = []

    if not hevc_nvenc:
        diagnostics.append("hevc_nvenc not available in ffmpeg build")
    if not av1_nvenc:
        diagnostics.append("av1_nvenc not available in ffmpeg build")
    if not h264_cuvid:
        diagnostics.append("h264_cuvid decoder unavailable — GPU decode path disabled")

    return ProbeResult(
        ffmpeg_found=True,
        ffprobe_found=True,
        hevc_nvenc=hevc_nvenc,
        av1_nvenc=av1_nvenc,
        h264_cuvid=h264_cuvid,
        diagnostics=diagnostics,
    )


def ensure_gpu_probe_or_raise(probe: ProbeResult) -> None:
    """Raise :class:`JobError` if ffmpeg or ffprobe are missing."""
    if not probe.ffmpeg_found or not probe.ffprobe_found:
        raise JobError(
            "probe-missing-runtime",
            "Missing ffmpeg/ffprobe runtime. Ensure both are installed and available in PATH.",
        )
