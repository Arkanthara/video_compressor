"""Input video inspection via ffprobe.

Extracts stream-level metadata (codec, resolution, pixel format, frame rate)
and container-level data (duration, bitrate, subtitle codecs) needed by the
transcoding pipeline to make informed encoding decisions.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from videocompress.models import JobError


@dataclass(slots=True)
class StreamInfo:
    """Key properties of the primary video stream."""

    codec_name: str | None
    width: int | None
    height: int | None
    pix_fmt: str | None
    avg_frame_rate: str | None


@dataclass(slots=True)
class InputInfo:
    """Aggregated metadata about an input file."""

    duration_seconds: float
    bitrate: int | None
    video: StreamInfo
    subtitle_codecs: list[str]


def inspect_input(path: Path) -> InputInfo:
    """Run ffprobe on *path* and return parsed :class:`InputInfo`.

    Raises :class:`~videocompress.models.JobError` if the file does not exist
    or ffprobe fails.
    """
    if not path.exists() or not path.is_file():
        raise JobError(
            "invalid-input-path",
            f"Input file not found: {path}",
        )

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise JobError("probe-missing-runtime", "ffprobe not found in PATH")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path.resolve()),
    ]

    proc = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise JobError("probe-input-failed", proc.stderr.strip() or "ffprobe failed")

    payload = json.loads(proc.stdout)
    format_block = payload.get("format", {})
    streams = payload.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})

    video = StreamInfo(
        codec_name=video_stream.get("codec_name"),
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        pix_fmt=video_stream.get("pix_fmt"),
        avg_frame_rate=video_stream.get("avg_frame_rate"),
    )

    duration = float(format_block.get("duration") or 0.0)
    bitrate = format_block.get("bit_rate")
    subtitle_codecs = [
        str(stream.get("codec_name"))
        for stream in streams
        if stream.get("codec_type") == "subtitle" and stream.get("codec_name")
    ]

    return InputInfo(
        duration_seconds=duration,
        bitrate=int(bitrate) if bitrate and str(bitrate).isdigit() else None,
        video=video,
        subtitle_codecs=subtitle_codecs,
    )
