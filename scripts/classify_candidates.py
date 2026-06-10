#!/usr/bin/env python3
"""Deterministic candidate classifier for piano-sheet-fetcher.

Owner: script. This is step 6 from design/AI与脚本职责划分.md.
It classifies candidates into success/auxiliary/exclude/UNKNOWN and writes the
small UNKNOWN batch that may be escalated to AI once.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from status_enum import CANONICAL_STATUSES, normalize_status  # noqa: E402

SUCCESS_FORMAT_RE = re.compile(r"\.?(pdf|mscz|mxl|musicxml)\b|五线谱|完整谱|full score|sheet music|score bundle|谱包", re.I)
AUX_FORMAT_RE = re.compile(r"\.?(mid|midi)\b", re.I)
EXCLUDE_RE = re.compile(r"简谱|数字谱|numbered|瀑布流|waterfall|synthesia|falling[- ]?note|tutorial|display[- ]?only|试听", re.I)
PRIVATE_RE = re.compile(r"私信|进群|加群|qq群|微信群|关注后|三连后|后台|私聊|dm me|discord", re.I)
PAID_RE = re.compile(r"\b(?:purchase|buy|paid|cart)\b|付费|购买|收费|有偿|購入|gumroad|patreon|ko-fi|booth|kokomu|mymusicsheet|mymusic\.st|piascore|thetapiano", re.I)
LOGIN_RE = re.compile(r"login|required|sign in|会员|member|subscription|pro\+|account/login|登录|ログイン", re.I)
DIRECT_HOST_RE = re.compile(r"drive\.google\.com|github\.com|raw\.githubusercontent\.com|dropbox\.com|1drv\.ms|lanzou|123pan|mediafire", re.I)
CLOUD_HOST_RE = re.compile(r"pan\.baidu\.com|pan\.quark\.cn|aliyundrive|alipan|lanzou|123pan|drive\.google\.com|dropbox\.com|1drv\.ms", re.I)
SCORE_PLATFORM_RE = re.compile(r"sheet\.host|musescore\.com", re.I)

TERMINAL_STATUS_MAP = {
    "downloaded": ("downloaded", "success", 100, "already downloaded and signature-validated"),
    "download_success": ("downloaded", "success", 100, "already downloaded and signature-validated"),
    "login_required_downloadable": ("login_required_downloadable", "success", 84, "visible score file requires normal login/platform flow"),
    "visible_score_files": ("login_required_downloadable", "success", 82, "visible score file requires normal login/platform flow"),
    "paid_or_store_excluded": ("paid_or_store_excluded", "exclude", 5, "paid/store signal"),
    "private_gate": ("private_gate", "exclude", 5, "private message/group gate"),
    "not_full_piano_score": ("not_full_piano_score", "exclude", 10, "not a complete piano score"),
    "midi_only_auxiliary": ("midi_only_auxiliary", "auxiliary", 35, "MIDI only"),
    "failed": ("failed", "exclude", 0, "probe or download failed"),
    "download_failed": ("failed", "exclude", 0, "download failed"),
    "probe_failed": ("failed", "exclude", 0, "probe failed"),
}


def flatten_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        if isinstance(payload.get("candidates"), list):
            return [x for x in payload["candidates"] if isinstance(x, dict)]
        if isinstance(payload.get("records"), list):
            return [x for x in payload["records"] if isinstance(x, dict)]
        if isinstance(payload.get("results"), list):
            out: list[dict[str, Any]] = []
            for row in payload["results"]:
                if isinstance(row, dict) and isinstance(row.get("records"), list):
                    out.extend(x for x in row["records"] if isinstance(x, dict))
                elif isinstance(row, dict):
                    out.append(row)
            return out
    raise SystemExit("input JSON must be an array or contain candidates/records/results")


def text_of(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["song", "title", "format", "access", "status", "classification", "evidence", "notes", "reason", "context", "url", "source_url"]:
        val = item.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
        else:
            parts.append(str(val))
    for link in item.get("links", []) if isinstance(item.get("links"), list) else []:
        if isinstance(link, dict):
            parts.extend(str(link.get(k, "")) for k in ["url", "context", "kind", "access_code"])
        else:
            parts.append(str(link))
    return "\n".join(parts)


def classify(item: dict[str, Any], index: int) -> dict[str, Any]:
    text = text_of(item)
    url = str(item.get("url") or item.get("source_url") or "")
    host = urllib.parse.urlparse(url).netloc.lower() if url else ""
    reasons: list[str] = []
    status = "UNKNOWN"
    profile = "UNKNOWN"
    score = 0

    has_success_format = bool(SUCCESS_FORMAT_RE.search(text))
    has_aux_format = bool(AUX_FORMAT_RE.search(text))
    has_exclude = bool(EXCLUDE_RE.search(text))
    has_paid = bool(PAID_RE.search(text))
    has_private = bool(PRIVATE_RE.search(text))
    has_login = bool(LOGIN_RE.search(text))
    has_direct_host = bool(DIRECT_HOST_RE.search(url))
    has_cloud = bool(CLOUD_HOST_RE.search(url))
    has_score_platform = bool(SCORE_PLATFORM_RE.search(url))
    incoming_status = str(item.get("status") or "")
    visible_files = item.get("visible_files") if isinstance(item.get("visible_files"), list) else []

    if incoming_status in TERMINAL_STATUS_MAP:
        status, profile, score, reason = TERMINAL_STATUS_MAP[incoming_status]
        reasons.append(reason)
    elif visible_files and any(str(f.get("kind", "")).lower() in {"pdf", "mscz", "mxl", "musicxml", "zip"} for f in visible_files if isinstance(f, dict)):
        status = "login_required_downloadable"; profile = "success"; score = 84; reasons.append("visible full-score file listed")
    elif has_paid:
        status = "paid_or_store_excluded"; profile = "exclude"; score = 5; reasons.append("paid/store signal")
    elif has_private:
        status = "private_gate"; profile = "exclude"; score = 5; reasons.append("private message/group gate")
    elif has_exclude and not has_success_format:
        status = "not_full_piano_score"; profile = "exclude"; score = 10; reasons.append("numbered/waterfall/display-only signal without full-score format")
    elif has_login and has_success_format:
        status = "login_required_downloadable"; profile = "success"; score = 82; reasons.append("full-score file visible but login-gated")
    elif has_score_platform and has_success_format:
        status = "page_candidate"; profile = "UNKNOWN"; score = 55; reasons.append("score platform page with full-score evidence; downloadability not confirmed")
    elif has_direct_host and has_success_format:
        status = "download_candidate"; profile = "success"; score = 95; reasons.append("public/direct host plus full-score format")
    elif has_cloud and has_success_format:
        status = "manual_action_required"; profile = "success"; score = 80; reasons.append("cloud share plus full-score format requires normal browser/client flow")
    elif has_aux_format and not has_success_format:
        status = "midi_only_auxiliary"; profile = "auxiliary"; score = 35; reasons.append("MIDI only")
    elif has_success_format and has_exclude:
        status = "UNKNOWN"; profile = "UNKNOWN"; score = 50; reasons.append("conflict: full-score and exclusion signals")
    elif has_success_format:
        status = "UNKNOWN"; profile = "UNKNOWN"; score = 55; reasons.append("full-score signal without access/source certainty")
    elif url:
        status = "page_candidate"; profile = "UNKNOWN"; score = 30; reasons.append("page link without enough score/access evidence")
    else:
        status = "not_full_piano_score"; profile = "exclude"; score = 0; reasons.append("no URL or score evidence")

    status = normalize_status(status)

    evidence = []
    compact = re.sub(r"\s+", " ", text).strip()
    if compact:
        evidence.append(compact[:500])
    if item.get("evidence") and str(item.get("evidence")) not in evidence:
        evidence.append(str(item.get("evidence"))[:500])

    out = dict(item)
    out.update({
        "candidate_id": item.get("candidate_id") or f"c{index:04d}",
        "status": status,
        "score": score,
        "match": {"piano_score": profile},
        "reasons": reasons,
        "evidence": evidence,
        "needs_ai": profile == "UNKNOWN" or status == "UNKNOWN",
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify score candidates deterministically and emit UNKNOWN batch for optional AI adjudication.")
    ap.add_argument("input_json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unknown-out", default="")
    args = ap.parse_args()
    _ = CANONICAL_STATUSES

    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    items = flatten_payload(payload)
    classified = [classify(item, i) for i, item in enumerate(items, 1)]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(classified, ensure_ascii=False, indent=2), encoding="utf-8")
    unknown = [x for x in classified if x.get("needs_ai")]
    if args.unknown_out:
        unknown_path = Path(args.unknown_out)
        unknown_path.parent.mkdir(parents=True, exist_ok=True)
        unknown_path.write_text(json.dumps(unknown, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)
    if args.unknown_out:
        print(args.unknown_out)
    return 0

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
