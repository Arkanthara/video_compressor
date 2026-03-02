"""Synchronize package version declarations.

This script updates both:
- ``pyproject.toml`` ([project].version)
- ``videocompress/__init__.py`` (__version__)

It is intended for CI workflows that derive release version from Git tags.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def _replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match in {path} for pattern: {pattern}")
    path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Version value without leading 'v'.")
    args = parser.parse_args()

    version = args.version.strip()
    if not version:
        raise ValueError("Version cannot be empty.")

    repo_root = Path(__file__).resolve().parents[1]
    pyproject = repo_root / "pyproject.toml"
    init_file = repo_root / "videocompress" / "__init__.py"

    _replace_once(
        pyproject,
        r'^version\s*=\s*"[^"]+"\s*$',
        f'version = "{version}"',
    )
    _replace_once(
        init_file,
        r'^__version__\s*=\s*"[^"]+"\s*$',
        f'__version__ = "{version}"',
    )

    print(f"Synchronized version to {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
