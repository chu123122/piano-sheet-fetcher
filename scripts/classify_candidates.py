#!/usr/bin/env python3
"""Profile-agnostic rough classifier for piano-sheet-fetcher.

Owner: script. This step performs only deterministic, profile-independent work:

- hard gates: paid/store, private/group, failed probes;
- access gates: visible login-required files and manual/cloud actions;
- file/page coarse shape: downloaded artifact, likely file candidate, page;
- UNKNOWN isolation for rubric-based step7 adjudication.

It must not decide whether a candidate is successful for a target profile. That
decision belongs to step7 with profiles/<profile>.yaml.
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
from status_enum import normalize_status  # noqa: E402

PAID_RE = re.compile(r"\b(?:purchase|buy|paid|cart)\b|付费|购买|收费|有偿|購入|gumroad|patreon|ko-fi|booth|kokomu|mymusicsheet|mymusic\.st|piascore|thetapiano", re.I)
PRIVATE_RE = re.compile(r"私信|进群|加群|qq群|微信群|关注后|三连后|后台|私聊|dm me|discord", re.I)
LOGIN_RE = re.compile(r"login|required|sign in|会员|member|subscription|pro\+|account/login|登录|ログイン", re.I)
CLOUD_HOST_RE = re.compile(r"pan\.baidu\.com|pan\.quark\.cn|aliyundrive|alipan|lanzou|123pan|drive\.google\.com|dropbox\.com|1drv\.ms", re.I)
DIRECT_HOST_RE = re.compile(r"drive\.google\.com|github\.com|raw\.githubusercontent\.com|dropbox\.com|1drv\.ms|lanzou|123pan|mediafire", re.I)
SCORE_PLATFORM_RE = re.compile(r"sheet\.host|musescore\.com", re.I)
FILE_EXT_RE = re.compile(r"\.(?:pdf|mscz|mxl|musicxml|mid|midi|zip|rar|7z|jpg|jpeg|png|gp|gp3|gp4|gp5|gpx)(?:\b|$)", re.I)

TERMINAL_STATUS_MAP = {
    "downloaded": ("downloaded", 100, "already downloaded and signature-validated", False),
    "download_success": ("downloaded", 100, "already downloaded and signature-validated", False),
    "paid_or_store_excluded": ("paid_or_store_excluded", 5, "paid/store signal", False),
    "private_gate": ("private_gate", 5, "private message/group gate", False),
    "failed": ("failed", 0, "probe or download failed", False),
    "download_failed": ("failed", 0, "download failed", False),
    "probe_failed": ("failed", 0, "probe failed", False),
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
    for key in ["song", "title", "format", "access", "status", "classification", "evidence", "notes", "reason", "context", "url", "source_url", "file_kind", "file_name"]:
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
    for f in item.get("visible_files", []) if isinstance(item.get("visible_files"), list) else []:
        if isinstance(f, dict):
            parts.extend(str(f.get(k, "")) for k in ["name", "kind", "size", "access"])
    return "\n".join(parts)


def visible_file_kinds(item: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for f in item.get("visible_files", []) if isinstance(item.get("visible_files"), list) else []:
        if isinstance(f, dict):
            kind = str(f.get("kind", "")).lower().lstrip(".")
            name = str(f.get("name", "")).lower()
            if kind:
                kinds.append(kind)
            else:
                m = FILE_EXT_RE.search(name)
                if m:
                    kinds.append(m.group(0).lstrip("."))
    return kinds


def classify(item: dict[str, Any], index: int) -> dict[str, Any]:
    text = text_of(item)
    url = str(item.get("url") or item.get("source_url") or "")
    host = urllib.parse.urlparse(url).netloc.lower() if url else ""
    incoming_status = str(item.get("status") or "")
    reasons: list[str] = []
    status = "UNKNOWN"
    score = 0
    needs_ai = True

    has_paid = bool(PAID_RE.search(text))
    has_private = bool(PRIVATE_RE.search(text))
    has_login = bool(LOGIN_RE.search(text))
    has_cloud = bool(CLOUD_HOST_RE.search(url))
    has_direct = bool(DIRECT_HOST_RE.search(url))
    has_platform = bool(SCORE_PLATFORM_RE.search(url))
    has_file_ext = bool(FILE_EXT_RE.search(text) or FILE_EXT_RE.search(url) or item.get("file_kind"))
    visible_kinds = visible_file_kinds(item)

    if incoming_status in TERMINAL_STATUS_MAP:
        status, score, reason, needs_ai = TERMINAL_STATUS_MAP[incoming_status]
        reasons.append(reason)
    elif has_paid:
        status = "paid_or_store_excluded"; score = 5; needs_ai = False; reasons.append("paid/store signal")
    elif has_private:
        status = "private_gate"; score = 5; needs_ai = False; reasons.append("private message/group gate")
    elif incoming_status in {"login_required_downloadable", "visible_score_files"} or (visible_kinds and has_login):
        status = "login_required_downloadable"; score = 75; needs_ai = True; reasons.append("visible files behind normal login/platform flow; profile success decided by rubric")
    elif incoming_status == "manual_action_required" or (has_cloud and not has_direct):
        status = "manual_action_required"; score = 45; needs_ai = True; reasons.append("normal manual/browser/client action required; profile success decided by rubric")
    elif incoming_status == "midi_only_auxiliary":
        status = "UNKNOWN"; score = 35; needs_ai = True; reasons.append("file candidate; target-profile success decided by rubric")
    elif has_file_ext and has_direct:
        status = "UNKNOWN"; score = 70; needs_ai = True; reasons.append("direct/file candidate; target-profile success decided by rubric")
    elif has_file_ext:
        status = "UNKNOWN"; score = 55; needs_ai = True; reasons.append("file-like evidence; target-profile success decided by rubric")
    elif has_platform or url:
        status = "page_candidate"; score = 30; needs_ai = True; reasons.append("page candidate; target-profile success decided by rubric")
    else:
        status = "UNKNOWN"; score = 0; needs_ai = True; reasons.append("insufficient evidence")

    status = normalize_status(status)

    evidence: list[str] = []
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
        "match": {"rubric": "UNKNOWN"},
        "reasons": reasons,
        "evidence": evidence,
        "needs_ai": needs_ai,
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Profile-agnostic rough classifier; emits UNKNOWN batch for rubric step7.")
    ap.add_argument("input_json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unknown-out", default="")
    args = ap.parse_args()

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
