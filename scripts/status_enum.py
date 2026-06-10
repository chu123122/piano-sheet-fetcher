"""Canonical candidate statuses for piano-sheet-fetcher.

This file is the single source of truth for product candidate status names.
Source probes may keep transport/probe metadata in other fields, but final
candidate `status` values must be one of CANONICAL_STATUSES.
"""
from __future__ import annotations

CANONICAL_STATUSES = {
    "downloaded",
    "download_candidate",
    "login_required_downloadable",
    "manual_action_required",
    "paid_or_store_excluded",
    "private_gate",
    "not_full_piano_score",
    "midi_only_auxiliary",
    "page_candidate",
    "UNKNOWN",
    "failed",
}

STATUS_ALIASES = {
    "download_success": "downloaded",
    "visible_score_files": "login_required_downloadable",
    "login_required_strong_candidate": "login_required_downloadable",
    "creator_linked_login_required": "login_required_downloadable",
    "login_or_membership_required": "login_required_downloadable",
    "manual_download_candidate": "manual_action_required",
    "cloud_folder_or_unknown": "manual_action_required",
    "purchase_required": "paid_or_store_excluded",
    "paid_or_member_only": "paid_or_store_excluded",
    "paid_or_membership": "paid_or_store_excluded",
    "private_or_group_gate": "private_gate",
    "not_target_profile": "not_full_piano_score",
    "not_piano_score": "not_full_piano_score",
    "no_score_evidence": "not_full_piano_score",
    "display_only": "not_full_piano_score",
    "display-only": "not_full_piano_score",
    "auxiliary_only": "midi_only_auxiliary",
    "score_platform_candidate": "page_candidate",
    "public_but_verify": "page_candidate",
    "public_link_needs_verify": "page_candidate",
    "full_score_mentioned_no_public_link": "page_candidate",
    "rights_unclear": "page_candidate",
    "creator_linked_public": "page_candidate",
    "video_probe": "page_candidate",
    "research_index": "page_candidate",
    "review_manually": "page_candidate",
    "no_visible_file": "page_candidate",
    "dead_link": "failed",
    "download_failed": "failed",
    "probe_failed": "failed",
}


def normalize_status(status: object, default: str = "UNKNOWN") -> str:
    value = str(status or "").strip()
    if value in CANONICAL_STATUSES:
        return value
    return STATUS_ALIASES.get(value, default)


def assert_canonical(status: object) -> str:
    value = normalize_status(status)
    if value not in CANONICAL_STATUSES:
        raise ValueError(f"non-canonical status: {status!r}")
    return value
