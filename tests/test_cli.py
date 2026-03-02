from __future__ import annotations

from pathlib import Path

import pytest

from videocompress import cli
from videocompress.models import AudioMode, Container, FallbackMode, TargetCodec
from videocompress.quality import default_threshold_for_metric


def _parse(args: list[str]) -> object:
    return cli._build_parser().parse_args(args)


def test_parse_file_defaults() -> None:
    args = _parse(["file", "input.mp4"])
    opts = cli._options_from_args(args, Path("input.mp4"))

    assert opts.codec == TargetCodec.AUTO
    assert opts.output_container == Container.MKV
    assert opts.audio_mode == AudioMode.COPY
    assert opts.fallback_mode == FallbackMode.FALLBACK_CODEC
    assert opts.quality_metric == "vmaf"
    assert opts.quality_threshold == default_threshold_for_metric("vmaf")
    assert opts.enable_gpu_optimization is True
    assert opts.rc_mode == "vbr"


def test_search_presets_parsing() -> None:
    args = _parse(["file", "input.mp4", "--search-presets", "p7,p6"])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.search_presets == ["p7", "p6"]


def test_auto_search_toggle() -> None:
    args = _parse(["file", "input.mp4", "--no-auto-search-best"])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.auto_search_best is False


def test_disable_gpu_optimization_flag() -> None:
    args = _parse(["file", "input.mp4", "--disable-gpu-optimization"])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.enable_gpu_optimization is False


@pytest.mark.parametrize("rc_mode", ["auto", "vbr", "constqp", "cbr", "crf"])
def test_rc_mode_choices(rc_mode: str) -> None:
    args = _parse(["file", "input.mp4", "--rc-mode", rc_mode])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.rc_mode == rc_mode


@pytest.mark.parametrize("codec", ["auto", "hevc", "av1"])
def test_codec_choices(codec: str) -> None:
    args = _parse(["file", "input.mp4", "--codec", codec])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.codec.value == codec


@pytest.mark.parametrize("container", ["mkv", "mp4"])
def test_container_choices(container: str) -> None:
    args = _parse(["file", "input.mp4", "--container", container])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.output_container.value == container


@pytest.mark.parametrize("audio", ["copy", "aac", "opus"])
def test_audio_choices(audio: str) -> None:
    args = _parse(["file", "input.mp4", "--audio", audio])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.audio_mode.value == audio


@pytest.mark.parametrize(
    "fallback",
    ["fail-fast", "fallback-codec", "fallback-cpu"],
)
def test_fallback_choices(fallback: str) -> None:
    args = _parse(["file", "input.mp4", "--fallback-mode", fallback])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.fallback_mode.value == fallback


@pytest.mark.parametrize("metric", ["vmaf", "ssim", "psnr"])
def test_metric_choices(metric: str) -> None:
    args = _parse(["file", "input.mp4", "--quality-metric", metric])
    opts = cli._options_from_args(args, Path("input.mp4"))
    assert opts.quality_metric == metric
