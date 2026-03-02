from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobOptions,
    TargetCodec,
)
from videocompress.transcode import run_job


def _ffmpeg_encoder_available(encoder: str) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return encoder in (proc.stdout or "")


def _sample_input() -> Path | None:
    base = Path(__file__).resolve().parents[2] / "test_videos"
    candidate = base / "sample_mkv_01.mkv"
    return candidate if candidate.exists() else None


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("VIDEOCOMPRESS_INTEGRATION") != "1",
    reason="Set VIDEOCOMPRESS_INTEGRATION=1 to enable integration tests.",
)
def test_encode_smoke(tmp_path: Path) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available")

    if not _ffmpeg_encoder_available("libx265") and not _ffmpeg_encoder_available(
        "hevc_nvenc"
    ):
        pytest.skip("No HEVC encoder available")

    input_path = _sample_input()
    if input_path is None:
        pytest.skip("Sample input not found")

    use_gpu = _ffmpeg_encoder_available("hevc_nvenc")

    opts = JobOptions(
        input_path=input_path,
        output_dir=tmp_path,
        output_container=Container.MKV,
        codec=TargetCodec.HEVC,
        fallback_mode=FallbackMode.FALLBACK_CPU,
        preset="p5",
        quality=28,
        quality_mode=False,
        validate_quality=False,
        quality_metric="vmaf",
        quality_threshold=95.0,
        auto_search_best=False,
        enable_gpu_optimization=use_gpu,
        dry_run=False,
        overwrite=True,
        audio_mode=AudioMode.COPY,
    )

    outcome = run_job(opts)

    assert outcome.output_path.exists()
    assert outcome.output_size > 0
