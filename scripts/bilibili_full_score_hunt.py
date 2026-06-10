#!/usr/bin/env python3
"""Bilibili-only source probe for possible complete piano score shares.

The script is intentionally conservative:
- it searches Bilibili web result pages and probes public video metadata;
- it extracts public descriptions/metadata, plus optional user-provided visible
  comment text, into raw source evidence records;
- it never uses private APIs, bypasses login/CAPTCHA/payment, or downloads from
  cloud drives automatically.

Final candidate status is owned by classify_candidates.py; this script must not
be used as the product classifier.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

FULL_SCORE_RE = re.compile(
    r"\b(pdf|mscz|mxl|musicxml)\b|\.pdf\b|\.mscz\b|\.mxl\b|\.musicxml\b|五线谱|完整谱|钢琴谱|谱包|乐谱包",
    re.I,
)
BUNDLE_RE = re.compile(r"\b(zip|rar|7z)\b|\.zip\b|\.rar\b|\.7z\b|压缩包|打包", re.I)
MIDI_RE = re.compile(r"\b(mid|midi)\b|\.mid\b|\.midi\b", re.I)
EXCLUDE_RE = re.compile(r"简谱|数字谱|瀑布流|waterfall|falling[- ]?note|synthesia|pianotutorial", re.I)
PAID_RE = re.compile(r"付费|购买|收费|有偿|¥|￥|booth|gumroad|ko-fi|patreon|shopee|piascore|kokomu|mymusicsheet|購入|buy sheet|purchase sheet|会员专属|充电专属", re.I)
PRIVATE_RE = re.compile(r"私信|进群|加群|qq群|微信群|关注后|三连后|后台|私聊", re.I)
MISSING_CODE_RE = re.compile(r"提取码|提取碼|访问码|訪問碼|解压码|密码|密碼|code|password", re.I)
NON_PIANO_RE = re.compile(r"吉他|主音吉他|guitar|鼓谱|鼓|口琴|harmonica|歌词|lyrics|伴奏|分轨|乐队谱", re.I)
URL_RE = re.compile(r"https?://[^\s<>'\"，。；；、）)\]}]+", re.I)
CODE_RE = re.compile(r"(?:提取码|提取碼|访问码|訪問碼|解压码|密码|密碼|pass(?:word)?|code)[:：\s]*([A-Za-z0-9]{3,8})", re.I)

CLOUD_HOST_HINTS = [
    "pan.baidu.com",
    "pan.quark.cn",
    "aliyundrive.com",
    "alipan.com",
    "lanzou",
    "123pan.com",
    "123684.com",
    "drive.google.com",
    "dropbox.com",
    "1drv.ms",
    "mega.nz",
]


def slugify(value: str, max_len: int = 80) -> str:
    value = urllib.parse.unquote(value)
    value = re.sub(r"[^\w._ -]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = value.strip("-._")
    return (value[:max_len].strip("-._") or "score")


def quote(value: str) -> str:
    value = value.strip()
    return f'"{value}"' if value else ""


def build_queries(title: str, artist: str = "", aliases: list[str] | None = None) -> list[str]:
    aliases = aliases or []
    names = [title, *aliases]
    names = [name for i, name in enumerate(names) if name and name not in names[:i]]
    artist_part = f" {artist}" if artist else ""
    templates = [
        "{q}{artist} 钢琴谱 五线谱 完整谱",
        "{q}{artist} 附谱 谱在简介 谱在评论 置顶",
        "{q}{artist} pdf mscz musicxml mxl 网盘 提取码",
        "{q}{artist} 钢琴谱 -简谱 -瀑布流",
        "{q}{artist} 五线谱 钢琴 独奏 谱子",
    ]
    rows: list[str] = []
    for name in names:
        for template in templates:
            rows.append(template.format(q=quote(name), artist=artist_part))
    return rows


def request(url: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7",
            "Referer": "https://www.bilibili.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def normalize_bvid(value: str) -> str | None:
    match = re.search(r"(BV[0-9A-Za-z]+)", value)
    return match.group(1) if match else None


def video_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}/"


def search_bilibili(query: str, max_results: int) -> tuple[list[str], str]:
    url = "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(query)
    try:
        status, raw = request(url)
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    bvids = list(dict.fromkeys(re.findall(r"BV[0-9A-Za-z]{8,}", raw)))
    return [video_url(bvid) for bvid in bvids[:max_results]], f"HTTP {status}"


def bilibili_public_metadata(url: str) -> tuple[dict[str, Any], str]:
    bvid = normalize_bvid(url)
    if not bvid:
        return {}, "missing BV id"
    api = "https://api.bilibili.com/x/web-interface/view?bvid=" + urllib.parse.quote(bvid)
    try:
        status, body = request(api)
        payload = json.loads(body)
    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        return {}, f"HTTP {status}; code={payload.get('code')}; message={payload.get('message')}"
    data = payload["data"]
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return {
        "bvid": bvid,
        "url": video_url(bvid),
        "title": str(data.get("title", "")),
        "author": str(owner.get("name", "")),
        "description": str(data.get("desc", "")),
        "tname": str(data.get("tname", "")),
        "dynamic": str(data.get("dynamic", "")),
    }, f"HTTP {status}; api code=0"


def extract_links(text: str, window: int = 140) -> list[dict[str, str]]:
    text = html.unescape(text)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url in seen:
            continue
        seen.add(url)
        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        context = re.sub(r"\s+", " ", text[start:end]).strip()
        code = (CODE_RE.search(context) or [None, ""])[1]
        kind = "cloud-share" if any(host in url.lower() for host in CLOUD_HOST_HINTS) else "direct-or-page"
        rows.append({"url": url, "kind": kind, "access_code": code, "context": context})
    return rows


def score_format(text: str) -> str:
    formats: list[str] = []
    for label, pattern in [
        ("PDF", r"\bpdf\b|\.pdf\b"),
        ("MSCZ", r"\bmscz\b|\.mscz\b"),
        ("MXL", r"\bmxl\b|\.mxl\b"),
        ("MusicXML", r"\bmusicxml\b|\.musicxml\b"),
        ("score-bundle", r"\b(zip|rar|7z)\b|谱包|乐谱包|压缩包|打包"),
        ("full-piano-score-text", r"钢琴谱|五线谱|完整谱|乐谱链接|曲谱链接|谱链接"),
        ("MIDI", r"\b(mid|midi)\b|\.mid\b|\.midi\b"),
    ]:
        if re.search(pattern, text, re.I):
            formats.append(label)
    return ", ".join(dict.fromkeys(formats))


def is_target_match(text: str, target_terms: list[str]) -> bool:
    folded = re.sub(r"\s+", "", text).lower()
    for term in target_terms:
        term = re.sub(r"\s+", "", term).lower()
        if term and term in folded:
            return True
    return False


def probe_signals(text: str, links: list[dict[str, str]], target_terms: list[str], force_target: bool = False) -> dict[str, Any]:
    """Extract deterministic source signals without deciding final status."""
    lower_text = text.lower()
    has_cloud = any(item["kind"] == "cloud-share" for item in links)
    has_full = bool(FULL_SCORE_RE.search(text)) or (bool(BUNDLE_RE.search(text)) and any(word in lower_text for word in ["谱", "score", "sheet"]))
    has_midi = bool(MIDI_RE.search(text))
    excluded = bool(EXCLUDE_RE.search(text))
    paid = bool(PAID_RE.search(text))
    private = bool(PRIVATE_RE.search(text))
    mentions_code = bool(MISSING_CODE_RE.search(text))
    has_code = any(item.get("access_code") for item in links)
    non_piano = bool(NON_PIANO_RE.search(text)) and not re.search(r"钢琴|piano", text, re.I)
    target_match = force_target or is_target_match(text, target_terms)
    return {
        "target_match": target_match,
        "has_links": bool(links),
        "has_cloud_link": has_cloud,
        "has_full_score_text": has_full,
        "has_midi_text": has_midi,
        "has_exclusion_text": excluded,
        "has_paid_text": paid,
        "has_private_gate_text": private,
        "mentions_access_code": mentions_code,
        "has_access_code": has_code,
        "non_piano_text": non_piano,
        "needs_comment_or_manual_probe": has_full and not links or (mentions_code and has_cloud and not has_code),
    }


@dataclass
class Candidate:
    url: str
    title: str = ""
    author: str = ""
    status: str = "source_probe"
    action: str = "source probe only; run classify_candidates.py for final status"
    format: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    evidence: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    probe_status: str = ""


def inspect_video(url: str, target_terms: list[str], extra_text: str = "", force_target: bool = False) -> Candidate:
    meta, probe_status = bilibili_public_metadata(url)
    text = "\n".join(
        value
        for value in [
            meta.get("title", ""),
            meta.get("author", ""),
            meta.get("tname", ""),
            meta.get("description", ""),
            meta.get("dynamic", ""),
            extra_text,
        ]
        if value
    )
    links = extract_links(text)
    signals = probe_signals(text, links, target_terms, force_target=force_target)
    evidence = re.sub(r"\s+", " ", text).strip()
    if len(evidence) > 800:
        evidence = evidence[:800] + "..."
    return Candidate(
        url=meta.get("url") or url,
        title=meta.get("title", ""),
        author=meta.get("author", ""),
        format=score_format(text),
        signals=signals,
        evidence=evidence,
        links=links,
        probe_status=probe_status,
    )


def read_comment_text(paths: list[str]) -> str:
    chunks: list[str] = []
    for value in paths:
        chunks.append(Path(value).read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def write_outputs(out_dir: Path, title: str, queries: list[str], search_log: list[dict[str, Any]], candidates: list[Candidate]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "BILIBILI_HUNT.json"
    md_path = out_dir / "BILIBILI_HUNT.md"
    payload = {
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "title": title,
        "queries": queries,
        "search_log": search_log,
        "candidates": [asdict(item) for item in candidates],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    counts: dict[str, int] = {}
    for item in candidates:
        counts[item.status] = counts.get(item.status, 0) + 1
    lines = [
        f"# Bilibili Source Probe: {title}",
        "",
        f"- Generated UTC: {payload['generated_utc']}",
        "- Scope: Bilibili only; public metadata and operator-supplied visible text only.",
        "- This file is source evidence, not final classification.",
        "- Final status belongs to `classify_candidates.py`; ranking belongs to `dedupe_candidates.py`.",
        f"- Videos inspected: {len(candidates)}",
        "",
        "## Summary",
        "",
    ]
    if counts:
        for status, count in sorted(counts.items()):
            lines.append(f"- {status}: {count}")
    else:
        lines.append("- no_candidates: 0")
    lines.extend(["", "## Queries", ""])
    for row in queries:
        lines.append(f"- `{row}`")
    lines.extend(["", "## Search log", ""])
    for row in search_log:
        lines.append(f"- `{row.get('query', '')}`: {row.get('status', '')}; found={row.get('found', 0)}")
    lines.extend(["", "## Candidates", ""])
    for idx, item in enumerate(candidates, 1):
        lines.extend(
            [
                f"### {idx}. {item.title or item.url}",
                "",
                f"- URL: {item.url}",
                f"- UP: {item.author}",
                f"- Probe: {item.probe_status}",
                f"- Status: {item.status}",
                f"- Action: {item.action}",
                f"- Format evidence: {item.format}",
                f"- Signals: `{json.dumps(item.signals, ensure_ascii=False)}`",
                f"- Evidence: {item.evidence}",
                "",
                "#### Links",
                "",
            ]
        )
        if item.links:
            for link in item.links:
                code = f"; code={link['access_code']}" if link.get("access_code") else ""
                lines.append(f"- {link['kind']}{code}: {link['url']}")
                if link.get("context"):
                    lines.append(f"  - Context: {link['context']}")
        else:
            lines.append("- none in public title/description metadata")
        lines.append("")
    lines.extend(
        [
            "## Manual action points",
            "",
            "- This automated pass does not bypass Bilibili login, CAPTCHA, private messages, private groups, or paid/member-only gates.",
            "- If a promising video says the score is in comments but public metadata has no link, inspect pinned/visible comments with the logged-in browser and feed copied text back via `--comment-text`.",
            "- If a cloud-drive page opens but needs confirmation or an extraction code not present in public metadata, record it as manual action instead of bypassing.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Bilibili-only source probe; emits raw evidence, not final classification.")
    parser.add_argument("title")
    parser.add_argument("--artist", default="")
    parser.add_argument("--alias", action="append", default=[])
    parser.add_argument("--url", action="append", default=[], help="Known Bilibili video URL/BVID to inspect")
    parser.add_argument("--comment-text", action="append", default=[], help="Copied visible Bilibili comments/dynamics text files")
    parser.add_argument("--out", default="sheet-music")
    parser.add_argument("--max-videos", type=int, default=12)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    queries = build_queries(args.title, args.artist, args.alias)
    target_terms = [args.title, *args.alias]
    urls: list[str] = []
    search_log: list[dict[str, Any]] = []
    per_query_limit = max(1, min(args.max_videos, 6))
    for query in queries:
        found, status = search_bilibili(query, per_query_limit)
        search_log.append({"query": query, "status": status, "found": len(found)})
        urls.extend(found)
        urls = list(dict.fromkeys(urls))
        if len(urls) >= args.max_videos:
            break

    forced_urls: set[str] = set()
    for value in args.url:
        bvid = normalize_bvid(value)
        normalized = video_url(bvid) if bvid else value
        forced_urls.add(normalized)
        urls.append(normalized)
    urls = list(dict.fromkeys(urls))[: args.max_videos]

    extra_text = read_comment_text(args.comment_text) if args.comment_text else ""
    candidates = [inspect_video(url, target_terms=target_terms, extra_text=extra_text, force_target=url in forced_urls) for url in urls]
    out_dir = Path(args.out) / slugify(f"{args.title}-{args.artist}" if args.artist else args.title)
    json_path, md_path = write_outputs(out_dir, args.title, queries, search_log, candidates)

    if args.json:
        print(json_path.read_text(encoding="utf-8"))
    else:
        print(md_path)
        print(json_path)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
