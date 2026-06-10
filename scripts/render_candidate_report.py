#!/usr/bin/env python3
"""Render a local audit report for already-classified piano-sheet candidates.

This is intentionally a pure renderer. It must not classify, reclassify, or
override candidate status; use classify_candidates.py + dedupe_candidates.py for
deterministic status/score ownership.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import urllib.parse
from collections import defaultdict
from pathlib import Path
from typing import Any


def slugify(value: str, max_len: int = 80) -> str:
    value = urllib.parse.unquote(value)
    value = re.sub(r"[^\w._ -]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", "-", value.strip().lower())
    value = value.strip("-._")
    return (value[:max_len].strip("-._") or "score")


def render_status(item: dict[str, Any]) -> tuple[str, str, int]:
    """Return existing status/action/score without adding judgement."""
    status = str(item.get("status") or "UNKNOWN")
    raw_reasons = item.get("reasons")
    if isinstance(raw_reasons, list):
        reasons = "; ".join(str(x) for x in raw_reasons)
    else:
        reasons = str(raw_reasons or "")
    action = str(item.get("action") or item.get("recommended_action") or reasons or "see source evidence; no renderer judgement")
    try:
        rank = int(item.get("score", item.get("rank", 0)) or 0)
    except (TypeError, ValueError):
        rank = 0
    return status, action, rank


def render_song(song: str, items: list[dict[str, Any]], out_root: Path) -> Path:
    out_dir = out_root / slugify(song)
    out_dir.mkdir(parents=True, exist_ok=True)

    enriched: list[dict[str, Any]] = []
    for item in items:
        status, action, rank = render_status(item)
        enriched.append({**item, "status": status, "action": action, "rank": rank})
    enriched.sort(key=lambda item: item["rank"], reverse=True)

    lines = [
        f"# Candidate Report: {song}",
        "",
        f"- Generated UTC: {_dt.datetime.now(_dt.timezone.utc).isoformat()}",
        f"- Candidates: {len(enriched)}",
        "- Full-score downloads in this run: 0",
        "",
        "## Summary",
        "",
    ]
    counts: dict[str, int] = defaultdict(int)
    for item in enriched:
        counts[item["status"]] += 1
    for status, count in sorted(counts.items()):
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Candidates", ""])

    for index, item in enumerate(enriched, 1):
        lines.extend(
            [
                f"### {index}. {item.get('title', item.get('url', 'candidate'))}",
                "",
                f"- URL: {item.get('url', '')}",
                f"- Channel: {item.get('channel', '')}",
                f"- Status: {item['status']}",
                f"- Action: {item['action']}",
                f"- Format: {item.get('format', '')}",
                f"- Access: {item.get('access', '')}",
                f"- Query: {item.get('query', '')}",
                f"- Upstream: {item.get('upstream', '')}",
                f"- Next probe: {item.get('next_probe', '')}",
                f"- Evidence: {item.get('evidence', '')}",
                f"- Notes: {item.get('notes', '')}",
                "",
            ]
        )

    lines.extend(
        [
            "## Problems And Fixes",
            "",
            "- This report only renders statuses already present in the input.",
            "- Classification ownership belongs to `classify_candidates.py`; dedupe/ranking ownership belongs to `dedupe_candidates.py`.",
            "- If this report shows `UNKNOWN`, run the step7 adjudication path or improve the deterministic classifier; do not patch renderer logic.",
            "",
        ]
    )

    report = out_dir / "CANDIDATE_REPORT.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Pure-render candidate audit reports by song; does not classify.")
    parser.add_argument("input_json", help="JSON array of candidate records")
    parser.add_argument("--out", default="sheet-music", help="Output root")
    args = parser.parse_args()

    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    by_song: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in payload:
        song = str(item.get("song") or item.get("query_title") or item.get("source_title") or item.get("title") or "candidates")
        by_song[song].append(item)

    for song, items in by_song.items():
        print(render_song(song, items, Path(args.out)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
