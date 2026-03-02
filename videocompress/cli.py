"""Command-line interface for videocompress.

Provides three sub-commands:

- ``videocompress file <path>``  — compress a single video file
- ``videocompress batch <dir>``  — compress all videos in a directory
- ``videocompress gui``          — launch the graphical user interface

Output is JSON on stdout (for scripting) with errors on stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from videocompress.models import (
    AudioMode,
    Container,
    FallbackMode,
    JobError,
    JobOptions,
    TargetCodec,
)
from videocompress.reporting import write_report
from videocompress.transcode import run_job


def _launch_gui() -> int:
    """Import and start the GUI (lazy import avoids loading tkinter for CLI use)."""
    from videocompress.gui import launch

    launch()
    return 0


def _collect_inputs(base: Path, recursive: bool) -> list[Path]:
    """Discover video files under *base*, optionally recursing into sub-directories."""
    patterns = ("*.mp4", "*.mkv", "*.mov")
    files: list[Path] = []
    for pattern in patterns:
        files.extend(base.rglob(pattern) if recursive else base.glob(pattern))
    return sorted({path for path in files if path.is_file()})


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser with file/batch/gui sub-commands."""
    parser = argparse.ArgumentParser(prog="videocompress")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_flags(p: argparse.ArgumentParser, is_batch: bool = False) -> None:
        target = "input_path" if not is_batch else "input_dir"
        p.add_argument(target, type=Path)
        p.add_argument("--codec", choices=["hevc", "av1", "auto"], default="auto")
        p.add_argument(
            "--fallback-mode",
            choices=["fail-fast", "fallback-codec", "fallback-cpu"],
            default="fallback-codec",
        )
        p.add_argument("--container", choices=["mp4", "mkv"], default="mkv")
        p.add_argument("--preset", default="p5")
        p.add_argument("--quality", type=int, default=22)
        p.add_argument("--quality-mode", action="store_true", default=True)
        p.add_argument("--no-quality-mode", dest="quality_mode", action="store_false")
        p.add_argument("--quality-metric", choices=["ssim", "psnr", "vmaf"], default="vmaf")
        p.add_argument(
            "--quality-threshold",
            type=float,
            default=None,
            help=(
                "Minimum quality score to accept a candidate. "
                "Auto-set per metric if not specified: "
                "VMAF=95.0, SSIM=0.97, PSNR=40.0"
            ),
        )
        p.add_argument("--auto-search-best", action="store_true", default=True)
        p.add_argument("--no-auto-search-best", dest="auto_search_best", action="store_false")
        p.add_argument("--validate-quality", action="store_true", default=True)
        p.add_argument("--no-validate-quality", dest="validate_quality", action="store_false")
        p.add_argument("--audio", choices=["copy", "aac", "opus"], default="copy")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--overwrite", action="store_true")
        p.add_argument("--output-dir", type=Path, default=Path("./output"))
        p.add_argument("--report-json", type=Path)
        p.add_argument(
            "--enable-gpu-optimization",
            action="store_true",
            help="Opt-in feature flag for GPU optimization pipeline",
        )
        p.add_argument(
            "--lossless",
            action="store_true",
            default=False,
            help=(
                "Use mathematically lossless encoding "
                "(hevc_nvenc -tune lossless). Much larger files "
                "— use only when exact pixel fidelity is required."
            ),
        )
        p.add_argument(
            "--no-lossless",
            dest="lossless",
            action="store_false",
            help="Disable lossless mode — use quality-based encoding",
        )
        p.add_argument(
            "--search-presets",
            default="all",
            help=(
                "Comma-separated list of presets to search during auto-search. "
                "Default: 'all' (search all built-in presets). "
                "Example: 'p7,p6' to only search p7 and p6 presets."
            ),
        )

    file_cmd = sub.add_parser("file", help="Compress a single video file")
    add_common_flags(file_cmd, is_batch=False)

    batch_cmd = sub.add_parser("batch", help="Compress a folder of videos")
    add_common_flags(batch_cmd, is_batch=True)
    batch_cmd.add_argument("--recursive", action="store_true")

    sub.add_parser("gui", help="Launch the graphical user interface")

    return parser


def _options_from_args(args: argparse.Namespace, input_path: Path) -> JobOptions:
    """Convert parsed CLI arguments into a :class:`JobOptions` instance."""
    return JobOptions(
        input_path=input_path,
        output_dir=args.output_dir,
        output_container=Container(args.container),
        codec=TargetCodec(args.codec),
        fallback_mode=FallbackMode(args.fallback_mode),
        preset=args.preset,
        quality=args.quality,
        quality_mode=args.quality_mode,
        validate_quality=args.validate_quality,
        quality_metric=args.quality_metric,
        quality_threshold=(
            args.quality_threshold
            if args.quality_threshold is not None
            else {"ssim": 0.97, "psnr": 40.0, "vmaf": 95.0}.get(args.quality_metric, 0.97)
        ),
        auto_search_best=args.auto_search_best,
        enable_gpu_optimization=args.enable_gpu_optimization,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        audio_mode=AudioMode(args.audio),
        report_json=args.report_json,
        lossless=args.lossless,
        search_presets=(
            []
            if args.search_presets.strip().lower() == "all"
            else [p.strip() for p in args.search_presets.split(",") if p.strip()]
        ),
    )


def _run_single(args: argparse.Namespace, input_path: Path) -> int:
    """Run a single-file compression job and print the JSON result."""
    opts = _options_from_args(args, input_path)
    outcome = run_job(opts)

    if args.report_json:
        write_report(args.report_json, outcome)

    print(
        json.dumps(
            {
                "input": str(outcome.input_path),
                "output": str(outcome.output_path),
                "effective_codec": outcome.selected_path.effective_codec.value,
                "encoder": outcome.selected_path.encoder,
                "lossless": opts.lossless,
                "used_gpu": outcome.selected_path.used_gpu,
                "fallback_used": outcome.selected_path.fallback_used,
                "input_mb": round(outcome.input_size / 1_048_576, 2),
                "output_mb": round(outcome.output_size / 1_048_576, 2),
                "compression_ratio": round(outcome.compression_ratio, 4),
                "diagnostics": outcome.diagnostics,
                "command": outcome.command if args.dry_run else None,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate sub-command."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "gui":
            return _launch_gui()

        if args.command == "file":
            return _run_single(args, args.input_path)

        if args.command == "batch":
            if not args.input_dir.exists() or not args.input_dir.is_dir():
                raise JobError("invalid-input-dir", f"Input directory not found: {args.input_dir}")

            files = _collect_inputs(args.input_dir, args.recursive)
            if not files:
                raise JobError("no-input-files", "No input files found matching .mp4/.mkv/.mov")

            failed = 0
            for file_path in files:
                try:
                    _run_single(args, file_path)
                except JobError as err:
                    failed += 1
                    print(f"ERROR [{err.taxonomy}] {file_path}: {err}", file=sys.stderr)

            return 1 if failed else 0

        raise JobError("invalid-command", "Unknown command")
    except JobError as err:
        print(f"ERROR [{err.taxonomy}] {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
