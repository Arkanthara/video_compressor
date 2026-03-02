# videocompress

**GPU-accelerated visually-lossless video compressor** — transcode H.264 videos
to H.265 (HEVC) or AV1 using NVIDIA NVENC hardware acceleration, with automatic
quality-aware parameter optimization.

<!-- Uncomment and update once the repo URL is set:
[![CI](https://github.com/Arkanthara/video_compressor/actions/workflows/ci.yml/badge.svg)](https://github.com/Arkanthara/video_compressor/actions/workflows/ci.yml)
[![Release](https://github.com/Arkanthara/video_compressor/actions/workflows/release.yml/badge.svg)](https://github.com/Arkanthara/video_compressor/actions/workflows/release.yml)
-->

---

## Features

| Feature | Description |
|---------|-------------|
| **GPU-accelerated encoding** | NVIDIA NVENC for H.265 and AV1 — orders of magnitude faster than CPU |
| **Zero-copy pipeline** | NVDEC decode → CUDA frames → NVENC encode (when h264_cuvid available) |
| **Automatic parameter search** | Tests multiple presets & quality levels on sample segments |
| **Quality gating** | VMAF, SSIM, or PSNR with per-frame P10 floor checking |
| **Batch processing** | Compress entire folders with optional recursive scan |
| **Modern GUI** | Dark-themed interface with real-time progress and console output |
| **CLI** | Full-featured command-line interface for scripting and automation |
| **Lossless mode** | Mathematically lossless encoding (hevc_nvenc `-tune lossless`) |
| **Smart fallbacks** | Automatic codec or CPU fallback when GPU encoder is unavailable |
| **Detailed reports** | JSON export with compression metrics, search results, diagnostics |

---

## Requirements

### System

| Requirement | Details |
|-------------|---------|
| **Python** | 3.11 or newer |
| **FFmpeg** | Built with NVIDIA NVENC support (`--enable-nvenc`) |
| **FFprobe** | Typically bundled with FFmpeg |
| **NVIDIA GPU** | NVENC-capable (GTX 600+ / Quadro K-series or newer) |
| **NVIDIA drivers** | Latest recommended for best NVENC feature support |

### Optional (quality metrics)

- **VMAF** — FFmpeg built with `libvmaf` (recommended for best quality gating)
- **SSIM / PSNR** — included in most FFmpeg builds (used as fallback)

---

## Installation

### From source (recommended for development)

```bash
# Clone the repository
git clone https://github.com/Arkanthara/video_compressor.git
cd videocompress

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

### From release binary

Download the latest archive from the
[Releases](https://github.com/Arkanthara/video_compressor/releases) page:

| Platform | Archive |
|----------|---------|
| Windows x64 | `videocompress-windows-x64.zip` |
| Linux x64 | `videocompress-linux-x64.tar.gz` |

Extract and run `videocompress` (or `videocompress.exe` on Windows).

> **Note:** FFmpeg with NVIDIA NVENC support must still be installed and
> available in your system PATH.

---

## Usage

### GUI mode

```bash
videocompress gui
# or the direct entry-point:
videocompress-gui
```

The GUI provides:
- File and folder pickers for input selection
- Auto Encoding tab (recommended) with quality metric and threshold settings
- Manual Encoding tab for direct preset/quality control
- Real-time progress bar and scrolling console output
- Start / Stop controls

### CLI mode

**Single file:**

```bash
# Basic — auto-detect codec, default quality gating
videocompress file input.mp4

# Specify codec and quality metric
videocompress file input.mp4 --codec hevc --quality-metric vmaf --quality-threshold 95

# AV1 encoding with GPU
videocompress file input.mp4 --codec av1 --enable-gpu-optimization

# Lossless mode (exact pixel fidelity)
videocompress file input.mp4 --lossless

# Dry run — show the FFmpeg command without encoding
videocompress file input.mp4 --dry-run
```

**Batch processing:**

```bash
# Compress all videos in a folder
videocompress batch ./videos --codec auto --output-dir ./compressed

# Recursive scan with JSON report
videocompress batch ./videos --recursive --report-json report.json

# Manual preset control (skip auto-search)
videocompress batch ./videos --no-auto-search-best --preset p7 --quality 28
```

### Key CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `--codec` | `auto` | Target codec: `hevc`, `av1`, or `auto` |
| `--container` | `mkv` | Output format: `mkv` or `mp4` |
| `--audio` | `copy` | Audio handling: `copy`, `aac`, or `opus` |
| `--preset` | `p5` | NVENC preset (`p1`–`p7`) or CPU preset (`fast`, `medium`, `slow`) |
| `--quality` | `22` | Quality level 0–51 (lower = higher quality) |
| `--quality-metric` | `vmaf` | Quality gate metric: `vmaf`, `ssim`, or `psnr` |
| `--quality-threshold` | auto | Minimum score to pass (VMAF=95, SSIM=0.97, PSNR=40) |
| `--auto-search-best` | `true` | Enable automatic parameter search |
| `--search-presets` | `all` | Comma-separated preset filter (e.g. `p7,p6`) |
| `--validate-quality` | `true` | Post-encode full quality validation |
| `--enable-gpu-optimization` | off | Enable NVIDIA GPU acceleration |
| `--lossless` | off | Mathematically lossless encoding |
| `--fallback-mode` | `fallback-codec` | `fail-fast`, `fallback-codec`, or `fallback-cpu` |
| `--dry-run` | off | Print FFmpeg command without encoding |
| `--overwrite` | off | Overwrite existing output files |
| `--output-dir` | `./output` | Output directory |
| `--report-json` | none | Save JSON report to this path |
| `--recursive` | off | Include sub-folders (batch mode) |

---

## How the parameter search works

When **auto-search** is enabled (the default), videocompress finds the
smallest possible file that still passes quality gating:

1. **Segment extraction** — 1–5 representative segments (20 s each) are
   extracted from the input using frame-accurate seek + lossless re-encode.
2. **Candidate grid** — a comprehensive set of encoding configurations is
   generated covering multiple presets, CQ/CRF levels, rate-control modes,
   and bit depths.
3. **Encode & measure** — each candidate is tested against every segment;
   quality is measured with per-frame statistics (average + P10 percentile).
4. **Early abort** — candidates that fail on any segment are immediately
   rejected, dramatically speeding up the search.
5. **Winner selection** — among all passing candidates, the one with the
   **smallest total encoded size** is chosen.

The result is a compression configuration that minimises file size while
guaranteeing visual quality above the configured threshold.

---

## Building executables

The project uses **PyInstaller** in `--onedir` mode for fast-launching
native executables. The `--onedir` layout avoids the slow temp-extraction
startup penalty of `--onefile` mode.

### Prerequisites

```bash
# Install dev dependencies (includes PyInstaller)
uv sync --group dev

# Or manually
pip install pyinstaller
```

### Build commands

**Windows:**

```powershell
uv run pyinstaller `
    --noconfirm `
    --onedir `
    --windowed `
    --name videocompress `
    --collect-all customtkinter `
    --clean `
    videocompress/__main__.py
```

**Linux:**

```bash
uv run pyinstaller \
    --noconfirm \
    --onedir \
    --name videocompress \
    --collect-all customtkinter \
    --clean \
    videocompress/__main__.py
```

The executable and all required files are placed in `dist/videocompress/`.

> **Why `--onedir` and not `--onefile`?**
>
> `--onefile` bundles everything into a single `.exe` that must extract to a
> temporary directory on every launch. This adds 5–15 seconds of startup time.
> `--onedir` keeps all files ready-to-run — startup is near-instant.

### Windows code signing

To avoid the **"Windows protected your PC"** SmartScreen warning when users
download and run the executable, you need to sign it with a code-signing
certificate.

#### Option 1: Local signing

```powershell
signtool sign `
    /f certificate.pfx `
    /p YOUR_PASSWORD `
    /fd sha256 `
    /tr http://timestamp.digicert.com `
    /td sha256 `
    dist\videocompress\videocompress.exe
```

#### Option 2: Automated signing in CI

The release workflow (`.github/workflows/release.yml`) includes an automatic
signing step. To enable it:

1. Obtain a code-signing certificate from a Certificate Authority
   (DigiCert, Sectigo, SSL.com, etc.)
2. **For immediate SmartScreen trust**, use an **EV (Extended Validation)**
   certificate. Standard OV certificates build reputation over time.
3. Base64-encode your PFX file:
   ```bash
   base64 -w 0 certificate.pfx > cert_base64.txt
   ```
4. Add the following GitHub repository secrets:
   - `WINDOWS_CERTIFICATE` — the Base64-encoded PFX content
   - `WINDOWS_CERTIFICATE_PASSWORD` — the PFX password

The CI will automatically sign the Windows executable during the release build.

---

## Development

### Setup

```bash
git clone https://github.com/Arkanthara/video_compressor.git
cd videocompress
uv sync --all-groups
```

### Lint & format

```bash
# Check for lint issues
uv run ruff check videocompress/

# Auto-fix lint issues
uv run ruff check videocompress/ --fix

# Check formatting
uv run ruff format --check videocompress/

# Apply formatting
uv run ruff format videocompress/
```

### Git-Flow workflow

This project uses the **Git-Flow** branching model:

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready code, tagged releases |
| `develop` | Integration branch for features |
| `feature/*` | New features (branch from `develop`) |
| `release/*` | Release preparation (branch from `develop`) |
| `hotfix/*` | Urgent production fixes (branch from `main`) |

#### Creating a feature

```bash
git checkout develop
git checkout -b feature/my-feature
# ... hack hack hack ...
git push origin feature/my-feature
# Open a PR targeting develop
```

#### Creating a release

```bash
# 1. Create release branch
git checkout develop
git checkout -b release/v0.2.0

# 2. Bump version in pyproject.toml and videocompress/__init__.py
# 3. Commit, push, open PR to main

# 4. After merge to main, tag the release:
git checkout main
git pull
git tag -a v0.2.0 -m "Release v0.2.0: summary of changes"
git push origin v0.2.0

# 5. Merge main back into develop:
git checkout develop
git merge main
git push origin develop
```

Pushing the tag triggers the CI to automatically:
- Build Windows and Linux executables
- Sign the Windows binary (if certificate secrets are configured)
- Create a GitHub Release with the platform archives
- Generate release notes from commit history

#### Hotfix

```bash
git checkout main
git checkout -b hotfix/v0.2.1
# Fix the issue, bump patch version
git push origin hotfix/v0.2.1
# Merge to both main and develop, tag as v0.2.1
```

---

## Architecture

```
videocompress/
├── __init__.py          # Package metadata and version
├── __main__.py          # Entry point (python -m videocompress)
├── cli.py               # Command-line interface (file, batch, gui)
├── gui.py               # Graphical interface (customtkinter)
├── models.py            # Dataclasses, enums, error types
├── capabilities.py      # GPU / FFmpeg capability detection
├── ffprobe_info.py      # Input video stream inspection
├── quality.py           # Quality metrics & parameter optimization
├── transcode.py         # FFmpeg encoding pipeline & job runner
└── reporting.py         # JSON report generation
```

### Data flow

```
User input (CLI/GUI)
    │
    ▼
JobOptions          ← models.py
    │
    ├─► probe_capabilities()   ← capabilities.py
    ├─► inspect_input()        ← ffprobe_info.py
    ├─► optimize_encoding_params()  ← quality.py  (auto-search mode)
    │
    ▼
run_job()           ← transcode.py
    │
    ├─► _select_codec_path()   (GPU/fallback resolution)
    ├─► _build_command()       (FFmpeg command construction)
    ├─► _run_ffmpeg()          (subprocess with progress parsing)
    │
    ▼
JobOutcome          ← models.py
    │
    ├─► write_report()         ← reporting.py  (optional JSON export)
    ▼
Results displayed in CLI/GUI
```

---

## License

MIT License — see [LICENSE](LICENSE) for details.
