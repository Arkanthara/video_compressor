from __future__ import annotations

from pathlib import Path

import pytest

from videocompress.ffprobe_info import InputInfo, StreamInfo
from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobOptions,
    ProbeResult,
    SelectedPath,
    TargetCodec,
)
from videocompress.transcode import (
    _build_command,
    _resolve_container,
    _resolve_rc_mode_for_encoder,
    run_job,
)


def _base_opts() -> JobOptions:
    return JobOptions(
        input_path=Path("test_videos/sample_mkv_01.mkv"),
        output_dir=Path("output"),
        output_container=Container.MP4,
        codec=TargetCodec.HEVC,
        fallback_mode=FallbackMode.FALLBACK_CODEC,
        preset="p7",
        quality=28,
        quality_mode=False,
        validate_quality=False,
        quality_metric="vmaf",
        quality_threshold=95.0,
        auto_search_best=False,
        enable_gpu_optimization=True,
        dry_run=True,
        overwrite=True,
        audio_mode=AudioMode.COPY,
    )


def test_resolve_container_keeps_mp4_for_text_subtitles() -> None:
    diagnostics: list[str] = []
    container = _resolve_container(Container.MP4, ["subrip", "ass"], diagnostics)
    assert container == Container.MP4
    assert not diagnostics


def test_resolve_container_falls_back_to_mkv_for_non_mp4_subtitles() -> None:
    diagnostics: list[str] = []
    container = _resolve_container(Container.MP4, ["hdmv_pgs_subtitle"], diagnostics)
    assert container == Container.MKV
    assert "container-adjusted:mp4-to-mkv-for-unsupported-subtitle-codec" in diagnostics


def test_build_command_transcodes_text_subtitles_to_mov_text_for_mp4(monkeypatch) -> None:
    monkeypatch.setattr("videocompress.transcode.shutil.which", lambda _: "ffmpeg")

    opts = _base_opts()
    selected = SelectedPath(
        codec_request=TargetCodec.HEVC,
        effective_codec=TargetCodec.HEVC,
        encoder="hevc_nvenc",
        used_gpu=True,
        fallback_used=False,
    )
    probe = ProbeResult(
        ffmpeg_found=True,
        ffprobe_found=True,
        hevc_nvenc=True,
        av1_nvenc=True,
        h264_cuvid=True,
        diagnostics=[],
    )
    input_info = InputInfo(
        duration_seconds=10.0,
        bitrate=None,
        video=StreamInfo(
            codec_name="h264",
            width=1920,
            height=1080,
            pix_fmt="yuv420p",
            avg_frame_rate="24000/1001",
        ),
        subtitle_codecs=["subrip"],
    )

    diagnostics: list[str] = []
    cmd = _build_command(
        opts=opts,
        selected=selected,
        container=Container.MP4,
        output_path=Path("output/sample_mkv_01.hevc.mp4"),
        probe=probe,
        input_info=input_info,
        diagnostics=diagnostics,
    )

    sub_idx = cmd.index("-c:s")
    assert cmd[sub_idx + 1] == "mov_text"
    assert "subtitle-adjusted:converted-to-mov_text-for-mp4" in diagnostics


def test_build_command_uses_constqp_when_requested(monkeypatch) -> None:
    monkeypatch.setattr("videocompress.transcode.shutil.which", lambda _: "ffmpeg")

    opts = _base_opts()
    opts.rc_mode = "constqp"
    selected = SelectedPath(
        codec_request=TargetCodec.HEVC,
        effective_codec=TargetCodec.HEVC,
        encoder="hevc_nvenc",
        used_gpu=True,
        fallback_used=False,
    )
    probe = ProbeResult(
        ffmpeg_found=True,
        ffprobe_found=True,
        hevc_nvenc=True,
        av1_nvenc=True,
        h264_cuvid=True,
        diagnostics=[],
    )
    input_info = InputInfo(
        duration_seconds=10.0,
        bitrate=None,
        video=StreamInfo(
            codec_name="mpeg4",
            width=1280,
            height=720,
            pix_fmt="yuv420p",
            avg_frame_rate="25/1",
        ),
        subtitle_codecs=[],
    )

    diagnostics: list[str] = []
    cmd = _build_command(
        opts=opts,
        selected=selected,
        container=Container.MP4,
        output_path=Path("output/sample.hevc.mp4"),
        probe=probe,
        input_info=input_info,
        diagnostics=diagnostics,
    )

    assert "-hwaccel" not in cmd
    assert "-rc" in cmd
    rc_idx = cmd.index("-rc")
    assert cmd[rc_idx + 1] == "constqp"
    qp_idx = cmd.index("-qp")
    assert cmd[qp_idx + 1] == str(opts.quality)


def test_resolve_rc_mode_adjusts_incompatible_cpu_mode() -> None:
    rc_mode, diagnostic = _resolve_rc_mode_for_encoder("vbr", "libx265")
    assert rc_mode == "crf"
    assert diagnostic is not None


def test_run_job_keeps_original_when_output_larger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"a" * 100)

    def _fake_run_ffmpeg(
        cmd: list[str],
        duration_s: float,
        progress_callback,
        stop_event,
    ) -> tuple[int, str]:
        Path(cmd[-1]).write_bytes(b"b" * 200)
        return 0, ""

    monkeypatch.setattr("videocompress.transcode.shutil.which", lambda _: "ffmpeg")
    monkeypatch.setattr(
        "videocompress.transcode.probe_capabilities",
        lambda: ProbeResult(
            ffmpeg_found=True,
            ffprobe_found=True,
            hevc_nvenc=False,
            av1_nvenc=False,
            h264_cuvid=False,
            diagnostics=[],
        ),
    )
    monkeypatch.setattr(
        "videocompress.transcode.inspect_input",
        lambda _: InputInfo(
            duration_seconds=1.0,
            bitrate=None,
            video=StreamInfo(
                codec_name="h264",
                width=1280,
                height=720,
                pix_fmt="yuv420p",
                avg_frame_rate="25/1",
            ),
            subtitle_codecs=[],
        ),
    )
    monkeypatch.setattr("videocompress.transcode._run_ffmpeg", _fake_run_ffmpeg)

    opts = JobOptions(
        input_path=input_path,
        output_dir=tmp_path / "out",
        output_container=Container.MKV,
        codec=TargetCodec.HEVC,
        fallback_mode=FallbackMode.FALLBACK_CPU,
        preset="p5",
        quality=28,
        quality_mode=False,
        validate_quality=False,
        quality_metric="vmaf",
        quality_threshold=95.0,
        rc_mode="vbr",
        auto_search_best=False,
        enable_gpu_optimization=False,
        dry_run=False,
        overwrite=True,
        audio_mode=AudioMode.COPY,
        keep_original_if_larger=True,
    )

    outcome = run_job(opts)

    assert outcome.copied_original is True
    assert outcome.output_path.name == input_path.name
    assert outcome.output_size == outcome.input_size
    assert "warning:encoded-output-not-smaller-original-kept" in outcome.diagnostics


def test_run_job_keeps_original_for_undecodable_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "input.avi"
    input_path.write_bytes(b"x" * 100)

    monkeypatch.setattr("videocompress.transcode.shutil.which", lambda _: "ffmpeg")
    monkeypatch.setattr(
        "videocompress.transcode.probe_capabilities",
        lambda: ProbeResult(
            ffmpeg_found=True,
            ffprobe_found=True,
            hevc_nvenc=True,
            av1_nvenc=True,
            h264_cuvid=False,
            diagnostics=[],
        ),
    )
    monkeypatch.setattr(
        "videocompress.transcode.inspect_input",
        lambda _: InputInfo(
            duration_seconds=10.0,
            bitrate=None,
            video=StreamInfo(
                codec_name="mpeg4",
                width=720,
                height=576,
                pix_fmt=None,
                avg_frame_rate="25/1",
            ),
            subtitle_codecs=[],
        ),
    )

    def _should_not_run(*args, **kwargs):
        raise AssertionError("_run_ffmpeg should not be called for undecodable input")

    monkeypatch.setattr("videocompress.transcode._run_ffmpeg", _should_not_run)

    opts = JobOptions(
        input_path=input_path,
        output_dir=tmp_path / "out",
        output_container=Container.MP4,
        codec=TargetCodec.HEVC,
        fallback_mode=FallbackMode.FALLBACK_CODEC,
        preset="p7",
        quality=28,
        quality_mode=True,
        validate_quality=True,
        quality_metric="vmaf",
        quality_threshold=95.0,
        rc_mode="vbr",
        auto_search_best=True,
        enable_gpu_optimization=True,
        dry_run=False,
        overwrite=True,
        audio_mode=AudioMode.COPY,
        keep_original_if_larger=True,
    )

    outcome = run_job(opts)

    assert outcome.copied_original is True
    assert outcome.output_path.name == input_path.name
    assert outcome.command == []
    assert "warning:input-video-undecodable-original-kept" in outcome.diagnostics
