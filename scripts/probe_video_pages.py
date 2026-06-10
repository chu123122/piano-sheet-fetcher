#!/usr/bin/env python3
"""Probe public video pages for score-related metadata without logging in."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


KEYWORDS = [
    "pan.baidu",
    "提取码",
    "网盘",
    "drive.google",
    "dropbox",
    "midi",
    "pdf",
    "mscz",
    "楽譜",
    "sheet",
    "score",
    "booth",
    "mikan5357",
    "hecap",
    "marasy",
    "钢琴谱",
    "简谱",
    "瀑布流",
    "乐谱",
    "附谱",
    "pianotutorial",
    "waterfall",
    "pianocover",
]

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class Probe:
    url: str
    status: int | None
    title: str
    author: str
    description: str
    links: list[str]
    keyword_hits: list[str]
    contexts: list[str]
    error: str = ""


def request(url: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
            "Referer": "https://www.bilibili.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def clean(text: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = "".join(ch if ch.isprintable() or ch.isspace() else " " for ch in text)
    return re.sub(r"\s+", " ", text).strip()


def extract_meta(raw: str, name: str) -> str:
    patterns = [
        rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\']([^"\']*)',
        rf'<meta\s+property=["\']og:{re.escape(name)}["\']\s+content=["\']([^"\']*)',
        rf'<meta\s+content=["\']([^"\']*)["\']\s+(?:name|property)=["\'](?:og:)?{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            return html.unescape(match.group(1))
    return ""


def extract_title(raw: str) -> str:
    return extract_meta(raw, "title") or html.unescape((re.search(r"<title[^>]*>([^<]*)", raw, re.I) or ["", ""])[1])


def decode_js_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return html.unescape(value)


def extract_jsonld(raw: str) -> dict[str, str]:
    for match in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>', raw, re.I):
        try:
            data = json.loads(html.unescape(match.group(1)).strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return {
                "title": str(data.get("name") or data.get("headline") or ""),
                "description": str(data.get("description") or ""),
                "author": _json_author(data.get("author")),
            }
    return {}


def _json_author(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("alternateName") or "")
    if isinstance(value, list) and value:
        return _json_author(value[0])
    if isinstance(value, str):
        return value
    return ""


def extract_bilibili_state(raw: str) -> dict[str, str]:
    # Bilibili often exposes escaped JSON fragments in __INITIAL_STATE__.
    fields: dict[str, str] = {}
    patterns = {
        "title": [r'"title"\s*:\s*"((?:\\.|[^"\\])*)"', r'"share_subtitle"\s*:\s*"((?:\\.|[^"\\])*)"'],
        "description": [r'"desc"\s*:\s*"((?:\\.|[^"\\])*)"', r'"description"\s*:\s*"((?:\\.|[^"\\])*)"'],
        "author": [r'"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*"sex"', r'"owner"\s*:\s*\{[^}]*"name"\s*:\s*"((?:\\.|[^"\\])*)"'],
    }
    for key, pats in patterns.items():
        for pattern in pats:
            match = re.search(pattern, raw)
            if match:
                value = decode_js_string(match.group(1)).strip()
                if value and len(value) < 1000:
                    fields[key] = value
                    break
    tags = re.findall(r'"tag_name"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if tags:
        fields["tags"] = ", ".join(decode_js_string(tag) for tag in tags[:20])
    return fields


def extract_links(raw: str) -> list[str]:
    raw = raw.replace("\\/", "/")
    links = re.findall(r"https?://[^\s\"'<>\\]+", raw)
    keep: list[str] = []
    for link in links:
        link = html.unescape(link).rstrip(".,;:!?)]}")
        if any(k.lower() in link.lower() for k in KEYWORDS) or any(host in link for host in ["youtube.com/watch", "youtu.be", "bilibili.com/video"]):
            if link not in keep:
                keep.append(link)
    return keep[:30]


def contexts(raw: str) -> tuple[list[str], list[str]]:
    text = clean(raw)
    hits: list[str] = []
    snippets: list[str] = []
    spans: list[tuple[int, int]] = []
    lower = text.lower()
    for key in KEYWORDS:
        key_lower = key.lower()
        start = 0
        while True:
            idx = lower.find(key_lower, start)
            if idx < 0:
                break
            left = max(0, idx - 160)
            right = min(len(text), idx + 220)
            snippet = text[left:right]
            start = idx + len(key_lower)
            if snippet.count("�") > max(3, len(snippet) // 20):
                continue
            overlap = any(max(0, min(right, old_right) - max(left, old_left)) > 0.6 * min(right - left, old_right - old_left) for old_left, old_right in spans)
            hits.append(key)
            if not overlap:
                snippets.append(snippet)
                spans.append((left, right))
            break
    return hits, list(dict.fromkeys(snippets))[:12]


def youtube_oembed(url: str) -> dict[str, str]:
    api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
    try:
        _, body = request(api)
        data = json.loads(body)
    except Exception:
        return {}
    return {
        "title": str(data.get("title", "")),
        "author": str(data.get("author_name", "")),
        "author_url": str(data.get("author_url", "")),
    }


def youtube_page_metadata(raw: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    patterns = {
        "description": [r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"', r'"attributedDescriptionBodyText"\s*:\s*\{"content"\s*:\s*"((?:\\.|[^"\\])*)"'],
        "author": [r'"author"\s*:\s*"((?:\\.|[^"\\])*)"'],
    }
    for key, pats in patterns.items():
        for pattern in pats:
            match = re.search(pattern, raw)
            if match:
                value = decode_js_string(match.group(1)).strip()
                if value:
                    fields[key] = value
                    break
    return fields


def bilibili_public_metadata(url: str) -> dict[str, str]:
    match = re.search(r"(BV[0-9A-Za-z]+)", url)
    if not match:
        return {}
    api = "https://api.bilibili.com/x/web-interface/view?bvid=" + urllib.parse.quote(match.group(1))
    try:
        _, body = request(api)
        payload = json.loads(body)
    except Exception:
        return {}
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        return {}
    data = payload["data"]
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return {
        "title": str(data.get("title", "")),
        "author": str(owner.get("name", "")),
        "description": str(data.get("desc", "")),
        "tname": str(data.get("tname", "")),
    }


def probe(url: str) -> Probe:
    try:
        status, raw = request(url)
        title = extract_title(raw)
        desc = extract_meta(raw, "description")
        author = ""
        jsonld = extract_jsonld(raw)
        title = title or jsonld.get("title", "")
        desc = desc or jsonld.get("description", "")
        author = jsonld.get("author", "")
        if "bilibili.com/video" in url:
            bili = extract_bilibili_state(raw)
            if not (bili.get("title") or bili.get("description") or bili.get("author")):
                bili = bilibili_public_metadata(url)
            title = bili.get("title") or title
            desc = bili.get("description") or desc
            author = bili.get("author") or author
            extra = "\n".join(value for value in [bili.get("tags", ""), bili.get("tname", ""), desc] if value)
            if extra:
                raw = extra + "\n" + raw
        if "youtube.com/watch" in url or "youtu.be/" in url:
            yt_page = youtube_page_metadata(raw)
            data = youtube_oembed(url)
            title = data.get("title") or title
            author = data.get("author") or yt_page.get("author") or author
            desc = yt_page.get("description") or desc
            raw += "\n".join(value for value in [title, author, desc] if value)
            if data.get("author_url"):
                raw += "\n" + data["author_url"]
        hits, snippets = contexts(raw)
        return Probe(url, status, title, author, desc, extract_links(raw), hits, snippets)
    except Exception as exc:
        return Probe(url, None, "", "", "", [], [], [], str(exc))


def write_markdown(path: Path, probes: list[Probe]) -> None:
    lines = ["# Video Probe Report", ""]
    for item in probes:
        lines.extend(
            [
                f"## {item.title or item.url}",
                "",
                f"- URL: {item.url}",
                f"- Status: {item.status or ''}",
                f"- Author: {item.author}",
                f"- Description: {item.description}",
                f"- Keyword hits: {', '.join(item.keyword_hits)}",
                f"- Error: {item.error}",
                "",
                "### Links",
                "",
            ]
        )
        lines.extend(f"- {link}" for link in item.links)
        lines.extend(["", "### Contexts", ""])
        lines.extend(f"- {ctx}" for ctx in item.contexts)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe public video metadata for score-related links.")
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    probes = [probe(url) for url in args.urls]
    if args.json:
        print(json.dumps([asdict(item) for item in probes], ensure_ascii=False, indent=2))
    else:
        out = Path(args.out) if args.out else Path("VIDEO_PROBE.md")
        write_markdown(out, probes)
        print(out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
