"""Input discovery helpers for videocompress.

Centralizes the list of known video extensions and provides utilities to
scan folders while still allowing ffprobe-based detection for unknown
extensions (ex: camera dumps, ISO images, or uncommon containers).
"""

from __future__ import annotations

from pathlib import Path

from videocompress.ffprobe_info import has_video_stream

VIDEO_EXTENSIONS: tuple[str, ...] = (
    ".3g2",
    ".3gp",
    ".avi",
    ".f4v",
    ".flv",
    ".iso",
    ".m2ts",
    ".m2v",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp2",
    ".mp4",
    ".mpe",
    ".mpeg",
    ".mpg",
    ".mts",
    ".mxf",
    ".ogv",
    ".ts",
    ".vob",
    ".webm",
    ".wmv",
)


def video_filetypes() -> list[tuple[str, str]]:
    """Return file dialog filters for common video formats."""
    patterns = " ".join(f"*{ext}" for ext in VIDEO_EXTENSIONS)
    return [("Video files", patterns), ("All files", "*.*")]


def is_known_video_extension(path: Path) -> bool:
    """Return True if *path* has a known video file extension."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def collect_video_files(
    base: Path,
    recursive: bool,
    *,
    probe_unknown: bool = True,
) -> list[Path]:
    """Collect video files under *base*.

    Known extensions are accepted immediately. For unknown extensions,
    ffprobe is used to detect whether a video stream exists (optional).
    """
    if not base.exists() or not base.is_dir():
        return []

    iterator = base.rglob("*") if recursive else base.iterdir()
    files: list[Path] = []

    for path in iterator:
        if not path.is_file():
            continue
        if is_known_video_extension(path):
            files.append(path)
        elif probe_unknown and has_video_stream(path):
            files.append(path)

    return sorted({p for p in files}, key=lambda p: str(p).lower())
