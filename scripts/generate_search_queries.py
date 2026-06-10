#!/usr/bin/env python3
"""Generate search queries for one piano-sheet target."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path


def slugify(value: str, max_len: int = 80) -> str:
    value = urllib.parse.unquote(value)
    value = re.sub(r"[^\w._ -]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = value.strip("-._")
    return (value[:max_len].strip("-._") or "score")


def quote(value: str) -> str:
    value = value.strip()
    return f'"{value}"' if value else ""


def build_queries(title: str, artist: str = "", aliases: list[str] | None = None, channel: str = "bilibili") -> list[dict[str, str]]:
    aliases = aliases or []
    names = [title, *aliases]
    names = [name for i, name in enumerate(names) if name and name not in names[:i]]
    artist_part = f" {artist}" if artist else ""

    rows: list[dict[str, str]] = []
    bilibili_templates = [
        ("bilibili-site", '{q} {artist} 钢琴谱 五线谱 完整谱'),
        ("bilibili-attach", '{q} {artist} 附谱 谱在简介 谱在评论 置顶'),
        ("bilibili-files", '{q} {artist} pdf midi mscz musicxml 网盘 提取码'),
        ("bilibili-exclude-simple", '{q} {artist} 钢琴谱 -简谱 -瀑布流'),
        ("bilibili-cn-alias", '{q} {artist} 五线谱 钢琴 独奏 谱子'),
    ]
    multi_templates = [
        ("google-file", '{q} {artist} piano sheet pdf OR midi OR mscz -print-gakufu -piascore -at-elise'),
        ("google-arranger", '{q} marasy hecap mikan5357 midi sheet'),
        ("google-filetype", '{q} filetype:pdf OR filetype:mid OR filetype:mscz'),
        ("youtube", 'site:youtube.com/watch {q} piano sheet OR midi OR synthesia'),
        ("youtube-comments", '{q} "sheet in description" OR "midi" OR "download"'),
        ("bilibili", 'site:bilibili.com/video {q} 钢琴谱 OR 附谱 OR 五线谱'),
        ("bilibili-netdisk", '{q} b站 钢琴谱 网盘 OR 提取码 OR 评论区'),
        ("cloud-drive", '{q} site:drive.google.com OR site:pan.baidu.com OR site:wwp.lanzou'),
        ("midi-sites", '{q} site:midishow.com OR site:onlinesequencer.net'),
        ("score-sites", '{q} site:sheet.host OR site:musescore.com'),
    ]
    templates = bilibili_templates if channel == "bilibili" else multi_templates

    for name in names:
        for channel, template in templates:
            rows.append(
                {
                    "channel": channel,
                    "query": template.format(q=quote(name), artist=artist_part.strip()),
                    "purpose": _purpose(channel),
                }
            )
    return rows


def _purpose(channel: str) -> str:
    return {
        "bilibili-site": "Search inside Bilibili for complete piano score candidates.",
        "bilibili-attach": "Find Bilibili videos whose description/pinned comments may attach full scores.",
        "bilibili-files": "Find Bilibili-visible cloud-drive/file-code hints.",
        "bilibili-exclude-simple": "Bias away from simplified numbered notation and waterfall-only videos.",
        "bilibili-cn-alias": "Try Chinese score wording for full printable piano notation.",
        "google-file": "Find non-store file or user-upload pages while excluding common paid stores.",
        "google-arranger": "Follow known arranger/uploader names and repost lineage.",
        "google-filetype": "Try direct indexed PDF/MIDI/MSCZ files.",
        "youtube": "Find videos whose description may contain sheet or MIDI links.",
        "youtube-comments": "Find indexed text hinting at description/comment downloads.",
        "bilibili": "Find Bilibili videos that show score, synthesia, or mention attached sheet.",
        "bilibili-netdisk": "Find Chinese pages mentioning Bilibili plus cloud-drive extraction codes.",
        "cloud-drive": "Find public cloud-drive shares indexed by search engines.",
        "midi-sites": "Find MIDI uploads and trace their source videos/uploader names.",
        "score-sites": "Find public score-host uploads for manual rights review.",
    }.get(channel, "Search candidate sources.")


def write_markdown(path: Path, title: str, rows: list[dict[str, str]]) -> None:
    lines = [f"# Search Query Plan: {title}", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row['channel']}",
                "",
                f"```text\n{row['query']}\n```",
                "",
                f"- Purpose: {row['purpose']}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate search queries for piano-sheet hunting.")
    parser.add_argument("title")
    parser.add_argument("--artist", default="")
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--channel", choices=["bilibili", "multi"], default="bilibili", help="Search scope. Default is Bilibili-only.")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = build_queries(args.title, args.artist, args.alias, args.channel)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        out = Path(args.out) if args.out else Path("sheet-music") / slugify(args.title) / "QUERY_PLAN.md"
        write_markdown(out, args.title, rows)
        print(out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
