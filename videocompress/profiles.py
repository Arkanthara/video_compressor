"""Human-readable encoding profiles and guidance text.

The GUI uses this module to provide production-focused presets with explicit
quality intent (for example Netflix-like or YouTube-like targets) while still
letting users override RC mode and quality factor manually.
"""

from __future__ import annotations

from dataclasses import dataclass

from videocompress.models import Container, TargetCodec
from videocompress.quality import default_threshold_for_metric

FAMILY_CAMERA_HEVC = "Camera / Streaming (HEVC)"
FAMILY_ARCHIVE_AV1 = "Archive / Delivery (AV1)"


@dataclass(frozen=True, slots=True)
class EncodingProfile:
    """Preset bundle for camera/high-bitrate and archive workflows."""

    name: str
    description: str
    codec: TargetCodec
    container: Container
    preset: str
    rc_mode: str
    quality: int
    quality_metric: str
    quality_threshold: float
    validate_quality: bool


CameraProfile = EncodingProfile


_PROFILES_BY_FAMILY: dict[str, dict[str, EncodingProfile]] = {
    FAMILY_CAMERA_HEVC: {
        "High Quality - Netflix-like Master": EncodingProfile(
            name="High Quality - Netflix-like Master",
            description=(
                "Highest quality HEVC profile for premium camera masters. "
                "Use when visual fidelity is more important than final size."
            ),
            codec=TargetCodec.HEVC,
            container=Container.MP4,
            preset="p7",
            rc_mode="vbr",
            quality=20,
            quality_metric="vmaf",
            quality_threshold=97.0,
            validate_quality=True,
        ),
        "Medium Quality - YouTube-like Upload": EncodingProfile(
            name="Medium Quality - YouTube-like Upload",
            description=(
                "Streaming-friendly quality target for online platforms. "
                "Good default for camera footage publishing."
            ),
            codec=TargetCodec.HEVC,
            container=Container.MP4,
            preset="p7",
            rc_mode="vbr",
            quality=24,
            quality_metric="vmaf",
            quality_threshold=95.0,
            validate_quality=True,
        ),
        "Balanced - Web Distribution": EncodingProfile(
            name="Balanced - Web Distribution",
            description=(
                "Balanced size/quality profile for websites and social media."
            ),
            codec=TargetCodec.HEVC,
            container=Container.MP4,
            preset="p6",
            rc_mode="vbr",
            quality=26,
            quality_metric="vmaf",
            quality_threshold=94.0,
            validate_quality=True,
        ),
        "Fast Proxy - Review & Approval": EncodingProfile(
            name="Fast Proxy - Review & Approval",
            description="Small proxy output for internal review workflows.",
            codec=TargetCodec.HEVC,
            container=Container.MP4,
            preset="p5",
            rc_mode="vbr",
            quality=30,
            quality_metric="ssim",
            quality_threshold=default_threshold_for_metric("ssim"),
            validate_quality=False,
        ),
    },
    FAMILY_ARCHIVE_AV1: {
        "High Quality - AV1 Studio Archive": EncodingProfile(
            name="High Quality - AV1 Studio Archive",
            description=(
                "High-fidelity AV1 archive profile for long-term retention "
                "with modern codec efficiency."
            ),
            codec=TargetCodec.AV1,
            container=Container.MKV,
            preset="p7",
            rc_mode="vbr",
            quality=22,
            quality_metric="vmaf",
            quality_threshold=96.0,
            validate_quality=True,
        ),
        "Medium Quality - AV1 Efficient Archive": EncodingProfile(
            name="Medium Quality - AV1 Efficient Archive",
            description=(
                "AV1 profile tuned for smaller storage footprint while keeping "
                "good perceptual quality."
            ),
            codec=TargetCodec.AV1,
            container=Container.MKV,
            preset="p6",
            rc_mode="vbr",
            quality=28,
            quality_metric="vmaf",
            quality_threshold=94.5,
            validate_quality=True,
        ),
        "Aggressive - AV1 Long-Term Storage": EncodingProfile(
            name="Aggressive - AV1 Long-Term Storage",
            description=(
                "Maximum size reduction target for cold storage and secondary "
                "assets where occasional quality loss is acceptable."
            ),
            codec=TargetCodec.AV1,
            container=Container.MKV,
            preset="p5",
            rc_mode="vbr",
            quality=32,
            quality_metric="ssim",
            quality_threshold=0.95,
            validate_quality=False,
        ),
    },
}

_DEFAULT_PROFILE_BY_FAMILY: dict[str, str] = {
    FAMILY_CAMERA_HEVC: "Medium Quality - YouTube-like Upload",
    FAMILY_ARCHIVE_AV1: "Medium Quality - AV1 Efficient Archive",
}


def profile_family_names() -> list[str]:
    """Return profile family names in display order."""
    return list(_PROFILES_BY_FAMILY.keys())


def default_profile_family_name() -> str:
    """Return the default profile family."""
    return FAMILY_CAMERA_HEVC


def profile_names(family_name: str) -> list[str]:
    """Return profile names for a family.

    Falls back to the default family when *family_name* is unknown.
    """
    family = _PROFILES_BY_FAMILY.get(family_name)
    if family is None:
        family = _PROFILES_BY_FAMILY[default_profile_family_name()]
    return list(family.keys())


def default_profile_name(family_name: str) -> str:
    """Return the default profile name for a family."""
    if family_name in _DEFAULT_PROFILE_BY_FAMILY:
        return _DEFAULT_PROFILE_BY_FAMILY[family_name]
    return _DEFAULT_PROFILE_BY_FAMILY[default_profile_family_name()]


def get_profile(family_name: str, profile_name: str) -> EncodingProfile:
    """Return a profile by family/name with safe fallbacks."""
    family = _PROFILES_BY_FAMILY.get(family_name)
    if family is None:
        family_name = default_profile_family_name()
        family = _PROFILES_BY_FAMILY[family_name]

    selected = family.get(profile_name)
    if selected is None:
        selected = family[default_profile_name(family_name)]
    return selected


def encoding_guide_text() -> str:
    """Return a concise encoding explainer for GUI display."""
    return (
        "Preset guide: p7 = best compression/quality (slowest), p6 = balanced, "
        "p5 = faster but less efficient.\n"
        "Quality factor (CQ/CRF): lower value means higher quality and larger files; "
        "higher value means smaller files but more artifacts. Typical movie defaults: "
        "20-22 (high), 24-26 (balanced), 28-32 (aggressive).\n"
        "RC modes: VBR (recommended for final delivery), "
        "ConstQP (fixed quality, less size-efficient), "
        "CBR (fixed bitrate for strict streaming pipes), "
        "CRF (CPU quality mode), Auto (engine decides).\n"
        "Post-encode quality validation compares source vs output "
        "(VMAF/SSIM/PSNR) after encoding and checks the threshold. "
        "If output is not smaller, the original is kept and "
        "validation is skipped by design."
    )


# Backward-compatible helpers used by older code/tests.
def camera_profile_names() -> list[str]:
    """Return names from the default camera family."""
    return profile_names(FAMILY_CAMERA_HEVC)


def default_camera_profile_name() -> str:
    """Return default profile name from camera family."""
    return default_profile_name(FAMILY_CAMERA_HEVC)


def get_camera_profile(name: str) -> CameraProfile:
    """Return a profile from camera family by name."""
    return get_profile(FAMILY_CAMERA_HEVC, name)
