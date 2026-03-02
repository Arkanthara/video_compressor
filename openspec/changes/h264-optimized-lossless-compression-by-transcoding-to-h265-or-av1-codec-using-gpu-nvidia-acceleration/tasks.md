## 1. Capability Detection and Configuration

- [x] 1.1 Implement NVIDIA GPU encoder capability probing for H.265 NVENC and AV1 NVENC at job-planning time
- [x] 1.2 Add configuration/CLI options for target codec (`h265`, `av1`, `auto`) and fallback mode (`fail-fast`, `fallback-codec`, `fallback-cpu`)
- [x] 1.3 Add automatic search of best parameters for lossless encoding with validation using quality metrics and part of input video
- [x] 1.4 Add validation for preset and quality inputs with clear accepted-value error messages

## 2. Transcode Execution Paths

- [x] 2.1 Based on ffmpeg, implement GPU encode path mapping to `hevc_nvenc` and `av1_nvenc` based on selected codec strategy
- [x] 2.2 Implement deterministic fallback behavior for unavailable codec paths according to configured fallback mode
- [x] 2.3 Implement CPU fallback execution path and explicit logging of GPU bypass events

## 3. Quality and Outcome Validation

- [x] 3.1 Implement test of different encoding parameters to find optimal parameters for lossless compression with file at minimum size using quality metrics for validation
- [x] 3.2 Implement visually lossless quality mode defaults per codec
- [x] 3.3 Integrate optional post-encode objective metric checks with configurable thresholds
- [x] 3.4 Handle missing metric tooling by marking validation unavailable without crashing the pipeline

## 4. Reporting, Diagnostics, and Rollout

- [x] 4.1 Persist per-job optimization results (input size, output size, compression ratio, codec path, fallback reason, quality metrics and similarity)
- [x] 4.2 Add diagnostics capturing detected encoder capabilities and failure taxonomy for troubleshooting
- [x] 4.3 Gate rollout behind feature flag/opt-in mode and document rollback procedure to legacy compression behavior
