#!/usr/bin/env python3
"""Phase 2a multi-source proxy runner for piano score discovery.

This is a test harness, not product code. It searches public pages across a few
user-approved channels, downloads only public direct score artifacts, records
login-gated strong candidates such as SheetHost, and excludes paid/store links.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
SCORE_EXTS = {".pdf", ".mscz", ".mxl", ".musicxml", ".mid", ".midi", ".zip"}
SUCCESS_EXTS = {".pdf", ".mscz", ".mxl", ".musicxml", ".zip"}
PAID_HOST_HINTS = ["mymusicsheet", "mymusic.st", "kokomu", "piascore", "gumroad", "patreon", "ko-fi", "booth.pm", "theta", "thetapiano"]
STORE_WORDS = re.compile(r"\b(?:purchase|buy|paid)\b|gumroad|patreon|ko-fi|mymusicsheet|kokomu|piascore|booth|購入|有料|付费|购买|收费", re.I)
GOOD_WORDS = re.compile(r"pdf|mscz|mxl|musicxml|sheet|score|楽譜|乐谱|鋼琴譜|钢琴谱|五线谱", re.I)
URL_RE = re.compile(r"https?://[^\s<>'\"，。；、）)\]}]+", re.I)
YOUTUBE_ID_RE = re.compile(r"(?:watch\?v=|/shorts/|youtu\.be/)([A-Za-z0-9_-]{11})")
KEEP_HOST_HINTS = [
    "drive.google", "dropbox", "1drv.ms", "onedrive", "mega.nz", "mediafire",
    "github.com", "raw.githubusercontent", "sheet.host", "musescore.com",
    "pianoscoreone", "blogspot", "wordpress", "gumroad", "patreon", "ko-fi",
    "mymusicsheet", "mymusic.st", "kokomu", "piascore", "booth.pm",
]

GOLDEN_SET = [
    {"id":"tada-kimi-ni-hare","title":"ただ君に晴れ","artist":"Yorushika","aliases":["君晴","只为你拨云放晴","Tada Kimi ni Hare","Just a Sunny Day for You"],"category":"热门日推/动漫"},
    {"id":"yoake-to-hotaru","title":"夜明けと蛍","artist":"n-buna","aliases":["黎明与萤火","Yoake to Hotaru"],"category":"热门日推/动漫"},
    {"id":"lemon","title":"Lemon","artist":"米津玄師","aliases":["米津玄师 Lemon","Kenshi Yonezu Lemon"],"category":"热门日推/动漫"},
    {"id":"uchiage-hanabi","title":"打上花火","artist":"DAOKO 米津玄師","aliases":["Uchiage Hanabi","烟花"],"category":"热门日推/动漫"},
    {"id":"haru-dorobou","title":"春泥棒","artist":"Yorushika","aliases":["Spring Thief","春泥棒 ヨルシカ"],"category":"冷门/小众"},
    {"id":"hitchcock","title":"ヒッチコック","artist":"Yorushika","aliases":["Hitchcock","ヨルシカ ヒッチコック"],"category":"冷门/小众"},
    {"id":"dry-flower","title":"ドライフラワー","artist":"優里","aliases":["Dry Flower","Dried Flower","Yuuri Dry Flower"],"category":"冷门/小众"},
    {"id":"qi-feng-le","title":"起风了","artist":"买辣椒也用券","aliases":["起風了","風になる 中文","The Wind Rises Chinese"],"category":"中文流行"},
    {"id":"qing-tian","title":"晴天","artist":"周杰伦","aliases":["Jay Chou 晴天","Sunny Day Jay Chou"],"category":"中文流行"},
    {"id":"canon","title":"卡农","artist":"Pachelbel","aliases":["Canon in D","Pachelbel Canon"],"category":"古典/改编"},
]

@dataclass
class LinkRecord:
    url: str
    channel: str
    source_url: str
    title: str = ""
    status: str = "candidate"
    classification: str = "unknown"
    file_name: str = ""
    bytes: int | None = None
    sha256: str = ""
    local_path: str = ""
    evidence: str = ""
    error: str = ""


def request(url: str, timeout: int = 30) -> tuple[int, str, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return r.status, r.geturl(), r.headers.get("Content-Type", ""), data


def text_request(url: str, timeout: int = 30) -> tuple[int, str, str]:
    status, final, _ctype, data = request(url, timeout)
    return status, final, data.decode("utf-8", errors="replace")


def clean_url(url: str) -> str:
    url = html.unescape(url.replace("\\/", "/"))
    url = url.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u002F", "/")
    # YouTube embeds descriptions as JSON-escaped text; a naive URL regex can
    # otherwise consume literal "\n..." or "\u0026..." fragments past the URL.
    url = re.split(r"(?:\\n|\\r|\\t|\\\\n|\\\\r|\\\\t|\\u003c|\\u003e|[<>])", url, 1)[0]
    return url.rstrip(".,;:!?)]}")


def normalize_external_url(url: str) -> str:
    url = clean_url(url)
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc.lower().endswith("youtube.com") and parsed.path.startswith("/redirect"):
        q = urllib.parse.parse_qs(parsed.query).get("q")
        if q:
            return clean_url(q[0])
    return url


def host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except ValueError:
        return ""


def slugify(s: str, max_len: int = 80) -> str:
    s = urllib.parse.unquote(s)
    s = re.sub(r"[^\w._ -]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip().lower()).strip("-._")
    return (s[:max_len].strip("-._") or "score")


def target_terms(song: dict[str, Any]) -> list[str]:
    return [song["title"], song.get("artist", ""), *song.get("aliases", [])]


def title_matches(song: dict[str, Any], text: str) -> bool:
    folded = re.sub(r"\s+", "", text).lower()
    for t in target_terms(song):
        t2 = re.sub(r"\s+", "", str(t)).lower()
        if t2 and t2 in folded:
            return True
    return False


def youtube_search(query: str, limit: int = 8) -> tuple[list[str], str]:
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    ids = []
    for vid in re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', raw) + re.findall(r"watch\?v=([A-Za-z0-9_-]{11})", raw):
        if vid not in ids:
            ids.append(vid)
    return [f"https://www.youtube.com/watch?v={v}" for v in ids[:limit]], f"HTTP {status}"


def bing_search(query: str, limit: int = 10) -> tuple[list[str], str]:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    urls = []
    for m in re.finditer(r'<a\s+href="(https?://[^"]+)"', raw):
        u = clean_url(m.group(1))
        h = host(u)
        if any(skip in h for skip in ["microsoft", "bing.com", "go.microsoft"]):
            continue
        if u not in urls:
            urls.append(u)
        if len(urls) >= limit:
            break
    return urls, f"HTTP {status}"


def extract_youtube_metadata(url: str) -> tuple[str, str, list[str], str]:
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        return "", "", [], f"{type(e).__name__}: {e}"
    title = ""
    mt = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', raw)
    if mt:
        title = html.unescape(mt.group(1))
    desc = ""
    for pat in [r'"shortDescription"\s*:\s*"((?:\\.|[^"\\])*)"', r'<meta\s+name="description"\s+content="([^"]*)"']:
        m = re.search(pat, raw)
        if m:
            val = m.group(1)
            try:
                desc = json.loads('"' + val + '"') if "\\" in val else html.unescape(val)
            except Exception:
                desc = html.unescape(val)
            break
    links = []
    blob = raw.replace("\\/", "/") + "\n" + desc
    for m in URL_RE.finditer(blob):
        u = normalize_external_url(m.group(0))
        h = host(u)
        suffix = Path(urllib.parse.urlparse(u).path).suffix.lower()
        keep = any(k in h for k in KEEP_HOST_HINTS) or suffix in SCORE_EXTS or STORE_WORDS.search(u)
        if keep and u not in links:
            links.append(u)
    return title, desc, links[:40], f"HTTP {status}"


def google_drive_file_id(url: str) -> str:
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (q.get("id") or [""])[0]


def is_artifact(data: bytes, url: str, ctype: str) -> bool:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    base = ctype.lower().split(";", 1)[0].strip()
    # Do not trust application/octet-stream by itself: Google Drive can return
    # opaque sidecar/metadata bytes with that type. Require a known signature,
    # a known filename suffix, or a precise score MIME type.
    return data.startswith(b"%PDF") or data.startswith(b"PK\x03\x04") or suffix in SCORE_EXTS or base in {"application/pdf", "application/zip", "audio/midi", "audio/x-midi"}


def write_artifact(song: dict[str, Any], data: bytes, url: str, out: Path, prefix: str) -> tuple[str, str, int, str]:
    song_dir = out / "downloads" / song["id"]
    song_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if suffix not in SCORE_EXTS:
        suffix = ".pdf" if data.startswith(b"%PDF") else ".zip" if data.startswith(b"PK\x03\x04") else ".bin"
    name = slugify(prefix + "-" + (Path(urllib.parse.urlparse(url).path).stem or song["id"])) + suffix
    path = song_dir / name
    i = 2
    while path.exists():
        path = song_dir / f"{Path(name).stem}-{i}{suffix}"
        i += 1
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return str(path), digest, len(data), suffix


def try_download(song: dict[str, Any], url: str, source_url: str, channel: str, out: Path) -> LinkRecord:
    rec = LinkRecord(url=url, channel=channel, source_url=source_url, status="candidate", classification="direct_or_cloud")
    h = host(url)
    download_url = url
    if "drive.google.com" in h:
        fid = google_drive_file_id(url)
        if not fid:
            rec.status = "manual_action_required"; rec.classification = "cloud_folder_or_unknown"; rec.evidence = "Google Drive URL without file id"
            return rec
        download_url = "https://drive.google.com/uc?export=download&id=" + urllib.parse.quote(fid)
        rec.classification = "google_drive_file"
    try:
        status, final, ctype, data = request(download_url, timeout=45)
        if not is_artifact(data, final, ctype):
            # Google sometimes returns confirm page for large files; this harness intentionally does not bypass confirm flows.
            rec.status = "manual_action_required"
            rec.evidence = f"HTTP {status}; content-type={ctype}; not direct score artifact"
            return rec
        suffix = Path(urllib.parse.urlparse(final).path).suffix.lower()
        if suffix in {".mid", ".midi"}:
            rec.status = "midi_only_auxiliary"; rec.classification = "midi_only"
        else:
            rec.status = "downloaded"; rec.classification = "full_score_file"
        path, sha, n, _suffix = write_artifact(song, data, final, out, channel)
        rec.local_path = path; rec.sha256 = sha; rec.bytes = n; rec.file_name = Path(path).name
        rec.evidence = f"Downloaded artifact via {channel}; HTTP {status}; content-type={ctype}"
        return rec
    except Exception as e:
        rec.status = "failed"; rec.error = f"{type(e).__name__}: {e}"
        return rec


def probe_sheethost(url: str) -> LinkRecord:
    rec = LinkRecord(url=url, channel="sheethost", source_url=url, status="candidate", classification="score_platform")
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        rec.status = "failed"; rec.error = f"{type(e).__name__}: {e}"; return rec
    title = ""
    mt = re.search(r"<title[^>]*>([^<]+)", raw, re.I)
    if mt: title = html.unescape(mt.group(1)).strip()
    rec.title = title
    visible = []
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(re.sub(r"\s+", " ", text))
    for m in re.finditer(r"Download\s+([^()]{1,120}\.(?:pdf|mid|midi|mscz|mxl|musicxml|zip))\s*\(([^)]{1,30})\)", text, re.I):
        visible.append(f"{m.group(1).strip()} ({m.group(2).strip()})")
    if "account/login" in raw and visible:
        rec.status = "login_required_downloadable"; rec.classification = "full_score_login_gated"; rec.evidence = "; ".join(visible[:5])
    elif visible:
        rec.status = "login_required_downloadable"; rec.classification = "full_score_visible"; rec.evidence = "; ".join(visible[:5])
    else:
        rec.status = "page_candidate"; rec.evidence = text[:300]
    return rec


def classify_page_link(url: str, source_url: str) -> LinkRecord:
    h = host(url)
    rec = LinkRecord(url=url, channel="web", source_url=source_url)
    if any(p in h for p in PAID_HOST_HINTS):
        rec.status = "paid_or_store_excluded"; rec.classification = "paid_or_store"; return rec
    if "musescore.com" in h:
        rec.status = "page_candidate"; rec.classification = "score_platform_candidate"; return rec
    rec.status = "page_candidate"; rec.classification = "unprobed_page"; return rec


def song_queries(song: dict[str, Any]) -> list[str]:
    names = [song["title"], *song.get("aliases", [])[:3]]
    qs = []
    for n in names:
        qs.append(f"{n} {song.get('artist','')} piano sheet pdf")
        qs.append(f"{n} {song.get('artist','')} sheet music download")
    qs.append(f"{song['title']} site:sheet.host")
    qs.append(f"{song['title']} site:drive.google.com piano sheet")
    return list(dict.fromkeys(qs))


def run_song(song: dict[str, Any], out: Path, yt_limit: int, web_limit: int) -> dict[str, Any]:
    logs = []
    records: list[LinkRecord] = []
    seen_links: set[str] = set()
    yt_urls: list[str] = []
    for q in song_queries(song)[:8]:
        urls, status = youtube_search(q, limit=yt_limit)
        logs.append({"channel":"youtube_search", "query":q, "status":status, "found":len(urls)})
        for u in urls:
            if u not in yt_urls:
                yt_urls.append(u)
        time.sleep(0.2)
    for yurl in yt_urls[: max(yt_limit * 4, 16)]:
        title, desc, links, status = extract_youtube_metadata(yurl)
        logs.append({"channel":"youtube_video", "query":yurl, "status":status, "found":len(links), "title":title})
        text = title + "\n" + desc + "\n" + "\n".join(links)
        if not links and not (title_matches(song, text) or GOOD_WORDS.search(text)):
            continue
        for link in links:
            if link in seen_links:
                continue
            seen_links.add(link)
            h = host(link)
            if any(p in h for p in PAID_HOST_HINTS) or STORE_WORDS.search(link):
                records.append(LinkRecord(url=link, channel="youtube", source_url=yurl, title=title, status="paid_or_store_excluded", classification="paid_or_store"))
            elif "drive.google.com" in h or Path(urllib.parse.urlparse(link).path).suffix.lower() in SUCCESS_EXTS:
                records.append(try_download(song, link, yurl, "youtube", out))
            elif "sheet.host" in h:
                r = probe_sheethost(link); r.source_url = yurl; records.append(r)
            elif "musescore.com" in h:
                records.append(LinkRecord(url=link, channel="youtube", source_url=yurl, title=title, status="page_candidate", classification="score_platform_candidate"))
            else:
                if GOOD_WORDS.search(link):
                    records.append(classify_page_link(link, yurl))
        time.sleep(0.25)

    for q in song_queries(song)[:web_limit]:
        urls, status = bing_search(q, limit=8)
        logs.append({"channel":"web_search", "query":q, "status":status, "found":len(urls)})
        for u in urls:
            if u in seen_links:
                continue
            seen_links.add(u)
            h = host(u)
            if "sheet.host" in h:
                records.append(probe_sheethost(u))
            elif "drive.google.com" in h or Path(urllib.parse.urlparse(u).path).suffix.lower() in SUCCESS_EXTS:
                records.append(try_download(song, u, q, "web", out))
            else:
                records.append(classify_page_link(u, q))
        time.sleep(0.25)

    return {"song_id": song["id"], "title": song["title"], "artist": song.get("artist", ""), "category": song.get("category", ""), "logs": logs, "records": [asdict(r) for r in records]}


def record_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    d: dict[str, int] = {}
    for r in records:
        d[r["status"]] = d.get(r["status"], 0) + 1
    return d


def write_song_report(path: Path, row: dict[str, Any]) -> None:
    lines = [f"# Multi-source Phase 2a: {row['title']}", "", f"- Artist: {row.get('artist','')}", f"- Category: {row.get('category','')}", "", "## Summary", ""]
    for k, v in sorted(record_counts(row["records"]).items()):
        lines.append(f"- {k}: {v}")
    lines += ["", "## Logs", ""]
    for log in row["logs"]:
        lines.append(f"- {log['channel']} `{log['query']}`: {log['status']}; found={log['found']}")
    lines += ["", "## Records", ""]
    for i, r in enumerate(row["records"], 1):
        lines += [f"### {i}. {r.get('status')} / {r.get('classification')}", "", f"- URL: {r.get('url')}", f"- Source: {r.get('source_url')}", f"- Title: {r.get('title','')}", f"- File: {r.get('file_name','')}", f"- Local: {r.get('local_path','')}", f"- Bytes: {r.get('bytes') or ''}", f"- SHA256: {r.get('sha256','')}", f"- Evidence: {r.get('evidence','')}", f"- Error: {r.get('error','')}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--yt-limit", type=int, default=12)
    ap.add_argument("--web-limit", type=int, default=4)
    ap.add_argument("--song", action="append", help="Optional song id filter")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "reports").mkdir(exist_ok=True)
    songs = [s for s in GOLDEN_SET if not args.song or s["id"] in set(args.song)]
    (out / "golden_set.json").write_text(json.dumps({"frozen_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "songs": songs}, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    for s in songs:
        row = run_song(s, out, args.yt_limit, args.web_limit)
        results.append(row)
        write_song_report(out / "reports" / f"{s['id']}.md", row)
    summary = []
    for row in results:
        recs = row["records"]
        downloads = [r for r in recs if r["status"] == "downloaded"]
        strong = [r for r in recs if r["status"] in {"downloaded", "login_required_downloadable"} or r.get("classification") == "score_platform_candidate"]
        paid = [r for r in recs if r["status"] == "paid_or_store_excluded"]
        summary.append({
            "song_id": row["song_id"], "title": row["title"], "category": row["category"],
            "downloaded": len(downloads), "strong_candidates": len(strong), "paid_or_store_excluded": len(paid),
            "download_paths": [r["local_path"] for r in downloads],
            "strong_candidate_urls": [r["url"] for r in strong[:10]],
        })
    metrics = {
        "scope_note": "multi-source public proxy: YouTube public pages, Bing-discovered pages, SheetHost public probes, public direct/Google Drive file download only; no paid/login bypass; no logged-in Chrome because native host was unavailable in previous check",
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "song_count": len(songs),
        "downloaded_songs": sum(1 for s in summary if s["downloaded"] > 0),
        "strong_candidate_songs": sum(1 for s in summary if s["strong_candidates"] > 0),
        "paid_or_store_only_songs": sum(1 for s in summary if s["strong_candidates"] == 0 and s["paid_or_store_excluded"] > 0),
        "per_song": summary,
    }
    (out / "tool_results.json").write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Phase 2a Multi-source Proxy Conclusion", "", "## Scope", "", metrics["scope_note"], "", "## Metrics", "", f"- Songs: {metrics['song_count']}", f"- Downloaded songs: {metrics['downloaded_songs']}", f"- Strong-candidate songs: {metrics['strong_candidate_songs']}", f"- Paid/store-only songs: {metrics['paid_or_store_only_songs']}", "", "## Per-song", "", "| Song | Category | Downloads | Strong candidates | Paid/store excluded |", "|---|---|---:|---:|---:|"]
    for s in summary:
        lines.append(f"| {s['title']} | {s['category']} | {s['downloaded']} | {s['strong_candidates']} | {s['paid_or_store_excluded']} |")
    lines += ["", "## Downloaded files", ""]
    any_dl = False
    for s in summary:
        for p in s["download_paths"]:
            any_dl = True
            lines.append(f"- {s['title']}: `{p}`")
    if not any_dl:
        lines.append("- none")
    lines += ["", "## Interpretation", "", "Multi-source discovery is materially more useful than Bilibili-only for finding off-platform score links and structured login-gated hosts. However, direct local download success is still limited by whether the public page exposes a direct artifact link; SheetHost should be treated as a strong logged-in-browser action point, not as a public direct-download source.", ""]
    (out / "CONCLUSION.md").write_text("\n".join(lines), encoding="utf-8")
    print(out / "CONCLUSION.md")
    return 0

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
