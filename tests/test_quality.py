from __future__ import annotations

from videocompress.quality import default_threshold_for_metric


def test_default_thresholds() -> None:
    assert default_threshold_for_metric("vmaf") == 95.0
    assert default_threshold_for_metric("ssim") == 0.97
    assert default_threshold_for_metric("psnr") == 40.0
