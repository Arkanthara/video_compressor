"""Data models, enumerations, and error types for videocompress.

This module defines the shared vocabulary used across the entire application:

- **Enums** — ``TargetCodec``, ``FallbackMode``, ``AudioMode``, ``Container``
- **Error** — ``JobError`` with machine-readable taxonomy strings
- **Probe** — ``ProbeResult`` from GPU / FFmpeg capability detection
- **Metrics** — ``MetricResult``, ``OptimizeCandidate``, ``OptimizeResult``
- **Job** — ``JobOptions`` (inputs) and ``JobOutcome`` (outputs)

All dataclasses use ``slots=True`` for memory efficiency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TargetCodec(StrEnum):
    """Video codec to use for the output file."""

    HEVC = "hevc"
    AV1 = "av1"
    AUTO = "auto"


class FallbackMode(StrEnum):
    """Strategy when the preferred GPU encoder is unavailable."""

    FAIL_FAST = "fail-fast"
    FALLBACK_CODEC = "fallback-codec"
    FALLBACK_CPU = "fallback-cpu"


class AudioMode(StrEnum):
    """Audio stream handling strategy."""

    COPY = "copy"
    AAC = "aac"
    OPUS = "opus"


class Container(StrEnum):
    """Output container format."""

    MP4 = "mp4"
    MKV = "mkv"


class JobError(RuntimeError):
    """Application error with a machine-readable ``taxonomy`` tag.

    The taxonomy string (e.g. ``"probe-missing-runtime"``,
    ``"encode-failed"``) enables structured error handling and
    consistent error reporting across CLI and GUI.
    """

    def __init__(self, taxonomy: str, message: str) -> None:
        super().__init__(message)
        self.taxonomy = taxonomy


@dataclass(slots=True)
class ProbeResult:
    """Result of probing the host system for FFmpeg and GPU capabilities."""

    ffmpeg_found: bool
    ffprobe_found: bool
    hevc_nvenc: bool
    av1_nvenc: bool
    h264_cuvid: bool = False
    diagnostics: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MetricResult:
    """One quality-metric measurement (VMAF, SSIM, or PSNR)."""

    name: str
    value: float | None
    available: bool
    raw: str | None = None


@dataclass(slots=True)
class SelectedPath:
    """Resolved encoding path: which codec, encoder, and GPU usage was chosen."""

    codec_request: TargetCodec
    effective_codec: TargetCodec
    encoder: str
    used_gpu: bool
    fallback_used: bool
    fallback_reason: str | None = None


@dataclass(slots=True)
class OptimizeCandidate:
    """A single encoding configuration to test during parameter search."""

    preset: str
    quality: int
    rc_mode: str = "vbr"
    bit_depth: int = 10
    extra_args: list[str] = field(default_factory=list)
    label: str = ""


@dataclass(slots=True)
class CandidateReport:
    """Result of testing a single candidate during parameter search."""

    label: str
    passed: bool
    early_aborted: bool
    avg_score: float | None
    worst_score: float | None
    p10_score: float | None
    total_size_bytes: int
    segments_tested: int
    segments_total: int


@dataclass(slots=True)
class OptimizeResult:
    preset: str
    quality: int
    score: float | None
    similarity_metric: str | None
    similarity_value: float | None
    sample_size_bytes: int | None
    rc_mode: str = "vbr"
    bit_depth: int = 10
    extra_args: list[str] = field(default_factory=list)
    search_report: list[CandidateReport] = field(default_factory=list)
    total_candidates: int = 0
    segments_count: int = 0
    segment_duration: float = 0.0


@dataclass(slots=True)
class JobOptions:
    """All user-supplied options for a single compression job."""

    input_path: Path
    output_dir: Path
    output_container: Container
    codec: TargetCodec
    fallback_mode: FallbackMode
    preset: str
    quality: int
    quality_mode: bool
    validate_quality: bool
    quality_metric: str
    auto_search_best: bool
    enable_gpu_optimization: bool
    dry_run: bool
    overwrite: bool
    audio_mode: AudioMode
    quality_threshold: float = 0.97
    report_json: Path | None = None
    lossless: bool = False
    encoder_extra_args: list[str] = field(default_factory=list)
    search_presets: list[str] = field(default_factory=list)

    def validate(self) -> None:
        valid_metrics = {"ssim", "psnr", "vmaf"}
        if self.quality_metric not in valid_metrics:
            raise JobError(
                "invalid-quality-metric",
                f"Unsupported quality metric '{self.quality_metric}'."
                f" Supported values: {sorted(valid_metrics)}",
            )

        if self.lossless:
            # No preset/quality validation in lossless mode — params are ignored
            return

        valid_presets = {
            "p1",
            "p2",
            "p3",
            "p4",
            "p5",
            "p6",
            "p7",
            "slow",
            "medium",
            "fast",
        }
        if self.preset not in valid_presets:
            raise JobError(
                "invalid-preset",
                f"Unsupported preset '{self.preset}'. Supported values: {sorted(valid_presets)}",
            )

        if not 0 <= self.quality <= 51:
            raise JobError(
                "invalid-quality",
                "Quality must be an integer between 0 and 51.",
            )


@dataclass(slots=True)
class JobOutcome:
    """Complete result of a finished compression job."""

    input_path: Path
    output_path: Path
    selected_path: SelectedPath
    probe: ProbeResult
    duration_seconds: float
    input_size: int
    output_size: int
    compression_ratio: float
    optimization: OptimizeResult | None
    metrics: dict[str, MetricResult]
    command: list[str]
    diagnostics: list[str]
    taxonomy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_path": str(self.input_path),
            "output_path": str(self.output_path),
            "codec_request": self.selected_path.codec_request.value,
            "effective_codec": self.selected_path.effective_codec.value,
            "encoder": self.selected_path.encoder,
            "used_gpu": self.selected_path.used_gpu,
            "fallback_used": self.selected_path.fallback_used,
            "fallback_reason": self.selected_path.fallback_reason,
            "probe": {
                "ffmpeg_found": self.probe.ffmpeg_found,
                "ffprobe_found": self.probe.ffprobe_found,
                "hevc_nvenc": self.probe.hevc_nvenc,
                "av1_nvenc": self.probe.av1_nvenc,
                "h264_cuvid": self.probe.h264_cuvid,
                "diagnostics": self.probe.diagnostics,
            },
            "duration_seconds": self.duration_seconds,
            "input_size": self.input_size,
            "output_size": self.output_size,
            "compression_ratio": self.compression_ratio,
            "optimization": None
            if self.optimization is None
            else {
                "preset": self.optimization.preset,
                "quality": self.optimization.quality,
                "score": self.optimization.score,
                "similarity_metric": self.optimization.similarity_metric,
                "similarity_value": self.optimization.similarity_value,
                "sample_size_bytes": self.optimization.sample_size_bytes,
                "rc_mode": self.optimization.rc_mode,
                "bit_depth": self.optimization.bit_depth,
                "total_candidates": self.optimization.total_candidates,
                "segments_count": self.optimization.segments_count,
                "segment_duration": self.optimization.segment_duration,
                "search_report": [
                    {
                        "label": r.label,
                        "passed": r.passed,
                        "early_aborted": r.early_aborted,
                        "avg_score": r.avg_score,
                        "worst_score": r.worst_score,
                        "p10_score": r.p10_score,
                        "total_size_bytes": r.total_size_bytes,
                        "segments_tested": r.segments_tested,
                        "segments_total": r.segments_total,
                    }
                    for r in (self.optimization.search_report or [])
                ],
            },
            "metrics": {
                name: {
                    "name": metric.name,
                    "value": metric.value,
                    "available": metric.available,
                    "raw": metric.raw,
                }
                for name, metric in self.metrics.items()
            },
            "command": self.command,
            "diagnostics": self.diagnostics,
            "taxonomy": self.taxonomy,
        }
