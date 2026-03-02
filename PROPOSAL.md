# Proposal: GPU-Accelerated Video Compressor (H.264 → H.265 / AV1)

## 1) Objective
Build a desktop-first CLI application that compresses source videos (commonly H.264) into:
- **H.265/HEVC** using NVIDIA NVENC
- **AV1** using NVIDIA NVENC (on supported RTX GPUs)

This tool must minimize file size while maintaining original quality. To do this, it must convert the H264 format to H265/AV1 format using GPU acceleration. The focus should be on compression and output quality rather than speed.

## 2) Problem Statement
Currently, a large number of videos are in H264 format. For the same quality, H265 or AV1 formats can reduce file size by around 30%. An intelligent transcoder should enable:
- Efficient video encoding without loss of quality
- File size minimization through optimization of encoding parameters
- Use of already implemented GPU accelerations


## 3) Proposed Solution
Create a command-line utility (with also a GUI version) that:
1. Detects NVIDIA GPU + codec capability
2. Analyzes multimedia streams and metadata
3. Searches for the optimal encoding profile to preserve the original video quality while reducing the file size as much as possible (can try settings on parts of the video and test similarity with the original video)
4. Executes FFmpeg/NVENC tasks with the optimal settings found previously
5. Generates logs, reports that can be exported as files, and summary metrics

### Core capabilities (MVP)
- Input formats: MP4, MKV, MOV (extensible)
- Output codecs:
  - HEVC (`hevc_nvenc`)
  - AV1 (`av1_nvenc`) when hardware supports it
- Audio handling:
  - Copy original audio when possible
  - Optional AAC/Opus transcode mode
- Subtitle/chapter/metadata pass-through (default: preserve)
- Batch mode for folders with recursive scan
- Dry-run mode (print planned FFmpeg commands)
- Overwrite protection + deterministic output naming

## 4) Technical Approach

### 4.1 Runtime & Packaging
- **Language**: Python 3.11+
- **Execution model**: FFmpeg process orchestration (subprocess)
- **CLI framework**: Typer (or argparse for minimal dependency)
- **Packaging**: uv

### 4.2 Dependency stack
- FFmpeg build with NVIDIA support (`--enable-nvenc`, CUDA/NPP support)
- NVIDIA driver with NVENC availability
- ffprobe for stream introspection

### 4.3 Pipeline
1. **Capability detection**
   - Validate the presence of `ffmpeg`/`ffprobe`.
   - Analyze `ffmpeg -encoders` for `hevc_nvenc` and `av1_nvenc`.
   - Determine supported pixel formats and bitrate control options.
   - Detect GPU acceleration capability
2. **Input analysis**
   - Read stream information: codec, resolution, fps, duration, bitrate, HDR indicators
   - Detect unsupported edge cases (10-bit, interlaced, VFR constraints)
3. **Profile analysis**
   - Search for optimal encoding parameters to minimize file size while maintaining original quality.
   - Test with video segments to validate parameters using similarity measurement
4. **Encoding execution**
   - Build FFmpeg command with robust escape and GPU acceleration
   - Optional two-pass strategy for quality-constrained targets (single-pass NVENC options first for MVP)
5. **Validation and reporting**
   - Verification of output readability and duration consistency
   - Verification of quality similarity between output and input
   - Comparison of input/output size, elapsed time, compression ratio
   - Issuance of a JSON report for automation

## 5) Encoding Profiles

No default encoding profile. The encoding profile must be calculated for each video in order to minimize file size while maintaining original quality.


> Final parameter tuning should be validated by measuring the similarity between parts of the original video and parts of the encoded video using parameter testing.

## 6) CLI UX (MVP)

```bash
videocompress file input.mp4 --codec hevc
videocompress file input.mp4 --codec av1
videocompress batch ./input --recursive --codec hevc --dry-run
videocompress batch ./input --codec auto --audio copy
```

### Key options
- `--codec {hevc,av1,auto}`
- `--container {mp4,mkv}`
- `--recursive`
- `--dry-run`
- `--report-json <path>`

## 7) Performance & Quality Targets
- Compression gain goal (indicative):
  - HEVC: 25–45% size reduction vs source H.264 at similar perceptual quality
  - AV1: 35–55% on favorable content/hardware
- Failure rate: <1% job failures on validated input set

## 8) Risks & Mitigations
- **GPU capability variance**: Some NVIDIA cards do not support AV1 encode.
  - Mitigation: automatic fallback from AV1 to HEVC with explicit warning.
- **FFmpeg build mismatch**: Missing NVENC in local build.
  - Mitigation: startup diagnostics with actionable install guidance.
- **Quality regressions on specific content**:
  - Mitigation: per-title CQ tuning, sample-based validation mode, conserving original file.
- **Container/metadata edge cases**:
  - Mitigation: preserve defaults, strict remux checks, safe fallback modes.

## 9) Delivery Plan

### Phase 1 (MVP, ~1 week)
- CLI scaffold + config
- Capability detection + ffprobe ingest
- HEVC/AV1 encode command generation
- Single file + batch processing
- Quality metric integration (VMAF/SSIM sampling)
- auto selection of best encoding parameters
- Summary and JSON report

### Phase 2 (~1 week)
- Smarter selection of encoding parameters
- Retry/fallback logic
- Better progress bars and logging levels
- Windows executable packaging

### Phase 3 (optional)
- Lightweight GUI
- Watch-folder automation


## 10) Acceptance Criteria
- Runs on Windows with NVIDIA GPU and FFmpeg NVENC build
- Encodes at least one H.264 source to HEVC and AV1 (or graceful AV1 fallback)
- Preserves audio/subtitles/metadata by default unless overridden
- Produces deterministic output with same quality of source and smaller size than source, with naming and machine-readable report
- Handles batch folder input with clear success/failure summary

## 11) Recommended First Implementation Scope
Start with **HEVC + AV1 single-file and batch CLI**, with strong diagnostics and dry-run. Defer GUI and advanced quality metrics until encode reliability is proven.

---
If approved, the next step is to scaffold the Python CLI project and implement capability detection + one working encode path end-to-end.