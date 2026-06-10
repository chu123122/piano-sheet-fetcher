#!/usr/bin/env python3
"""Organize user-downloaded score files into the skill output layout."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path


SCORE_EXTENSIONS = {
    ".pdf",
    ".mscz",
    ".mxl",
    ".musicxml",
    ".mid",
    ".midi",
    ".zip",
    ".rar",
    ".7z",
    ".jpg",
    ".jpeg",
    ".png",
}


def slugify(value: str, max_len: int = 80) -> str:
    value = urllib.parse.unquote(value)
    value = re.sub(r"[^\w._ -]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = value.strip("-._")
    return (value[:max_len].strip("-._") or "score")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def organize_file(source_path: Path, out_dir: Path, args: argparse.Namespace) -> Path:
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.lower() not in SCORE_EXTENSIONS:
        raise ValueError(f"unsupported score extension: {source_path}")

    prefix = slugify(args.name_prefix or args.song)
    base = slugify(source_path.stem)
    target = unique_path(out_dir / f"{prefix}-{base}{source_path.suffix.lower()}")
    shutil.copy2(source_path, target)

    metadata = {
        "song": args.song,
        "artist": args.artist,
        "arranger_or_uploader": args.arranger,
        "source_url": args.source_url,
        "source_title": args.source_title,
        "acquired_by": args.acquired_by,
        "rights_note": args.rights_note,
        "original_local_path": str(source_path),
        "organized_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sha256": sha256(target),
        "bytes": target.stat().st_size,
        "policy_note": "This script organizes files already obtained through a normal login, purchase, creator-provided download, or user-authorized source. It does not bypass access controls.",
    }
    target.with_suffix(target.suffix + ".source.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Organize local score files with source metadata.")
    parser.add_argument("files", nargs="+", help="Already-downloaded score files")
    parser.add_argument("--song", required=True, help="Song title, e.g. ただ君に晴れ")
    parser.add_argument("--artist", default="", help="Artist/composer name")
    parser.add_argument("--out", default="sheet-music", help="Output root")
    parser.add_argument("--source-url", default="", help="Original source or purchase/download page")
    parser.add_argument("--source-title", default="", help="Source page title or candidate title")
    parser.add_argument("--arranger", default="", help="Arranger/uploader when known")
    parser.add_argument(
        "--acquired-by",
        default="user-provided-local-file",
        help="How the user obtained the file, e.g. sheethost-login, official-purchase, creator-download",
    )
    parser.add_argument(
        "--rights-note",
        default="User provided the local file and is responsible for rights/terms compliance.",
        help="Short note about permission, purchase, or intended personal use",
    )
    parser.add_argument("--name-prefix", default="", help="Filename prefix override")
    args = parser.parse_args()

    out_dir = Path(args.out) / slugify(f"{args.song}-{args.artist}" if args.artist else args.song)
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for file_name in args.files:
        outputs.append(organize_file(Path(file_name), out_dir, args))
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
