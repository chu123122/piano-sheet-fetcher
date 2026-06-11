#!/usr/bin/env python3
"""Config-driven deterministic classifier for piano-sheet-fetcher.

Owner: script. This step performs profile-independent hard gates first, then a
profile-configured format-level judgment by reading profiles/<profile>.yaml.
Only genuinely ambiguous page/text/conflict candidates are left for step7 AI.

Classifier rules must stay generic: do not add profile-name semantic branches.
Add or change a target by editing profiles/*.yaml.
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
from profile_utils import (  # noqa: E402
    load_profile,
    normalize_format,
    profile_auxiliary_formats,
    profile_names,
    profile_success_formats,
)
from status_enum import normalize_status  # noqa: E402

PAID_RE = re.compile(r"\b(?:purchase|buy|paid|cart)\b|付费|购买|收费|有偿|購入|有料|gumroad|patreon|ko-fi|booth|kokomu|mymusicsheet|mymusic\.st|piascore|thetapiano", re.I)
PRIVATE_RE = re.compile(r"私信|进群|加群|qq群|微信群|关注后|三连后|后台|私聊|dm me|discord", re.I)
LOGIN_RE = re.compile(r"login|required|sign in|会员|member|subscription|pro\+|account/login|登录|ログイン", re.I)
CLOUD_HOST_RE = re.compile(r"pan\.baidu\.com|pan\.quark\.cn|aliyundrive|alipan|lanzou|123pan|drive\.google\.com|dropbox\.com|1drv\.ms", re.I)
DIRECT_HOST_RE = re.compile(r"drive\.google\.com|github\.com|raw\.githubusercontent\.com|dropbox\.com|1drv\.ms|lanzou|123pan|mediafire", re.I)
SCORE_PLATFORM_RE = re.compile(r"sheet\.host|musescore\.com", re.I)
FILE_EXT_RE = re.compile(r"\.(pdf|mscz|mxl|musicxml|mid|midi|zip|rar|7z|jpg|jpeg|png|gp|gp3|gp4|gp5|gpx|html?)(?:\b|$)", re.I)
AUXILIARY_STATUS_FORMATS = {"mid", "midi"}

TERMINAL_STATUS_MAP = {
    "paid_or_store_excluded": ("paid_or_store_excluded", 5, "paid/store signal", False, "exclude"),
    "private_gate": ("private_gate", 5, "private message/group gate", False, "exclude"),
    "failed": ("failed", 0, "probe or download failed", False, "exclude"),
    "download_failed": ("failed", 0, "download failed", False, "exclude"),
    "probe_failed": ("failed", 0, "probe failed", False, "exclude"),
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


def _format_from_name(value: object) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    m = FILE_EXT_RE.search(value)
    if m:
        return normalize_format(m.group(1))
    return normalize_format(value) if re.fullmatch(r"[a-z0-9]+", value, re.I) else ""


def explicit_formats(item: dict[str, Any]) -> list[str]:
    """Return explicit file formats only, not loose text mentions.

    Explicit means a typed field, visible file name/kind, URL path suffix, or a
    structured link kind/URL suffix. Free-form evidence like "I mention PDF" is
    intentionally ignored and left to step7.
    """
    formats: list[str] = []

    def add(fmt: object) -> None:
        normalized = normalize_format(fmt)
        if normalized and normalized not in formats:
            formats.append(normalized)

    for key in ["file_kind", "format"]:
        val = item.get(key)
        if isinstance(val, str):
            fmt = _format_from_name(val)
            if fmt:
                add(fmt)

    for key in ["file_name", "url", "source_url"]:
        fmt = _format_from_name(item.get(key))
        if fmt:
            add(fmt)

    for f in item.get("visible_files", []) if isinstance(item.get("visible_files"), list) else []:
        if not isinstance(f, dict):
            continue
        fmt = _format_from_name(f.get("kind")) or _format_from_name(f.get("name"))
        if fmt:
            add(fmt)

    for link in item.get("links", []) if isinstance(item.get("links"), list) else []:
        if isinstance(link, dict):
            fmt = _format_from_name(link.get("kind")) or _format_from_name(link.get("url"))
            if fmt:
                add(fmt)
        else:
            fmt = _format_from_name(link)
            if fmt:
                add(fmt)

    return formats


def classify_format(formats: list[str], profile: dict[str, Any]) -> tuple[str, str]:
    success = profile_success_formats(profile)
    auxiliary = profile_auxiliary_formats(profile)
    if any(fmt in success for fmt in formats):
        return "success", next(fmt for fmt in formats if fmt in success)
    if any(fmt in auxiliary for fmt in formats):
        return "auxiliary", next(fmt for fmt in formats if fmt in auxiliary)
    if formats:
        return "exclude", formats[0]
    return "UNKNOWN", ""


def is_login_or_manual_status(status: str) -> bool:
    return status in {"login_required_downloadable", "visible_score_files", "manual_action_required", "manual_download_candidate", "cloud_folder_or_unknown"}


def status_for_format_match(match: str, fmt: str, incoming_status: str, item: dict[str, Any], text: str, url: str) -> tuple[str, int, str]:
    if match == "success":
        if incoming_status in {"downloaded", "download_success"} or item.get("local_path"):
            return "downloaded", 100, f"explicit target format {fmt}; already downloaded/validated upstream"
        if incoming_status in {"login_required_downloadable", "visible_score_files"} or LOGIN_RE.search(text):
            return "login_required_downloadable", 75, f"explicit target format {fmt} behind normal login/platform flow"
        if incoming_status in {"manual_action_required", "manual_download_candidate", "cloud_folder_or_unknown"} or (CLOUD_HOST_RE.search(url) and not DIRECT_HOST_RE.search(url)):
            return "manual_action_required", 45, f"explicit target format {fmt} requires manual/browser/client action"
        return "download_candidate", 70, f"explicit target format {fmt}"
    if match == "auxiliary":
        status = "midi_only_auxiliary" if fmt in AUXILIARY_STATUS_FORMATS else "page_candidate"
        return status, 35, f"explicit auxiliary format {fmt} for target profile"
    return "not_full_piano_score", 10, f"explicit non-target format {fmt}"


def classify(item: dict[str, Any], index: int, target_profile: str = "piano_score", profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = profile or load_profile(target_profile)
    target_profile = str(profile.get("profile") or target_profile)
    text = text_of(item)
    url = str(item.get("url") or item.get("source_url") or "")
    incoming_status = normalize_status(item.get("status"), default=str(item.get("status") or ""))
    reasons: list[str] = []
    status = "UNKNOWN"
    score = 0
    needs_ai = True
    match_value = "UNKNOWN"

    has_paid = bool(PAID_RE.search(text))
    has_private = bool(PRIVATE_RE.search(text))
    has_login = bool(LOGIN_RE.search(text))
    has_cloud = bool(CLOUD_HOST_RE.search(url))
    has_direct = bool(DIRECT_HOST_RE.search(url))
    has_platform = bool(SCORE_PLATFORM_RE.search(url))
    formats = explicit_formats(item)

    if incoming_status in TERMINAL_STATUS_MAP:
        status, score, reason, needs_ai, match_value = TERMINAL_STATUS_MAP[incoming_status]
        reasons.append(reason)
    elif has_paid:
        status = "paid_or_store_excluded"; score = 5; needs_ai = False; match_value = "exclude"; reasons.append("paid/store signal")
    elif has_private:
        status = "private_gate"; score = 5; needs_ai = False; match_value = "exclude"; reasons.append("private message/group gate")
    elif formats:
        match_value, fmt = classify_format(formats, profile)
        status, score, reason = status_for_format_match(match_value, fmt, incoming_status, item, text, url)
        needs_ai = False
        reasons.append(reason)
    elif incoming_status in {"downloaded", "download_success"}:
        # A downloaded candidate without an explicit recognizable file kind is a
        # conflict; step7/user should inspect before counting it as target.
        status = "UNKNOWN"; score = 60; needs_ai = True; match_value = "UNKNOWN"; reasons.append("downloaded-like status without explicit target format")
    elif is_login_or_manual_status(incoming_status) or (has_cloud and not has_direct):
        status = "manual_action_required" if incoming_status != "login_required_downloadable" and not has_login else "login_required_downloadable"
        score = 40; needs_ai = True; match_value = "UNKNOWN"; reasons.append("access-gated candidate without explicit file format; needs rubric adjudication")
    elif has_platform or url:
        status = "page_candidate"; score = 30; needs_ai = True; match_value = "UNKNOWN"; reasons.append("page candidate without explicit file format; needs rubric adjudication")
    else:
        status = "UNKNOWN"; score = 0; needs_ai = True; match_value = "UNKNOWN"; reasons.append("insufficient evidence")

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
        "match": {target_profile: match_value},
        "reasons": reasons,
        "evidence": evidence,
        "needs_ai": needs_ai,
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Config-driven classifier; only ambiguous candidates go to rubric step7.")
    ap.add_argument("input_json")
    ap.add_argument("--profile", default="piano_score", choices=profile_names(), help="target profile loaded from profiles/<profile>.yaml")
    ap.add_argument("--out", required=True)
    ap.add_argument("--unknown-out", default="")
    args = ap.parse_args()

    profile = load_profile(args.profile)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    items = flatten_payload(payload)
    classified = [classify(item, i, args.profile, profile) for i, item in enumerate(items, 1)]
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
