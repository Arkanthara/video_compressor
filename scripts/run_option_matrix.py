"""Run a coverage-style matrix of CLI options in dry-run mode.

Usage:
    python scripts/run_option_matrix.py --input test_videos/sample_mkv_01.mkv

Set --mode full to try a larger cartesian matrix.
Set --run parse to only validate argument parsing (no ffmpeg required).
"""

from __future__ import annotations

import argparse
import itertools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from videocompress import cli


def _ffmpeg_ready() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _run_command(cmd: list[str]) -> int:
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _parse_only(args: list[str]) -> int:
    parser = cli._build_parser()
    parsed = parser.parse_args(args)
    if parsed.command != "file":
        raise SystemExit("Only file command is supported in parse mode")
    cli._options_from_args(parsed, Path(parsed.input_path))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("./output"))
    parser.add_argument("--mode", choices=["coverage", "full"], default="coverage")
    parser.add_argument("--run", choices=["dry-run", "parse"], default="dry-run")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2

    if args.run == "dry-run" and not _ffmpeg_ready():
        print("ERROR: ffmpeg/ffprobe are required for dry-run mode.", file=sys.stderr)
        return 2

    base_args = [
        "file",
        str(args.input),
        "--output-dir",
        str(args.output_dir),
        "--dry-run",
        "--overwrite",
    ]

    cases: list[tuple[str, list[str]]] = []

    if args.mode == "coverage":
        for codec in ["auto", "hevc", "av1"]:
            cases.append((f"codec={codec}", ["--codec", codec]))
        for container in ["mkv", "mp4"]:
            cases.append((f"container={container}", ["--container", container]))
        for audio in ["copy", "aac", "opus"]:
            cases.append((f"audio={audio}", ["--audio", audio]))
        for fallback in ["fail-fast", "fallback-codec", "fallback-cpu"]:
            cases.append((f"fallback={fallback}", ["--fallback-mode", fallback]))
        for metric in ["vmaf", "ssim", "psnr"]:
            cases.append((f"metric={metric}", ["--quality-metric", metric]))
        for preset in ["p1", "p5", "p7", "fast", "medium", "slow"]:
            cases.append((f"preset={preset}", ["--preset", preset]))
        for quality in ["0", "22", "51"]:
            cases.append((f"quality={quality}", ["--quality", quality]))
        for rc_mode in ["auto", "vbr", "constqp", "cbr", "crf"]:
            cases.append((f"rc-mode={rc_mode}", ["--rc-mode", rc_mode]))
        cases.append(("no-auto-search", ["--no-auto-search-best"]))
        cases.append(("no-validate", ["--no-validate-quality"]))
        cases.append(("no-quality-mode", ["--no-quality-mode"]))
        cases.append(("lossless", ["--lossless"]))
        cases.append(("gpu-opt-on", ["--enable-gpu-optimization"]))
        cases.append(("gpu-opt-off", ["--disable-gpu-optimization"]))
        cases.append(("search-presets", ["--search-presets", "p7,p6"]))
    else:
        codecs = ["auto", "hevc", "av1"]
        containers = ["mkv", "mp4"]
        audio_modes = ["copy", "aac", "opus"]
        fallback_modes = ["fail-fast", "fallback-codec", "fallback-cpu"]
        metrics = ["vmaf", "ssim", "psnr"]

        for combo in itertools.product(
            codecs,
            containers,
            audio_modes,
            fallback_modes,
            metrics,
        ):
            codec, container, audio, fallback, metric = combo
            label = f"full:{codec}/{container}/{audio}/{fallback}/{metric}"
            cases.append(
                (
                    label,
                    [
                        "--codec",
                        codec,
                        "--container",
                        container,
                        "--audio",
                        audio,
                        "--fallback-mode",
                        fallback,
                        "--quality-metric",
                        metric,
                    ],
                )
            )

    failures = 0
    for label, extra in cases:
        args_list = base_args + extra
        if args.run == "parse":
            rc = _parse_only(args_list)
        else:
            cmd = [sys.executable, "-m", "videocompress"] + args_list
            rc = _run_command(cmd)
        if rc != 0:
            failures += 1
            print(f"FAILED: {label}")

    if failures:
        print(f"{failures} case(s) failed")
        return 1

    print(f"OK: {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
