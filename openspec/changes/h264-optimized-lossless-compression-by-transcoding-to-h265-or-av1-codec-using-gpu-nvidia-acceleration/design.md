## Context

The project currently handles video compression but does not explicitly optimize legacy or high-bitrate H.264 sources by re-encoding into newer codecs under GPU acceleration. The proposed change introduces an optimization path that chooses H.265 or AV1 output, runs NVIDIA-accelerated encoding where available, and preserves visually lossless quality while reducing size.

Constraints include:
- Hardware variability across user systems (GPU model, NVENC generation, driver versions).
- Codec support differences (e.g., AV1 encode support depends on newer NVIDIA GPUs).
- Need for deterministic behavior when acceleration is unavailable or partially supported.
- Maintaining quality confidence through objective metrics and output reporting.

Primary stakeholders are users operating storage-sensitive workflows and maintainers who need reliable, diagnosable transcoding behavior.

## Goals / Non-Goals

**Goals:**
- Add a GPU-first transcode pipeline that converts H.264/MKV and other low-compression codec inputs to H.265 or AV1 where supported.
- Provide automatic detection of best encoding parameters for visually lossless output.
- Minimize the lossless output size.
- Use existent tools for encoding such as ffmpeg
- Favorise output compression quality over speed
- Detect codec/hardware capability at runtime and apply deterministic fallback behavior.
- Produce result telemetry (compression ratio and quality-check summary) for each completed job.
- quality validation integration between output and input

**Non-Goals:**
- Implement CPU-only performance optimizations beyond a minimal fallback path.
- Build a full perceptual tuning system for all content genres.
- Introduce distributed transcoding or multi-node scheduling.
- Guarantee mathematically lossless output; focus remains visually lossless compression.

## Decisions

1. Capability-Probe Before Job Execution
- Decision: Run a startup/job-time probe to detect available NVIDIA encoders and supported codec profiles.
- Rationale: Avoid mid-job failures and provide actionable user feedback early.
- Alternatives considered:
  - Blindly invoke requested encoder and fail on error (simpler, poorer UX).
  - Static capability map by GPU model (fragile across drivers/FFmpeg builds).

2. Explicit Codec Strategy with Ordered Preference
- Decision: Respect user-selected target (`h265` or `av1`) and optionally allow `auto` mode that prefers AV1 when supported, otherwise H.265.
- Rationale: AV1 can deliver better efficiency but support is not universal.
- Alternatives considered:
  - Always default to H.265 (most compatible, misses AV1 savings).
  - Always default to AV1 (breaks on unsupported hardware).

3. GPU-Accelerated FFmpeg Integration with Structured Presets
- Decision: Use FFmpeg GPU encoders (`hevc_nvenc`, `av1_nvenc`) with a bounded preset/quality configuration surface exposed by the app.
- Rationale: Keeps UX predictable and avoids invalid free-form option combinations.
- Alternatives considered:
  - Expose raw FFmpeg flags directly (flexible but error-prone and hard to support).

4. Quality Guardrail via Post-Encode Validation
- Decision: Run optional objective checks (e.g., VMAF/SSIM/PSNR where available) and fail/warn when below threshold policy.
- Rationale: Supports “visually lossless” claim with measurable criteria.
- Alternatives considered:
  - No objective validation (faster, less trust in output quality).
  - Mandatory validation for every run (higher confidence, slower throughput).

5. Deterministic Fallback and Error Taxonomy
- Decision: Introduce explicit fallback modes: `fail-fast`, `fallback-codec`, `fallback-cpu`.
- Rationale: Different operational environments need different reliability/quality priorities.
- Alternatives considered:
  - Implicit fallback only (hard to reason about and audit).

## Risks / Trade-offs

- [AV1 support gaps on older NVIDIA GPUs] → Mitigation: capability probe + automatic fallback to H.265 when policy allows.
- [Quality metric tools not present in some FFmpeg builds] → Mitigation: mark quality validation as optional with clear “not available” reporting.
- [GPU memory pressure for high-resolution content] → Mitigation: tune default presets, expose max concurrency controls, and emit actionable OOM diagnostics.
- [Faster presets may reduce quality or savings] → Mitigation: default to better quality presets and document trade-offs in user-facing configuration help.
- [Behavior drift across FFmpeg/NVIDIA driver versions] → Mitigation: include encoder capability snapshot in job logs and diagnostic output.

## Migration Plan

1. Add capability-detection module and wire it into job planning.
2. Add codec-selection + fallback policy model in configuration/CLI.
3. Implement transcode executor paths for H.265/AV1 NVENC.
4. Add post-encode reporting (size reduction, codec used, preset, fallback events, quality metrics).
5. Add obligatory quality validation integration and threshold policy.
6. Roll out behind a feature flag or opt-in mode initially.
7. Validate on representative NVIDIA hardware tiers before making defaults broader.

Rollback strategy:
- Disable the feature flag/opt-in path and route jobs back to existing compression behavior.
- Preserve logs/artifacts for failed optimization runs to aid root-cause analysis.

## Open Questions

- Should AV1 be default in `auto` mode only for specific minimum GPU generations? -> automatically switch to H265 if AV1 not available or too old GPU
- What default quality threshold should define “visually lossless” for this project’s target content? -> the default quality must be lossless compression
- Should quality validation run synchronously in the main job or asynchronously as a post-step? -> quality validation must run to set up best encoding parameters. Then it must run as a post-step to validate the result.
- Do we need per-resolution preset defaults (e.g., 1080p vs 4K) in v1? -> no need of preset because best parameters are chosen automatically from the input.
