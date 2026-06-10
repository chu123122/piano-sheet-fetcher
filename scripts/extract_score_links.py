#!/usr/bin/env python3
"""Extract score-like links and access codes from descriptions/comments."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


URL_RE = re.compile(r"https?://[^\s<>'\"，。；；、）)\]}]+", re.I)
CODE_RE = re.compile(
    r"(?:提取码|提取碼|访问码|訪問碼|密码|密碼|pass(?:word)?|code)[:：\s]*([A-Za-z0-9]{3,8})",
    re.I,
)
SHEET_EXT_RE = re.compile(r"\.(pdf|mscz|mxl|musicxml|mid|midi|zip|rar|7z|jpg|jpeg|png)(?:$|[?#])", re.I)

CLOUD_HOSTS = {
    "pan.baidu.com": "baidu-pan",
    "www.aliyundrive.com": "aliyun-drive",
    "www.alipan.com": "aliyun-drive",
    "drive.google.com": "google-drive",
    "dropbox.com": "dropbox",
    "www.dropbox.com": "dropbox",
    "1drv.ms": "onedrive",
    "onedrive.live.com": "onedrive",
    "mega.nz": "mega",
    "www.lanzou": "lanzou",
    "lanzou": "lanzou",
    "www.123pan.com": "123pan",
    "www.123684.com": "123pan",
    "pan.quark.cn": "quark",
}


@dataclass
class FoundLink:
    url: str
    kind: str
    score: int
    access_code: str | None
    context: str


def classify(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if SHEET_EXT_RE.search(parsed.path):
        return "direct-score-file", 100
    for key, value in CLOUD_HOSTS.items():
        if key in host:
            return value, 80
    if "musescore.com" in host:
        return "musescore-page", 45
    if "youtube.com" in host or "youtu.be" in host or "bilibili.com" in host:
        return "video-page", 35
    return "page", 20


def extract(text: str, window: int = 120) -> list[FoundLink]:
    text = html.unescape(text)
    results: list[FoundLink] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url in seen:
            continue
        seen.add(url)
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        context = re.sub(r"\s+", " ", text[start:end]).strip()
        kind, score = classify(url)
        code_match = CODE_RE.search(context) if kind not in {"direct-score-file", "video-page", "page"} else None
        if code_match:
            score += 5
        results.append(
            FoundLink(
                url=url,
                kind=kind,
                score=score,
                access_code=code_match.group(1) if code_match else None,
                context=context,
            )
        )
    return sorted(results, key=lambda item: item.score, reverse=True)


def write_markdown(path: Path, links: list[FoundLink]) -> None:
    lines = ["# Extracted Score Links", ""]
    if not links:
        lines.extend(["No links found.", ""])
    for item in links:
        lines.extend(
            [
                f"## {item.kind} ({item.score})",
                "",
                f"- URL: {item.url}",
                f"- Access code: {item.access_code or ''}",
                f"- Context: {item.context}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract score-related URLs from text/HTML/JSON.")
    parser.add_argument("inputs", nargs="*", help="Input files. Reads stdin when omitted.")
    parser.add_argument("--out", help="Write Markdown report to this path")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args(argv)

    chunks: list[str] = []
    if args.inputs:
        for value in args.inputs:
            chunks.append(Path(value).read_text(encoding="utf-8", errors="replace"))
    else:
        chunks.append(sys.stdin.read())

    links = extract("\n".join(chunks))
    if args.out:
        write_markdown(Path(args.out), links)
    elif args.json:
        print(json.dumps([asdict(item) for item in links], ensure_ascii=False, indent=2))
    else:
        for item in links:
            code = f" code={item.access_code}" if item.access_code else ""
            print(f"[{item.score}] {item.kind}{code} {item.url}")
    return 0 if links else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
