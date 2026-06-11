#!/usr/bin/env python3
"""Find complete piano score files for one song and save actionable results.

Product entry point for piano-sheet-fetcher. It turns the multi-source lessons
from Phase 2a into a stable user-facing workflow:

- search public video/web sources for score links
- download only public direct score artifacts
- validate file signatures before success
- list login-gated downloadable score pages separately
- exclude paid/store/private-gated resources from downloads

No login bypass, no paid download, no CAPTCHA/client automation.
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
import urllib.parse
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from classify_candidates import classify as classify_candidate  # noqa: E402
from dedupe_candidates import dedupe as dedupe_candidates  # noqa: E402
from bilibili_full_score_hunt import (  # noqa: E402
    extract_links as extract_bilibili_links,
    search_bilibili as search_bilibili_videos,
    video_url as bilibili_video_url,
)
from status_enum import normalize_status  # noqa: E402
from profile_utils import (  # noqa: E402
    load_profile,
    profile_auxiliary_formats,
    profile_names,
    profile_rubric_text,
    profile_success_formats,
    profile_target_kinds,
    profile_target_suffixes,
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
SCORE_EXTS = {".pdf", ".mscz", ".mxl", ".musicxml", ".mid", ".midi", ".zip", ".rar", ".7z"}
SUCCESS_EXTS = {".pdf", ".mscz", ".mxl", ".musicxml"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
PAID_HOST_HINTS = ["mymusicsheet", "mymusic.st", "kokomu", "piascore", "gumroad", "patreon", "ko-fi", "booth.pm", "theta", "thetapiano"]
STORE_WORDS = re.compile(r"\b(?:purchase|buy|paid|cart)\b|gumroad|patreon|ko-fi|mymusicsheet|kokomu|piascore|booth|購入|有料|付费|购买|收费|thetapiano", re.I)
GOOD_WORDS = re.compile(r"pdf|mscz|mxl|musicxml|sheet|score|楽譜|乐谱|鋼琴譜|钢琴谱|五线谱", re.I)
URL_RE = re.compile(r"https?://[^\s<>'\"，。；、）)\]}]+", re.I)
KEEP_HOST_HINTS = [
    "drive.google", "dropbox", "1drv.ms", "onedrive", "mega.nz", "mediafire",
    "github.com", "raw.githubusercontent", "sheet.host", "musescore.com",
    "bilibili.com", "pan.baidu.com", "pan.quark.cn", "aliyundrive", "alipan",
    "pianoscoreone", "blogspot", "wordpress", "gumroad", "patreon", "ko-fi",
    "mymusicsheet", "mymusic.st", "kokomu", "piascore", "booth.pm",
]
KNOWN_ALIASES_PATH = SCRIPT_DIR.parent / "references" / "known_aliases.json"


@dataclass
class Song:
    title: str
    artist: str = ""
    aliases: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        base = f"{self.title}-{self.artist}" if self.artist else self.title
        return slugify(base, 90)


@dataclass
class Record:
    url: str
    status: str
    classification: str
    channel: str
    source_url: str = ""
    source_title: str = ""
    platform: str = ""
    visible_files: list[dict[str, str]] = field(default_factory=list)
    local_path: str = ""
    file_name: str = ""
    file_kind: str = ""
    bytes: int | None = None
    sha256: str = ""
    archive_files: list[dict[str, Any]] = field(default_factory=list)
    evidence: str = ""
    error: str = ""


@dataclass(frozen=True)
class SourceContext:
    song: Song
    out_dir: Path
    youtube_limit: int
    max_videos: int
    web_limit: int
    target_profile: str = "piano_score"
    profile: dict[str, Any] = field(default_factory=dict)
    text_inputs: list[str] = field(default_factory=list)


SourceFn = Callable[[SourceContext], tuple[list[Record], list[dict[str, Any]]]]


@dataclass(frozen=True)
class SourcePlugin:
    name: str
    discover: SourceFn


def request(url: str, timeout: int = 30) -> tuple[int, str, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,ja;q=0.7"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.headers.get("Content-Type", ""), r.read()


def text_request(url: str, timeout: int = 30) -> tuple[int, str, str]:
    status, final, _ctype, data = request(url, timeout)
    return status, final, data.decode("utf-8", errors="replace")


def slugify(s: str, max_len: int = 80) -> str:
    s = urllib.parse.unquote(str(s))
    s = re.sub(r"[^\w._ -]+", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip().lower()).strip("-._")
    return (s[:max_len].strip("-._") or "score")


def clean_url(url: str) -> str:
    url = html.unescape(str(url).replace("\\/", "/"))
    url = url.replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u002F", "/")
    url = re.split(r"(?:\\n|\\r|\\t|\\\\n|\\\\r|\\\\t|\\u003c|\\u003e|[<>])", url, 1)[0]
    return url.rstrip(".,;:!?)]}")


def normalize_external_url(url: str) -> str:
    url = clean_url(url)
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return ""
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


def canonical_url(url: str) -> str:
    url = normalize_external_url(url)
    if not url:
        return ""
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return ""
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    keep = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urllib.parse.urlunparse((p.scheme.lower() or "https", p.netloc.lower(), re.sub(r"/+$", "", p.path), "", urllib.parse.urlencode(keep), ""))


def target_terms(song: Song) -> list[str]:
    return [song.title, song.artist, *song.aliases]


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value).strip()
        key = re.sub(r"\s+", "", item).lower()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def load_known_aliases(title: str, artist: str = "") -> list[str]:
    """Load deterministic cached aliases without binding the CLI to one song."""
    if not KNOWN_ALIASES_PATH.exists():
        return []
    try:
        data = json.loads(KNOWN_ALIASES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    aliases: list[str] = []
    for key in [title, artist]:
        if key and isinstance(data.get(key), list):
            aliases.extend(str(x) for x in data[key])
    folded_title = re.sub(r"\s+", "", title).lower()
    for key, vals in data.items():
        folded_key = re.sub(r"\s+", "", str(key)).lower()
        if folded_key and folded_key in folded_title and isinstance(vals, list):
            aliases.extend(str(x) for x in vals)
    return ordered_unique(aliases)


def target_matches(song: Song, text: str) -> bool:
    folded = re.sub(r"\s+", "", text).lower()
    for term in target_terms(song):
        t = re.sub(r"\s+", "", str(term)).lower()
        if t and t in folded:
            return True
    return False


def youtube_search(query: str, limit: int) -> tuple[list[str], str]:
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    ids: list[str] = []
    for vid in re.findall(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', raw) + re.findall(r"watch\?v=([A-Za-z0-9_-]{11})", raw):
        if vid not in ids:
            ids.append(vid)
    return [f"https://www.youtube.com/watch?v={v}" for v in ids[:limit]], f"HTTP {status}"


def bing_search(query: str, limit: int) -> tuple[list[str], str]:
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    urls: list[str] = []
    # Bing result markup varies; keep this conservative. YouTube usually carries
    # most useful creator links, so search failure is not fatal.
    patterns = [r'<a\s+href="(https?://[^"]+)"', r'"url"\s*:\s*"(https?://[^"]+)"']
    for pat in patterns:
        for m in re.finditer(pat, raw):
            u = normalize_external_url(m.group(1))
            h = host(u)
            if not h or any(skip in h for skip in ["microsoft", "bing.com", "go.microsoft"]):
                continue
            if u not in urls:
                urls.append(u)
            if len(urls) >= limit:
                return urls, f"HTTP {status}"
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
    links: list[str] = []
    blob = raw.replace("\\/", "/") + "\n" + desc
    for m in URL_RE.finditer(blob):
        u = normalize_external_url(m.group(0))
        h = host(u)
        if not h:
            continue
        suffix = Path(urllib.parse.urlparse(u).path).suffix.lower()
        keep = any(k in h for k in KEEP_HOST_HINTS) or suffix in SCORE_EXTS or bool(STORE_WORDS.search(u))
        if keep and u not in links:
            links.append(u)
    return title, desc, links[:60], f"HTTP {status}"


def extract_scoreish_links(text: str) -> list[str]:
    """Extract public score-ish links from pasted text or source metadata."""
    out: list[str] = []
    for m in URL_RE.finditer(text):
        u = normalize_external_url(m.group(0))
        h = host(u)
        if not h:
            continue
        suffix = Path(urllib.parse.urlparse(u).path).suffix.lower()
        keep = any(k in h for k in KEEP_HOST_HINTS) or suffix in SCORE_EXTS or bool(STORE_WORDS.search(u))
        if keep and u not in out:
            out.append(u)
    return out


def google_drive_file_id(url: str) -> str:
    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (q.get("id") or [""])[0]


def file_kind(data: bytes, final_url: str, ctype: str) -> str:
    suffix = Path(urllib.parse.urlparse(final_url).path).suffix.lower()
    base = ctype.lower().split(";", 1)[0].strip()
    if data.startswith(b"%PDF") or suffix == ".pdf" or base == "application/pdf":
        return "pdf"
    if data.startswith(b"PK\x03\x04") or suffix in {".zip", ".mscz", ".mxl"} or base == "application/zip":
        return suffix.lstrip(".") if suffix in {".mscz", ".mxl"} else "zip"
    if suffix in {".musicxml"}:
        return "musicxml"
    if suffix in {".mid", ".midi"} or base in {"audio/midi", "audio/x-midi"}:
        return "midi"
    if data.startswith(b"Rar!\x1a\x07") or suffix == ".rar":
        return "rar"
    if data.startswith(b"7z\xbc\xaf\x27\x1c") or suffix == ".7z":
        return "7z"
    return "unknown"


def target_file_kinds(profile: dict[str, Any]) -> set[str]:
    return profile_target_kinds(profile)


def is_success_kind(kind: str, profile: dict[str, Any]) -> bool:
    return kind in target_file_kinds(profile)


def archive_target_suffixes(profile: dict[str, Any]) -> set[str]:
    return profile_target_suffixes(profile)


def safe_archive_name(name: str) -> str:
    name = name.replace("\\", "/")
    parts = [p for p in name.split("/") if p not in {"", ".", ".."}]
    return slugify("-".join(parts), 120)


def extract_zip_targets(zip_path: Path, profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Safely extract target score files from a ZIP archive.

    Only profile-target files are extracted. Path traversal and huge entries are
    skipped. Nested archives are recorded but not recursively expanded.
    """
    targets = archive_target_suffixes(profile)
    extracted_dir = zip_path.with_suffix(zip_path.suffix + ".extracted")
    extracted: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            member = info.filename.replace("\\", "/")
            suffix = Path(member).suffix.lower()
            if suffix not in targets:
                continue
            if member.startswith("/") or ".." in member.split("/"):
                extracted.append({"name": info.filename, "status": "skipped_unsafe_path"})
                continue
            if info.file_size > 100 * 1024 * 1024:
                extracted.append({"name": info.filename, "status": "skipped_too_large", "bytes": info.file_size})
                continue
            extracted_dir.mkdir(parents=True, exist_ok=True)
            out_name = safe_archive_name(member)
            if not out_name.lower().endswith(suffix):
                out_name += suffix
            out_path = extracted_dir / out_name
            n = 2
            while out_path.exists():
                out_path = extracted_dir / f"{Path(out_name).stem}-{n}{suffix}"
                n += 1
            with zf.open(info) as src:
                data = src.read()
            out_path.write_bytes(data)
            extracted.append({
                "name": info.filename,
                "kind": suffix.lstrip(".") if suffix not in {".mid", ".midi"} else "midi",
                "bytes": len(data),
                "local_path": str(out_path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "status": "extracted",
            })
    return extracted


def write_artifact(song: Song, data: bytes, final_url: str, source_url: str, out_dir: Path, channel: str, kind: str) -> tuple[Path, str]:
    downloads = out_dir / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    suffix_map = {"pdf": ".pdf", "mscz": ".mscz", "mxl": ".mxl", "musicxml": ".musicxml", "zip": ".zip", "midi": ".mid", "rar": ".rar", "7z": ".7z"}
    suffix = suffix_map.get(kind, ".bin")
    stem = slugify(f"{song.title}-{channel}-{Path(urllib.parse.urlparse(final_url).path).stem or 'download'}", 90)
    path = downloads / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = downloads / f"{stem}-{n}{suffix}"
        n += 1
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    meta = {
        "song": song.title,
        "artist": song.artist,
        "channel": channel,
        "source_url": source_url,
        "final_url": final_url,
        "file_kind": kind,
        "bytes": len(data),
        "sha256": sha,
        "downloaded_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "policy_note": "Public non-paid artifact fetched without bypassing login/pay/CAPTCHA/client gates; verify rights before redistribution.",
    }
    path.with_suffix(path.suffix + ".source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path, sha


def try_download(song: Song, url: str, source_url: str, source_title: str, channel: str, out_dir: Path, profile: dict[str, Any]) -> Record:
    rec = Record(url=url, channel=channel, source_url=source_url, source_title=source_title, status="candidate", classification="direct_or_cloud", platform=host(url))
    h = host(url)
    download_url = url
    if "drive.google.com" in h:
        fid = google_drive_file_id(url)
        if not fid:
            rec.status = "manual_action_required"
            rec.classification = "cloud_folder_or_unknown"
            rec.evidence = "Google Drive folder or URL without file id; manual browser action needed."
            return rec
        download_url = "https://drive.google.com/uc?export=download&id=" + urllib.parse.quote(fid)
        rec.classification = "google_drive_public_file"
    try:
        status, final, ctype, data = request(download_url, timeout=45)
        kind = file_kind(data, final, ctype)
        if kind == "zip":
            path, sha = write_artifact(song, data, final, source_url, out_dir, channel, kind)
            rec.local_path = str(path)
            rec.file_name = path.name
            rec.file_kind = kind
            rec.bytes = len(data)
            rec.sha256 = sha
            try:
                extracted = extract_zip_targets(path, profile)
            except Exception as exc:
                rec.status = "manual_action_required"
                rec.classification = "archive_unreadable"
                rec.evidence = f"Downloaded ZIP but could not inspect safely: {type(exc).__name__}: {exc}"
                return rec
            rec.archive_files = extracted
            good = [x for x in extracted if x.get("status") == "extracted"]
            if good:
                rec.status = "downloaded"
                rec.classification = f"{profile.get('profile', 'target')}_archive"
                rec.evidence = f"Downloaded ZIP and extracted {len(good)} target file(s)."
            else:
                rec.status = "manual_action_required"
                rec.classification = "archive_no_target_files"
                rec.evidence = f"Downloaded ZIP but no {profile.get('profile', 'target')} target files were found inside."
            return rec
        if kind in {"rar", "7z"}:
            path, sha = write_artifact(song, data, final, source_url, out_dir, channel, kind)
            rec.status = "manual_action_required"
            rec.classification = "unsupported_archive"
            rec.local_path = str(path)
            rec.file_name = path.name
            rec.file_kind = kind
            rec.bytes = len(data)
            rec.sha256 = sha
            rec.evidence = f"Downloaded {kind} archive; extraction requires external tool, not counted as success."
            return rec
        if not is_success_kind(kind, profile):
            rec.status = "manual_action_required"
            rec.classification = "non_direct_or_untrusted_artifact"
            rec.file_kind = kind
            if kind in profile_auxiliary_formats(profile):
                rec.status = "midi_only_auxiliary" if kind in {"mid", "midi"} else "page_candidate"
                rec.classification = "auxiliary_only"
            rec.evidence = f"HTTP {status}; content-type={ctype}; file signature={kind}; target_profile={profile.get('profile', '')}; not counted as target success."
            return rec
        path, sha = write_artifact(song, data, final, source_url, out_dir, channel, kind)
        rec.status = "downloaded"
        rec.classification = "full_score_file"
        rec.local_path = str(path)
        rec.file_name = path.name
        rec.file_kind = kind
        rec.bytes = len(data)
        rec.sha256 = sha
        rec.evidence = f"Downloaded {kind}; HTTP {status}; content-type={ctype}."
        return rec
    except Exception as e:
        rec.status = "failed"
        rec.error = f"{type(e).__name__}: {e}"
        return rec


def probe_sheethost(url: str, source_url: str = "", source_title: str = "") -> Record:
    rec = Record(url=url, channel="sheethost", source_url=source_url or url, source_title=source_title, status="candidate", classification="score_platform", platform="sheet.host")
    try:
        status, _final, raw = text_request(url)
    except Exception as e:
        rec.status = "failed"
        rec.error = f"{type(e).__name__}: {e}"
        return rec
    mt = re.search(r"<title[^>]*>([^<]+)", raw, re.I)
    if mt and not rec.source_title:
        rec.source_title = html.unescape(mt.group(1)).strip()
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)))
    visible: list[dict[str, str]] = []
    for m in re.finditer(r"(?:Download\s+)?([^()]{1,160}\.(?:pdf|mid|midi|mscz|mxl|musicxml|zip))\s*\(([^)]{1,40})\)", text, re.I):
        name = m.group(1).strip()
        if re.search(r"\bDownload\s+", name, re.I):
            name = re.split(r"\bDownload\s+", name, flags=re.I)[-1]
        if "-->" in name:
            name = name.rsplit("-->", 1)[-1]
        name = re.sub(r"^Download\s+", "", name, flags=re.I).strip()
        ext = Path(name).suffix.lower().lstrip(".")
        if not ext:
            continue
        visible.append({"name": name, "kind": ext, "size": m.group(2).strip(), "access": "login_required" if "account/login" in raw else "visible"})
    rec.visible_files = visible
    if visible and "account/login" in raw:
        rec.status = "login_required_downloadable"
        rec.classification = "full_score_login_gated"
        rec.evidence = "; ".join(f"{x['name']} ({x['size']})" for x in visible[:8])
    elif visible:
        rec.status = "login_required_downloadable"
        rec.classification = "full_score_visible"
        rec.evidence = "; ".join(f"{x['name']} ({x['size']})" for x in visible[:8])
    else:
        rec.status = "page_candidate"
        rec.evidence = text[:300]
    return rec


def classify_page(url: str, source_url: str, source_title: str, channel: str) -> Record:
    h = host(url)
    if any(p in h for p in PAID_HOST_HINTS) or STORE_WORDS.search(url):
        return Record(url=url, status="paid_or_store_excluded", classification="paid_or_store", channel=channel, source_url=source_url, source_title=source_title, platform=h)
    if "musescore.com" in h:
        return Record(url=url, status="page_candidate", classification="score_platform_page", channel=channel, source_url=source_url, source_title=source_title, platform=h, evidence="MuseScore score page candidate; download permission not confirmed.")
    return Record(url=url, status="page_candidate", classification="unprobed_page", channel=channel, source_url=source_url, source_title=source_title, platform=h)


def handle_link(song: Song, link: str, source_url: str, source_title: str, channel: str, out_dir: Path, profile: dict[str, Any]) -> Record | None:
    """Route one extracted link through deterministic handlers."""
    link = normalize_external_url(link)
    if not link:
        return None
    h = host(link)
    if "sheet.host" in h:
        return probe_sheethost(link, source_url, source_title)
    if "drive.google.com" in h or Path(urllib.parse.urlparse(link).path).suffix.lower() in SCORE_EXTS:
        return try_download(song, link, source_url, source_title, channel, out_dir, profile)
    return classify_page(link, source_url, source_title, channel)


def profile_search_terms(profile: dict[str, Any]) -> list[str]:
    terms = [str(x).strip() for x in profile.get("search_terms", []) if str(x).strip()]
    if terms:
        return ordered_unique(terms)
    return ordered_unique(sorted(profile_success_formats(profile)))


def build_queries(song: Song, profile: dict[str, Any] | None = None) -> list[str]:
    names = [song.title, *song.aliases]
    if song.artist:
        names.append(f"{song.title} {song.artist}")
    terms = profile_search_terms(profile or {}) or ["sheet music"]
    joined_terms = " ".join(terms[:4])
    first_term = terms[0]
    templates = [
        "{q} {terms}",
        "{q} {first} download",
        "{q} {first} file",
        "{q} sheet music download",
        "{q} site:sheet.host {first}",
        "{q} site:drive.google.com {first}",
    ]
    qs: list[str] = []
    for n in names:
        for t in templates:
            q = t.format(q=n, terms=joined_terms, first=first_term).strip()
            if q not in qs:
                qs.append(q)
    return qs


def build_bilibili_queries(song: Song) -> list[str]:
    names = [song.title, *song.aliases]
    if song.artist:
        names.append(f"{song.title} {song.artist}")
    templates = [
        "{q} 钢琴谱",
        "{q} 五线谱",
        "{q} 完整谱",
        "{q} 附谱",
        "{q} pdf mscz musicxml 网盘 提取码",
    ]
    qs: list[str] = []
    for n in names:
        for t in templates:
            q = t.format(q=n).strip()
            if q not in qs:
                qs.append(q)
    return qs


def discover_pasted_text(ctx: SourceContext) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, text in enumerate(ctx.text_inputs, 1):
        links = extract_scoreish_links(text)
        # Reuse Bilibili extractor to catch pan links/codes in Chinese text; URL
        # handling still goes through this entrypoint's deterministic handlers.
        for item in extract_bilibili_links(text):
            url = str(item.get("url") or "")
            if url and url not in links:
                links.append(url)
        logs.append({"channel": "pasted_text", "query": f"text_input_{index}", "status": "local_text", "found": len(links)})
        for link in links:
            key = canonical_url(link)
            if not key or key in seen:
                continue
            seen.add(key)
            rec = handle_link(ctx.song, link, f"pasted_text:{index}", "", "pasted_text", ctx.out_dir, ctx.profile)
            if rec:
                # Keep nearby text as evidence for manual/cloud candidates.
                if not rec.evidence:
                    rec.evidence = re.sub(r"\s+", " ", text).strip()[:500]
                records.append(rec)
    return records, logs


def discover_youtube(ctx: SourceContext) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen_videos: list[str] = []
    seen_urls: set[str] = set()
    for q in build_queries(ctx.song, ctx.profile):
        urls, status = youtube_search(q, ctx.youtube_limit)
        logs.append({"channel": "youtube_search", "query": q, "status": status, "found": len(urls)})
        for u in urls:
            if u not in seen_videos:
                seen_videos.append(u)
        time.sleep(0.15)

    for video_url in seen_videos[:ctx.max_videos]:
        title, desc, links, status = extract_youtube_metadata(video_url)
        logs.append({"channel": "youtube_video", "query": video_url, "status": status, "found": len(links), "title": title})
        text = "\n".join([title, desc, *links])
        if not links and not (target_matches(ctx.song, text) or GOOD_WORDS.search(text)):
            continue
        for link in links:
            key = canonical_url(link)
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            rec = handle_link(ctx.song, link, video_url, title, "youtube", ctx.out_dir, ctx.profile)
            if rec:
                records.append(rec)
        time.sleep(0.2)
    return records, logs


def discover_bilibili(ctx: SourceContext) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen_bvids: list[str] = []
    seen_urls: set[str] = set()
    for q in build_bilibili_queries(ctx.song):
        bvids, status = search_bilibili_videos(q, max_results=ctx.youtube_limit)
        logs.append({"channel": "bilibili_search", "query": q, "status": status, "found": len(bvids)})
        for bvid in bvids:
            if bvid not in seen_bvids:
                seen_bvids.append(bvid)
        time.sleep(0.15)
    for bvid in seen_bvids[:ctx.max_videos]:
        url = bilibili_video_url(bvid)
        try:
            _status, _final, raw = text_request(url)
        except Exception as e:
            logs.append({"channel": "bilibili_video", "query": url, "status": f"{type(e).__name__}: {e}", "found": 0})
            continue
        title = html.unescape((re.search(r"<title[^>]*>([^<]*)", raw, re.I) or ["", ""])[1])
        links = extract_scoreish_links(raw)
        for item in extract_bilibili_links(raw):
            link = str(item.get("url") or "")
            if link and link not in links:
                links.append(link)
        logs.append({"channel": "bilibili_video", "query": url, "status": "HTTP 200", "found": len(links), "title": title})
        for link in links:
            key = canonical_url(link)
            if not key or key in seen_urls:
                continue
            seen_urls.add(key)
            rec = handle_link(ctx.song, link, url, title, "bilibili", ctx.out_dir, ctx.profile)
            if rec:
                records.append(rec)
        time.sleep(0.2)
    return records, logs


def discover_github(ctx: SourceContext) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen: set[str] = set()
    queries = []
    for q in build_queries(ctx.song, ctx.profile):
        queries.append(f"{q} site:github.com")
        queries.append(f"{q} site:gist.github.com")
    for q in queries[:ctx.web_limit * 2]:
        urls, status = bing_search(q, 8)
        logs.append({"channel": "github_search", "query": q, "status": status, "found": len(urls)})
        for u in urls:
            h = host(u)
            if "github.com" not in h and "raw.githubusercontent.com" not in h:
                continue
            key = canonical_url(u)
            if not key or key in seen:
                continue
            seen.add(key)
            rec = handle_link(ctx.song, u, q, "", "github", ctx.out_dir, ctx.profile)
            if rec:
                records.append(rec)
        time.sleep(0.15)
    return records, logs


def discover_web(ctx: SourceContext) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for q in build_queries(ctx.song, ctx.profile)[:ctx.web_limit]:
        urls, status = bing_search(q, 8)
        logs.append({"channel": "web_search", "query": q, "status": status, "found": len(urls)})
        for u in urls:
            key = canonical_url(u)
            if not key or key in seen:
                continue
            seen.add(key)
            rec = handle_link(ctx.song, u, q, "", "web", ctx.out_dir, ctx.profile)
            if rec:
                records.append(rec)
        time.sleep(0.15)
    return records, logs


SOURCE_PLUGINS: dict[str, SourcePlugin] = {
    "pasted_text": SourcePlugin("pasted_text", discover_pasted_text),
    "youtube": SourcePlugin("youtube", discover_youtube),
    "bilibili": SourcePlugin("bilibili", discover_bilibili),
    "github": SourcePlugin("github", discover_github),
    "web": SourcePlugin("web", discover_web),
}


def selected_source_names(requested: list[str], has_text: bool, profile: dict[str, Any]) -> list[str]:
    preferred = [x for x in profile.get("preferred_channels", []) if x in SOURCE_PLUGINS]
    names = requested or ordered_unique([*preferred, "pasted_text", "github", "youtube", "web", "bilibili"])
    if not has_text:
        names = [x for x in names if x != "pasted_text"]
    if has_text and "pasted_text" not in names:
        names = ["pasted_text", *names]
    out: list[str] = []
    for name in names:
        if name not in SOURCE_PLUGINS:
            raise SystemExit(f"unknown source {name!r}; choose from {', '.join(SOURCE_PLUGINS)}")
        if name not in out:
            out.append(name)
    return out


def compact_evidence(item: dict[str, Any]) -> str:
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        return " | ".join(str(x) for x in evidence[:2])
    if evidence:
        return str(evidence)
    return "; ".join(str(x) for x in item.get("reasons", []))


def strong_record_count(records: list[Record]) -> int:
    return sum(1 for r in records if r.status in {"downloaded", "download_candidate", "login_required_downloadable"})


def discover(song: Song, out_dir: Path, youtube_limit: int, max_videos: int, web_limit: int, sources: list[str], text_inputs: list[str], target_profile: str, profile: dict[str, Any]) -> tuple[list[Record], list[dict[str, Any]]]:
    records: list[Record] = []
    logs: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    ctx = SourceContext(song=song, out_dir=out_dir, youtube_limit=youtube_limit, max_videos=max_videos, web_limit=web_limit, target_profile=target_profile, profile=profile, text_inputs=text_inputs)
    for source_name in sources:
        if strong_record_count(records) >= 5:
            logs.append({"channel": "early_stop", "query": source_name, "status": "skipped_after_5_strong_candidates", "found": len(records)})
            continue
        plugin = SOURCE_PLUGINS[source_name]
        source_records, source_logs = plugin.discover(ctx)
        logs.extend(source_logs)
        for rec in source_records:
            key = canonical_url(rec.url) or rec.url or rec.local_path
            # Cross-source dedupe happens again after classification, but this
            # cheap guard avoids repeated downloads/probes in a single run.
            if key and key in seen_urls:
                continue
            if key:
                seen_urls.add(key)
            records.append(rec)
    return records, logs


def load_adjudication(path: str) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("--adjudication-json must contain a JSON array")
    out: dict[str, dict[str, Any]] = {}
    for item in payload:
        if isinstance(item, dict) and item.get("candidate_id"):
            out[str(item["candidate_id"])] = item
    return out


def render_step7_prompt(target_profile: str, profile: dict[str, Any], unknown: list[dict[str, Any]]) -> str:
    template = (SCRIPT_DIR.parent / "templates" / "unknown_adjudication_prompt.md").read_text(encoding="utf-8")
    return "\n\n".join([
        template,
        "## Target profile rubric",
        profile_rubric_text(profile),
        "## UNKNOWN candidates",
        json.dumps(unknown, ensure_ascii=False, indent=2),
    ])


def apply_step7(classified: list[dict[str, Any]], target_profile: str, profile: dict[str, Any], unknown_policy: str, adjudication_json: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve UNKNOWN candidates after deterministic classification.

    If an adjudication JSON is provided, merge it by candidate_id. Otherwise the
    default conservative policy assigns UNKNOWN candidates to page_candidate so
    terminal reports have an explicit non-download action instead of a dangling
    runtime AI requirement.
    """
    original_unknown = [x for x in classified if x.get("needs_ai")]
    decisions = load_adjudication(adjudication_json)
    resolved: list[dict[str, Any]] = []
    for item in classified:
        item = dict(item)
        cid = str(item.get("candidate_id", ""))
        decision = decisions.get(cid)
        if decision:
            status = normalize_status(decision.get("status"), default="UNKNOWN")
            item["status"] = status
            item["match"] = decision.get("match") if isinstance(decision.get("match"), dict) else {target_profile: decision.get("match", "UNKNOWN")}
            item.setdefault("reasons", [])
            item["reasons"] = [*item.get("reasons", []), "step7 adjudication: " + str(decision.get("reason", ""))]
            item["needs_ai"] = item["status"] == "UNKNOWN" or "UNKNOWN" in [str(v) for v in item.get("match", {}).values()]
        elif item.get("needs_ai") and unknown_policy == "conservative":
            if item.get("status") not in {"login_required_downloadable", "manual_action_required", "private_gate", "paid_or_store_excluded", "failed"}:
                item["status"] = "page_candidate"
            item["match"] = {target_profile: "UNKNOWN"}
            item["score"] = min(int(item.get("score") or 0), 40)
            item["reasons"] = [*item.get("reasons", []), f"step7 conservative fallback: keep as {item.get('status')}, do not download"]
            item["needs_ai"] = False
        resolved.append(item)
    unresolved = [x for x in resolved if x.get("needs_ai")]
    step7_decisions = [
        {
            "candidate_id": x.get("candidate_id"),
            "status": x.get("status"),
            "match": x.get("match"),
            "reasons": x.get("reasons", [])[-2:],
        }
        for x in resolved
        if x.get("candidate_id") in {u.get("candidate_id") for u in original_unknown}
    ]
    return resolved, original_unknown if unknown_policy != "keep" or decisions else unresolved, step7_decisions


def classify_and_rank(out_dir: Path, records: list[Record], target_profile: str, profile: dict[str, Any], unknown_policy: str, adjudication_json: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = [asdict(r) for r in records]
    for item in raw:
        item["status"] = normalize_status(item.get("status"))
    (out_dir / "raw_candidates.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    classified = [classify_candidate(item, i, target_profile, profile) for i, item in enumerate(raw, 1)]
    original_unknown_preview = [x for x in classified if x.get("needs_ai")]
    (out_dir / "STEP7_PROMPT.md").write_text(render_step7_prompt(target_profile, profile, original_unknown_preview), encoding="utf-8")
    classified, original_unknown, step7_decisions = apply_step7(classified, target_profile, profile, unknown_policy, adjudication_json)
    unknown = [x for x in classified if x.get("needs_ai")]
    ranked = dedupe_candidates(classified)
    (out_dir / "classified.json").write_text(json.dumps(classified, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "unknown_candidates.original.json").write_text(json.dumps(original_unknown, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "unknown_candidates.json").write_text(json.dumps(unknown, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "STEP7_DECISIONS.json").write_text(json.dumps(step7_decisions, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "ranked.json").write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
    return raw, classified, unknown, ranked, step7_decisions


def write_outputs(song: Song, out_dir: Path, ranked: list[dict[str, Any]], unknown: list[dict[str, Any]], logs: list[dict[str, Any]], target_profile: str, step7_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = [r for r in ranked if r.get("status") == "downloaded" and r.get("local_path")]
    login_required = [
        r for r in ranked
        if r.get("status") == "login_required_downloadable"
        and (
            r.get("visible_files")
            or "sheet.host" in str(r.get("url", "")).lower()
        )
    ]
    manual = [r for r in ranked if r.get("status") == "manual_action_required"]
    excluded = [r for r in ranked if r.get("status") == "paid_or_store_excluded"]
    score_platform = [
        r for r in ranked
        if r.get("status") == "page_candidate"
        and "score_platform" in str(r.get("classification", "")).lower()
    ]
    failures = [r for r in ranked if r.get("status") == "failed"]
    result = {
        "song": asdict(song),
        "target_profile": target_profile,
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "result_contract": "Either downloaded_files contains local complete-score artifacts, or login_required_downloadables lists pages with visible downloadable score files requiring normal login/platform flow.",
        "unknown_candidates": unknown,
        "step7_decisions": step7_decisions,
        "downloaded_files": downloaded,
        "login_required_downloadables": login_required,
        "score_platform_candidates": score_platform,
        "manual_action_required": manual,
        "excluded_paid_or_store": excluded[:80],
        "failures": failures[:40],
        "all_records": ranked,
        "logs": logs,
    }
    (out_dir / "RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Piano Score Result: {song.title}", "", f"- Artist: {song.artist}", f"- Target profile: `{target_profile}`", f"- Output: `{out_dir}`", ""]
    lines += ["## Downloaded files", ""]
    if downloaded:
        for r in downloaded:
            lines += [f"- `{r.get('local_path')}`", f"  - kind: {r.get('file_kind')}; bytes: {r.get('bytes')}; sha256: `{r.get('sha256')}`", f"  - source: {r.get('source_url')}"]
            if r.get("archive_files"):
                for f in r.get("archive_files", []):
                    lines.append(f"  - archive file: `{f.get('name')}` -> `{f.get('local_path', '')}` ({f.get('status')})")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines += ["## Login-required downloadable candidates", ""]
    if login_required:
        for r in login_required:
            lines += [f"- {r.get('url')}", f"  - platform: {r.get('platform') or host(r.get('url',''))}", f"  - source: {r.get('source_url')}"]
            if r.get("visible_files"):
                for f in r.get("visible_files", []):
                    lines.append(f"  - file: `{f.get('name')}` ({f.get('size')}, {f.get('access')})")
            elif r.get("evidence"):
                lines.append(f"  - evidence: {r.get('evidence')}")
            lines.append("")
    else:
        lines.append("- none")
        lines.append("")

    lines += ["## Score platform candidates", ""]
    if score_platform:
        for r in score_platform[:20]:
            lines += [f"- {r.get('url')}", f"  - reason: {compact_evidence(r)}", f"  - source: {r.get('source_url')}", ""]
    else:
        lines.append("- none")
        lines.append("")

    lines += ["## Manual action / non-direct candidates", ""]
    if manual:
        for r in manual[:20]:
            lines += [f"- {r.get('url')}", f"  - reason: {compact_evidence(r) or r.get('classification')}", f"  - source: {r.get('source_url')}", ""]
    else:
        lines.append("- none")
        lines.append("")

    lines += ["## Excluded paid/store links", ""]
    if excluded:
        for r in excluded[:30]:
            lines.append(f"- {r.get('url')}")
        if len(excluded) > 30:
            lines.append(f"- ... {len(excluded) - 30} more in RESULT.json")
    else:
        lines.append("- none")
    lines.append("")

    lines += ["## Unknown candidates", ""]
    if unknown:
        lines.append(f"- {len(unknown)} candidates still require adjudication. See `unknown_candidates.json`.")
    else:
        lines.append("- none; step7 produced explicit fallback/adjudication decisions where needed.")
    lines.append("")

    lines += ["## Search log", ""]
    for log in logs:
        lines.append(f"- {log['channel']} `{log['query']}`: {log['status']}; found={log['found']}")
    (out_dir / "RESULT.md").write_text("\n".join(lines), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find public non-paid complete piano score files for one song.")
    ap.add_argument("song")
    ap.add_argument("--artist", default="")
    ap.add_argument("--alias", action="append", default=[])
    ap.add_argument("--out", default="sheet-music")
    ap.add_argument("--youtube-limit", type=int, default=18, help="Videos per YouTube query")
    ap.add_argument("--max-videos", type=int, default=100, help="Max unique YouTube/Bilibili videos to inspect")
    ap.add_argument("--web-limit", type=int, default=8, help="Number of web queries to run")
    ap.add_argument("--profile", choices=profile_names(), default="piano_score", help="Target profile loaded from profiles/*.yaml.")
    ap.add_argument("--unknown-policy", choices=["conservative", "keep"], default="conservative", help="Step7 fallback when no adjudication JSON is supplied.")
    ap.add_argument("--adjudication-json", default="", help="Optional JSON array of step7 AI/adjudicator decisions to merge by candidate_id.")
    ap.add_argument("--source", action="append", choices=sorted(SOURCE_PLUGINS), help="Source plugin to run. Repeatable. Default: youtube,bilibili,github,web; pasted_text is added when text is supplied.")
    ap.add_argument("--text", action="append", default=[], help="Public description/comment text to parse as a P0 source.")
    ap.add_argument("--text-file", action="append", default=[], help="UTF-8 text file containing public description/comment text to parse as a P0 source.")
    args = ap.parse_args(argv)

    title = args.song.strip()
    artist = args.artist.strip()
    aliases = ordered_unique([*load_known_aliases(title, artist), *[a.strip() for a in args.alias if a.strip()]])
    song = Song(title, artist, aliases)
    profile = load_profile(args.profile)
    root_name = song.id if args.profile == "piano_score" else f"{song.id}-{args.profile}"
    root = Path(args.out) / root_name
    text_inputs = [str(x) for x in args.text if str(x).strip()]
    for path in args.text_file:
        try:
            text_inputs.append(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            text_inputs.append(f"[failed to read {path}: {type(e).__name__}: {e}]")
    sources = selected_source_names(args.source or [], bool(text_inputs), profile)
    records, logs = discover(song, root, args.youtube_limit, args.max_videos, args.web_limit, sources, text_inputs, args.profile, profile)
    _raw, _classified, unknown, ranked, step7_decisions = classify_and_rank(root, records, args.profile, profile, args.unknown_policy, args.adjudication_json)
    result = write_outputs(song, root, ranked, unknown, logs, args.profile, step7_decisions)
    result["sources"] = sources
    (root / "RESULT.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(root / "RESULT.md")
    print(json.dumps({
        "sources": sources,
        "target_profile": args.profile,
        "downloaded_files": len(result["downloaded_files"]),
        "login_required_downloadables": len(result["login_required_downloadables"]),
        "manual_action_required": len(result["manual_action_required"]),
        "result_md": str(root / "RESULT.md"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
