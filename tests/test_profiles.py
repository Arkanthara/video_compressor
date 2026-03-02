from __future__ import annotations

from videocompress.models import Container, TargetCodec
from videocompress.profiles import (
    FAMILY_ARCHIVE_AV1,
    FAMILY_CAMERA_HEVC,
    default_profile_family_name,
    default_profile_name,
    encoding_guide_text,
    get_profile,
    profile_family_names,
    profile_names,
)


def test_profile_families_non_empty() -> None:
    families = profile_family_names()
    assert families
    assert default_profile_family_name() in families


def test_profile_names_non_empty_for_default_family() -> None:
    family = default_profile_family_name()
    names = profile_names(family)
    assert names
    assert default_profile_name(family) in names


def test_get_profile_default_fallback() -> None:
    family = FAMILY_CAMERA_HEVC
    profile = get_profile(family, "does-not-exist")
    assert profile.name == default_profile_name(family)


def test_netflix_like_profile_shape() -> None:
    profile = get_profile(FAMILY_CAMERA_HEVC, "High Quality - Netflix-like Master")
    assert profile.codec == TargetCodec.HEVC
    assert profile.container == Container.MP4
    assert profile.rc_mode in {"vbr", "constqp", "cbr", "crf", "auto"}
    assert 0 <= profile.quality <= 51


def test_archive_family_default_profile_shape() -> None:
    name = default_profile_name(FAMILY_ARCHIVE_AV1)
    profile = get_profile(FAMILY_ARCHIVE_AV1, name)
    assert profile.codec == TargetCodec.AV1
    assert profile.container == Container.MKV


def test_encoding_guide_mentions_key_controls() -> None:
    guide = encoding_guide_text().lower()
    assert "p7" in guide
    assert "rate" in guide
    assert "validation" in guide
