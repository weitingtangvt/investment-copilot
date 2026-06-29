#!/usr/bin/env python
"""Check sanitized repository text files for UTF-8 and obvious mojibake."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CHECKED_EXTENSIONS = {
    ".bat",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}

SKIPPED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "filings_cache",
    "node_modules",
}

MOJIBAKE_MARKERS = tuple(
    chr(codepoint)
    for codepoint in (
        0xFFFD,
        0x9225,
        0x9239,
        0x6D93,
        0x9354,
        0x942D,
        0x5A34,
        0x93C2,
        0x93C4,
    )
)

MOJIBAKE_FRAGMENTS = (
    "\u00e4\u00b8",
    "\u00e6\u0096",
    "\u00e5\u0086",
    "\u00e5\u00ae",
    "\u00e8\u00af",
    "\u00e2\u20ac\u2122",
    "\u00e2\u20ac\u0153",
    "\u00e2\u20ac\u009d",
)


def is_checked_text_file(path: Path) -> bool:
    return path.suffix.lower() in CHECKED_EXTENSIONS


def iter_default_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIPPED_DIRS for part in path.parts):
            continue
        if path.is_file() and is_checked_text_file(path):
            yield path


def check_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: invalid utf-8 at byte {exc.start}"]

    problems = []
    for marker in MOJIBAKE_MARKERS:
        if marker in text:
            problems.append(f"{path}: mojibake marker {marker!r}")
    for fragment in MOJIBAKE_FRAGMENTS:
        if fragment in text:
            problems.append(f"{path}: mojibake fragment {fragment!r}")
    return problems


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fail if checked text files are not UTF-8 or contain mojibake.")
    parser.add_argument("paths", nargs="*", type=Path)
    return parser.parse_args(argv)


def collect_files(paths: list[Path], root: Path) -> list[Path]:
    if not paths:
        return sorted(iter_default_files(root))
    files = []
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        if resolved.is_dir():
            files.extend(iter_default_files(resolved))
        elif resolved.is_file() and is_checked_text_file(resolved):
            files.append(resolved)
    return sorted(set(files))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv or sys.argv[1:])
    root = Path(__file__).resolve().parents[2]
    problems = []
    for path in collect_files(args.paths, root):
        problems.extend(check_file(path))
    if problems:
        for problem in problems:
            print(problem)
        return 1
    print("Encoding check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
