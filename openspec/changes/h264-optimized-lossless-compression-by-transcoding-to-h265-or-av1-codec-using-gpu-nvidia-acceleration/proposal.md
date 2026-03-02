## Why

Many source videos are encoded in H.264 with near-lossless settings, producing larger files than necessary for archival and distribution. Modern codecs (H.265/HEVC and AV1) can preserve visually lossless quality at substantially lower bitrates, and NVIDIA GPUs can accelerate this workflow to keep processing time practical.

## What Changes

- Add a codec-optimization workflow that accepts H.264 input and transcodes it to H.265 or AV1 using NVIDIA GPU acceleration.
- Add a quality-preservation mode that targets visually lossless output while reducing file size.
- Add configurable codec and tuning options (codec target, quality level, preset, and optional fallback behavior).
- Add validation/reporting of output quality and compression ratio so users can confirm optimization outcomes.
- Add error handling for unsupported hardware/driver combinations and codec capability mismatches.

## Capabilities

### New Capabilities
- `gpu-accelerated-codec-transcoding`: Transcode input videos to H.265 or AV1 with NVIDIA GPU acceleration and user-selectable codec settings.
- `visually-lossless-compression-targeting`: Preserve near-lossless visual quality while minimizing output size and reporting achieved savings.

### Modified Capabilities
- None.

## Impact

- Affected systems: encoding/transcoding pipeline, hardware capability detection, and output validation/reporting.
- Potential dependencies: FFmpeg GPU-enabled build, NVIDIA driver/runtime availability, and codec support matrix for NVENC/NVDEC.
- API/CLI impact: new options for target codec, quality target, preset selection, and fallback mode.
- Operational impact: higher GPU utilization during transcode jobs, reduced storage/network usage for optimized outputs.
