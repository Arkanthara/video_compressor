"""videocompress — GPU-accelerated visually-lossless video compressor.

Transcode H.264 videos to H.265 (HEVC) or AV1 using NVIDIA NVENC hardware
acceleration, with automatic quality-aware parameter optimization via VMAF,
SSIM, or PSNR gating.

Modules:
    capabilities  — FFmpeg / GPU encoder detection
    cli           — Command-line interface (file, batch, gui sub-commands)
    ffprobe_info  — Input file stream inspection via ffprobe
    gui           — Graphical user interface (customtkinter)
    models        — Dataclasses, enums, and error types
    profiles      — Camera/high-bitrate compression profiles
    quality       — Quality metrics computation and parameter search
    reporting     — JSON report generation
    transcode     — FFmpeg encoding pipeline and job runner
"""

__version__ = "0.1.0"
__all__ = [
    "__version__",
    "capabilities",
    "cli",
    "ffprobe_info",
    "gui",
    "models",
    "profiles",
    "quality",
    "reporting",
    "transcode",
]
