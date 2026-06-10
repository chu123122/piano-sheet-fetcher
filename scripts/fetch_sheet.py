#!/usr/bin/env python3
"""Fetch legally accessible piano sheet artifacts from direct URLs or public catalogs.

This helper is intentionally conservative. It downloads direct score artifacts and writes
source metadata, but it does not bypass paywalls, logins, anti-bot flows, or
viewer-only pages.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


USER_AGENT = "CodexPianoSheetFetcher/1.0 (+local user initiated)"
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
SCORE_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "audio/midi",
    "audio/x-midi",
    "image/jpeg",
    "image/png",
}
BLOCKED_DOWNLOAD_HOST_RE = re.compile(
    r"pan\.baidu\.com|pan\.quark\.cn|aliyundrive|alipan|sheet\.host|musescore\.com|"
    r"mymusicsheet|mymusic\.st|kokomu|piascore|gumroad|patreon|ko-fi|booth",
    re.I,
)
EXT_PATTERN = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(SCORE_EXTENSIONS, key=len, reverse=True))
HTML_SCORE_RE = re.compile(r"""href=["']([^"']+\.(""" + EXT_PATTERN + r""")(?:\?[^"']*)?)["']""", re.I)


@dataclass
class Candidate:
    title: str
    url: str
    source: str
    note: str


def slugify(value: str, max_len: int = 80) -> str:
    value = urllib.parse.unquote(value)
    value = re.sub(r"[^\w._ -]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = value.strip("-._")
    return (value[:max_len].strip("-._") or "score")


def request_url(url: str, timeout: int = 30) -> tuple[bytes, str, str]:
    parsed = urllib.parse.urlparse(url)
    if BLOCKED_DOWNLOAD_HOST_RE.search(parsed.netloc):
        raise PermissionError(f"host is login/client/paid/platform gated; use probe/report flow, not direct fetch: {parsed.netloc}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
    return data, final_url, content_type


def is_score_artifact(data: bytes, url: str, content_type: str) -> bool:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    lowered_type = content_type.lower().split(";", 1)[0].strip()
    return (
        data.startswith(b"%PDF")
        or data.startswith(b"PK\x03\x04")
        or suffix in SCORE_EXTENSIONS
        or lowered_type in SCORE_CONTENT_TYPES
    )


def write_artifact(data: bytes, out_dir: Path, query: str, source_url: str, source: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(source_url)
    base = slugify(Path(parsed.path).name or query)
    suffix = Path(urllib.parse.urlparse(source_url).path).suffix.lower()
    if suffix not in SCORE_EXTENSIONS:
        suffix = ".pdf" if data.startswith(b"%PDF") else ".bin"
    if not base.lower().endswith(suffix):
        base += suffix
    artifact_path = out_dir / base
    if artifact_path.exists():
        stem = artifact_path.stem
        suffix = artifact_path.suffix
        counter = 2
        while artifact_path.exists():
            artifact_path = out_dir / f"{stem}-{counter}{suffix}"
            counter += 1
    artifact_path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    meta = {
        "query": query,
        "source": source,
        "source_url": source_url,
        "downloaded_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sha256": digest,
        "bytes": len(data),
        "policy_note": "Verify source-page rights before redistribution or reuse.",
    }
    artifact_path.with_suffix(artifact_path.suffix + ".source.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return artifact_path


def save_candidates(out_dir: Path, query: str, candidates: Iterable[Candidate]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates = list(candidates)
    lines = [
        f"# Search Results: {query}",
        "",
        "No direct score artifact was downloaded. Review rights and download permission before fetching manually.",
        "",
    ]
    if not candidates:
        lines.extend(
            [
                "No candidates were returned by the built-in public-catalog search.",
                "",
                "For modern J-pop/J-hop, continue with web/video/cloud-drive search and render a candidate report.",
                "",
            ]
        )
    for item in candidates:
        lines.extend(
            [
                f"## {item.title}",
                "",
                f"- Source: {item.source}",
                f"- URL: {item.url}",
                f"- Note: {item.note}",
                "",
            ]
        )
    path = out_dir / "SEARCH_RESULTS.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def resolve_artifact_from_page(page_url: str) -> Candidate | None:
    data, final_url, content_type = request_url(page_url)
    if is_score_artifact(data, final_url, content_type):
        return Candidate(
            Path(urllib.parse.urlparse(final_url).path).name,
            final_url,
            "direct",
            "Direct score artifact",
        )

    if b"<html" not in data[:2048].lower() and "text/html" not in content_type.lower():
        return None

    text = data.decode("utf-8", errors="replace")
    for match in HTML_SCORE_RE.finditer(text):
        artifact_url = urllib.parse.urljoin(final_url, html.unescape(match.group(1)))
        return Candidate(
            Path(urllib.parse.urlparse(artifact_url).path).name,
            artifact_url,
            "linked-score",
            "Score artifact linked from page",
        )
    return None


def search_imslp(query: str, limit: int) -> list[Candidate]:
    api = "https://imslp.org/api.php?" + urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": str(limit),
            "namespace": "0",
            "format": "json",
        }
    )
    try:
        data, _, _ = request_url(api)
        payload = json.loads(data.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return [Candidate("IMSLP search failed", api, "imslp", str(exc))]

    titles = payload[1] if len(payload) > 1 else []
    urls = payload[3] if len(payload) > 3 else []
    candidates: list[Candidate] = []
    for title, url in zip(titles, urls):
        candidates.append(
            Candidate(
                title=title,
                url=url,
                source="imslp",
                note="Candidate page. Verify public-domain status on the work/file page before downloading.",
            )
        )
    return candidates


def search_mutopia(query: str, limit: int) -> list[Candidate]:
    url = "https://www.mutopiaproject.org/cgibin/make-table.cgi?" + urllib.parse.urlencode(
        {"searchingfor": query}
    )
    try:
        data, final_url, _ = request_url(url)
    except (urllib.error.URLError, TimeoutError) as exc:
        return [Candidate("Mutopia search failed", url, "mutopia", str(exc))]

    text = data.decode("utf-8", errors="replace")
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for match in HTML_SCORE_RE.finditer(text):
        artifact_url = urllib.parse.urljoin(final_url, html.unescape(match.group(1)))
        if artifact_url in seen:
            continue
        seen.add(artifact_url)
        candidates.append(
            Candidate(
                title=Path(urllib.parse.urlparse(artifact_url).path).name,
                url=artifact_url,
                source="mutopia",
                note="Mutopia score candidate. Mutopia scores are generally freely licensed; verify page details.",
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def fetch_direct(query_or_url: str, out_dir: Path) -> Path | None:
    candidate = resolve_artifact_from_page(query_or_url)
    if not candidate:
        return None
    data, final_url, content_type = request_url(candidate.url)
    if not is_score_artifact(data, final_url, content_type):
        return None
    return write_artifact(data, out_dir, query_or_url, final_url, candidate.source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch legally accessible piano sheet artifacts.")
    parser.add_argument("query", help="Song/piece title or direct source URL")
    parser.add_argument("--out", default="sheet-music", help="Output directory")
    parser.add_argument("--limit", type=int, default=5, help="Maximum candidates per source")
    parser.add_argument("--dry-run", action="store_true", help="Only write candidate/search notes")
    args = parser.parse_args(argv)

    query = args.query.strip()
    out_dir = Path(args.out) / slugify(query)

    if urllib.parse.urlparse(query).scheme in {"http", "https", "file"} and not args.dry_run:
        try:
            pdf_path = fetch_direct(query, out_dir)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"direct fetch failed: {exc}", file=sys.stderr)
            return 2
        if pdf_path:
            print(pdf_path)
            return 0
        print("No direct score artifact found at the supplied URL.", file=sys.stderr)
        return 1

    candidates: list[Candidate] = []
    candidates.extend(search_mutopia(query, args.limit))
    candidates.extend(search_imslp(query, args.limit))

    for item in candidates:
        if args.dry_run:
            continue
        if Path(urllib.parse.urlparse(item.url).path).suffix.lower() in SCORE_EXTENSIONS:
            try:
                data, final_url, content_type = request_url(item.url)
                if is_score_artifact(data, final_url, content_type):
                    artifact_path = write_artifact(data, out_dir, query, final_url, item.source)
                    print(artifact_path)
                    return 0
            except (urllib.error.URLError, TimeoutError, OSError):
                continue

    results_path = save_candidates(out_dir, query, candidates)
    print(results_path)
    return 0 if candidates else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
