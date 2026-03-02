from __future__ import annotations

from pathlib import Path

import pytest

from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobError,
    JobOptions,
    TargetCodec,
)


def _base_options() -> JobOptions:
    return JobOptions(
        input_path=Path("input.mp4"),
        output_dir=Path("output"),
        output_container=Container.MKV,
        codec=TargetCodec.HEVC,
        fallback_mode=FallbackMode.FALLBACK_CODEC,
        preset="p5",
        quality=22,
        quality_mode=True,
        validate_quality=False,
        quality_metric="vmaf",
        quality_threshold=95.0,
        auto_search_best=False,
        enable_gpu_optimization=False,
        dry_run=True,
        overwrite=False,
        audio_mode=AudioMode.COPY,
    )


def test_job_options_validate_ok() -> None:
    opts = _base_options()
    opts.validate()


def test_job_options_invalid_metric() -> None:
    opts = _base_options()
    opts.quality_metric = "invalid"
    with pytest.raises(JobError):
        opts.validate()


def test_job_options_invalid_preset() -> None:
    opts = _base_options()
    opts.preset = "superfast"
    with pytest.raises(JobError):
        opts.validate()


def test_job_options_invalid_quality() -> None:
    opts = _base_options()
    opts.quality = 999
    with pytest.raises(JobError):
        opts.validate()


def test_job_options_invalid_rc_mode() -> None:
    opts = _base_options()
    opts.rc_mode = "bad"
    with pytest.raises(JobError):
        opts.validate()
