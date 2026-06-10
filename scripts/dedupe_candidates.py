#!/usr/bin/env python3
"""Dedupe, rank, and render classified score candidates.

Owner: script. This covers step 8 and the pure-render part of step 11 from
AI与脚本职责划分.md. It performs no semantic AI judgement.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any


def canonical_url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    url = url.replace("\\/", "/").replace("\\u0026", "&").replace("\\u003d", "=")
    p = urllib.parse.urlparse(url)
    if p.netloc.endswith("youtube.com") and p.path.startswith("/redirect"):
        q = urllib.parse.parse_qs(p.query).get("q")
        if q:
            return canonical_url(q[0])
    query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    keep = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    netloc = p.netloc.lower()
    path = re.sub(r"/+$", "", urllib.parse.unquote(p.path))
    return urllib.parse.urlunparse((p.scheme.lower() or "https", netloc, path, "", urllib.parse.urlencode(keep), ""))


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in items:
        key = canonical_url(str(item.get("url") or item.get("source_url") or item.get("local_path") or item.get("candidate_id") or ""))
        if not key:
            key = str(item.get("candidate_id") or len(best))
        item = dict(item)
        item["canonical_url"] = key
        old = best.get(key)
        if old is None or int(item.get("score") or 0) > int(old.get("score") or 0):
            item["duplicate_count"] = int(old.get("duplicate_count", 1)) + 1 if old else 1
            best[key] = item
        else:
            old["duplicate_count"] = int(old.get("duplicate_count", 1)) + 1
    return sorted(best.values(), key=lambda x: (int(x.get("score") or 0), int(x.get("duplicate_count") or 1)), reverse=True)


def render_markdown(items: list[dict[str, Any]], title: str) -> str:
    counts = Counter(str(x.get("status", "")) for x in items)
    lines = [f"# Candidate Report: {title}", "", f"- Candidates after dedupe: {len(items)}", "", "## Status counts", ""]
    for k, v in sorted(counts.items()):
        lines.append(f"- {k or 'unknown'}: {v}")
    lines += ["", "## Ranked candidates", ""]
    for i, item in enumerate(items, 1):
        match = item.get("match") if isinstance(item.get("match"), dict) else {}
        lines += [
            f"### {i}. {item.get('title') or item.get('url') or item.get('candidate_id')}",
            "",
            f"- URL: {item.get('url','')}",
            f"- Canonical: {item.get('canonical_url','')}",
            f"- Status: {item.get('status','')}",
            f"- Match[piano_score]: {match.get('piano_score','')}",
            f"- Score: {item.get('score','')}",
            f"- Needs AI: {item.get('needs_ai', False)}",
            f"- Reasons: {'; '.join(str(x) for x in item.get('reasons', []))}",
            f"- Evidence: {' | '.join(str(x) for x in item.get('evidence', [])[:2])}",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Dedupe/rank classified candidates and optionally render a markdown report.")
    ap.add_argument("input_json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--title", default="score candidates")
    args = ap.parse_args()
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("input JSON must be a classified candidate array")
    ranked = dedupe([x for x in payload if isinstance(x, dict)])
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report:
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(render_markdown(ranked, args.title), encoding="utf-8")
    print(out_path)
    if args.report:
        print(args.report)
    return 0

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
