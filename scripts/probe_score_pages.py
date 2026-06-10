#!/usr/bin/env python3
"""Probe score pages for visible files, login gates, and direct artifacts."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path


USER_AGENT = "CodexPianoSheetFetcher/1.0 score-page probe"
SCORE_EXT_RE = re.compile(r"\.(pdf|mscz|mxl|musicxml|mid|midi|zip|rar|7z|jpg|jpeg|png)(?:$|[?#])", re.I)
RELATED_HOSTS = [
    "booth.pm",
    "drive.google.com",
    "dropbox.com",
    "musescore.com",
    "nicovideo.jp",
    "nicozon.net",
    "onlinesequencer.net",
    "pan.baidu.com",
    "sheet.host",
    "twitter.com",
    "x.com",
    "youtube.com",
    "youtu.be",
]
SIGNAL_KEYWORDS = [
    "all rights reserved",
    "booth",
    "download",
    "download midi",
    "login",
    "midi",
    "musescore pro",
    "offline access",
    "pdf",
    "print",
    "pro+",
    "subscription",
    "ログイン",
    "メンバー",
    "購入",
    "楽譜",
    "提取码",
    "网盘",
]


@dataclass
class VisibleFile:
    name: str
    size: str
    kind: str
    href: str
    access: str


@dataclass
class ScoreProbe:
    url: str
    status: int | None
    title: str
    page_type: str
    files: list[VisibleFile]
    direct_links: list[str]
    related_links: list[str]
    signals: list[str]
    notes: list[str]
    error: str = ""


def request(url: str) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en,ja,zh-CN;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.status, response.geturl(), response.read().decode("utf-8", errors="replace")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def title(raw: str) -> str:
    return html.unescape((re.search(r"<title[^>]*>([^<]*)", raw, re.I) or ["", ""])[1])


def page_type(final_url: str) -> str:
    host = urllib.parse.urlparse(final_url).netloc.lower()
    if "sheet.host" in host:
        return "sheethost"
    if "musescore.com" in host:
        return "musescore"
    if "midishow.com" in host:
        return "midishow"
    if "nicozon.net" in host or "nicovideo.jp" in host:
        return "nico-video-index"
    if "onlinesequencer.net" in host:
        return "online-sequencer"
    return "score-page"


def direct_links(raw: str, base: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"""href=["']([^"']+)["']""", raw, re.I):
        href = html.unescape(match.group(1))
        url = urllib.parse.urljoin(base, href)
        parsed_path = urllib.parse.urlparse(url).path.lower()
        if any(skip in parsed_path for skip in ["/assets/", "/favicon", "apple-touch-icon"]):
            continue
        if SCORE_EXT_RE.search(parsed_path) and url not in links:
            links.append(url)
    return links


def related_links(raw: str, base: str) -> list[str]:
    raw = raw.replace("\\/", "/")
    links: list[str] = []
    for match in re.finditer(r"""(?:href=["']|["'])(https?://[^"'<>\s\\]+)""", raw, re.I):
        url = html.unescape(match.group(1)).rstrip(".,;:!?)]}")
        if is_noise_link(url, base):
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(related in host for related in RELATED_HOSTS) and url not in links:
            links.append(url)
    for match in re.finditer(r"""href=["']([^"']+)["']""", raw, re.I):
        href = html.unescape(match.group(1))
        url = urllib.parse.urljoin(base, href)
        if is_noise_link(url, base):
            continue
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(related in host for related in RELATED_HOSTS) and url not in links:
            links.append(url)
    return links[:40]


def is_noise_link(url: str, base: str = "") -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if any(skip in path for skip in ["/assets/", "/favicon", "apple-touch-icon"]):
        return True
    if path.endswith((".css", ".js", ".gif", ".ico", ".png", ".jpg", ".jpeg", ".svg", ".webp")):
        return True
    if path.startswith("/http"):
        return True
    base_host = urllib.parse.urlparse(base).netloc.lower() if base else ""
    if base_host and parsed.netloc.lower() == base_host:
        keep_prefixes = ("/sheet/", "/user/", "/account/login", "/instrument/")
        if not path.startswith(keep_prefixes):
            return True
    return False


def page_signals(raw: str) -> list[str]:
    text = strip_tags(re.sub(r"<script[\s\S]*?</script>", " ", raw, flags=re.I))
    lowered = text.lower()
    signals: list[str] = []
    for keyword in SIGNAL_KEYWORDS:
        idx = lowered.find(keyword.lower())
        if idx < 0:
            continue
        snippet = text[max(0, idx - 80) : min(len(text), idx + 160)]
        if snippet.count("�") > max(3, len(snippet) // 20):
            continue
        signals.append(snippet)
    return list(dict.fromkeys(signals))[:12]


def sheethost_files(raw: str, base: str) -> list[VisibleFile]:
    files: list[VisibleFile] = []
    block_match = re.search(r'<div class="well sheet-download">([\s\S]*?)</div>\s*<div class="well sheet-share', raw, re.I)
    if not block_match:
        return files
    block = block_match.group(1)
    for item in re.finditer(r"""<li>\s*<a\s+href=["']([^"']+)["'][^>]*>([\s\S]*?)</a>\s*</li>""", block, re.I):
        href = urllib.parse.urljoin(base, html.unescape(item.group(1)))
        label_html = item.group(2)
        label = strip_tags(label_html)
        size_match = re.search(r"\(([^)]*B)\)", label)
        size = size_match.group(1) if size_match else ""
        name = re.sub(r"\s*\([^)]*B\)\s*$", "", label).strip()
        kind = Path(name).suffix.lstrip(".").lower() or "file"
        access = "login_required" if "/account/login" in href or "code=4001" in href else "visible_link"
        files.append(VisibleFile(name=name, size=size, kind=kind, href=href, access=access))
    return files


def probe(url: str) -> ScoreProbe:
    try:
        status, final_url, raw = request(url)
        files = sheethost_files(raw, final_url)
        links = direct_links(raw, final_url)
        related = related_links(raw, final_url)
        signals = page_signals(raw)
        notes: list[str] = []
        kind = page_type(final_url)
        if files and all(item.access == "login_required" for item in files):
            notes.append("Visible score files exist, but downloads route through login. Do not bypass.")
        lowered_signals = " ".join(signals).lower()
        if any(word in lowered_signals for word in ["subscription", "pro+", "musescore pro", "offline access", "login", "ログイン", "メンバー"]):
            notes.append("Page shows account, membership, or subscription signals. Use normal site flow only.")
        if related and not links:
            notes.append("Related source links were found, but no direct score artifact link was exposed in public HTML.")
        if not files and not links:
            notes.append("No direct score files found in public HTML.")
        return ScoreProbe(url, status, title(raw), kind, files, links, related, signals, notes)
    except Exception as exc:
        notes: list[str] = []
        if isinstance(exc, urllib.error.HTTPError) and exc.code in {401, 403}:
            notes.append("Public script request was denied. Use normal browser/account flow; do not bypass.")
        elif isinstance(exc, urllib.error.URLError):
            notes.append("Network or platform request failed; rerun later or inspect through normal browser UI.")
        return ScoreProbe(url, None, "", page_type(url), [], [], [], [], notes, str(exc))


def write_markdown(path: Path, probes: list[ScoreProbe]) -> None:
    lines = ["# Score Page Probe", ""]
    for item in probes:
        lines.extend(
            [
                f"## {item.title or item.url}",
                "",
                f"- URL: {item.url}",
                f"- Status: {item.status or ''}",
                f"- Page type: {item.page_type}",
                f"- Error: {item.error}",
                "",
                "### Visible files",
                "",
            ]
        )
        if not item.files:
            lines.append("- none")
        for file in item.files:
            lines.extend(
                [
                    f"- `{file.name}`",
                    f"  - kind: {file.kind}",
                    f"  - size: {file.size}",
                    f"  - access: {file.access}",
                    f"  - href: {file.href}",
                ]
            )
        lines.extend(["", "### Direct links", ""])
        if not item.direct_links:
            lines.append("- none")
        else:
            lines.extend(f"- {link}" for link in item.direct_links)
        lines.extend(["", "### Related links", ""])
        if not item.related_links:
            lines.append("- none")
        else:
            lines.extend(f"- {link}" for link in item.related_links)
        lines.extend(["", "### Page signals", ""])
        if not item.signals:
            lines.append("- none")
        else:
            lines.extend(f"- {signal}" for signal in item.signals)
        lines.extend(["", "### Notes", ""])
        if not item.notes:
            lines.append("- none")
        else:
            lines.extend(f"- {note}" for note in item.notes)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe visible score files without downloading them.")
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--out", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    probes = [probe(url) for url in args.urls]
    if args.json:
        print(json.dumps([asdict(item) for item in probes], ensure_ascii=False, indent=2))
    else:
        out = Path(args.out) if args.out else Path("SCORE_PAGE_PROBE.md")
        write_markdown(out, probes)
        print(out)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
