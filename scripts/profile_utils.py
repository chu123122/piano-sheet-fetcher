"""Profile rubric loader for piano-sheet-fetcher.

Profiles are intentionally small YAML-like files so adding a target profile does
not require editing classifier or adjudication prompt code. The parser supports
the subset used by profiles/*.yaml: scalar strings and one-line lists.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROFILES_DIR = SCRIPT_DIR.parent / "profiles"


def _parse_list(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
    return [value.strip().strip("'\"")] if value else []


def load_profile(name: str) -> dict[str, Any]:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        raise SystemExit(f"unknown profile {name!r}; available: {', '.join(profile_names())}")
    data: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("["):
            data[key] = _parse_list(value)
        else:
            data[key] = value.strip("'\"")
    data.setdefault("profile", name)
    data.setdefault("success_formats", [])
    data.setdefault("auxiliary_formats", [])
    data.setdefault("extra_paid_domains", [])
    data.setdefault("preferred_channels", [])
    data.setdefault("search_terms", [])
    return data


def profile_names() -> list[str]:
    if not PROFILES_DIR.exists():
        return ["piano_score"]
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml") if re.fullmatch(r"[a-z0-9_][a-z0-9_-]*", p.stem))


def normalize_format(fmt: object) -> str:
    value = str(fmt or "").lower().strip().lstrip(".")
    aliases = {"jpeg": "jpg", "htm": "html", "xml": "musicxml"}
    return aliases.get(value, value)


def profile_success_formats(profile: dict[str, Any]) -> set[str]:
    return {normalize_format(x) for x in profile.get("success_formats", []) if normalize_format(x)}


def profile_auxiliary_formats(profile: dict[str, Any]) -> set[str]:
    return {normalize_format(x) for x in profile.get("auxiliary_formats", []) if normalize_format(x)}


def profile_target_kinds(profile: dict[str, Any]) -> set[str]:
    formats = profile_success_formats(profile)
    aliases = {"mid": "midi", "jpg": "image", "jpeg": "image", "png": "image", "html": "html"}
    out = {aliases.get(x, x) for x in formats}
    if "musicxml" in formats:
        out.add("musicxml")
    return out


def profile_target_suffixes(profile: dict[str, Any]) -> set[str]:
    suffixes: set[str] = set()
    for fmt in profile_success_formats(profile):
        if fmt == "midi":
            suffixes.update({".mid", ".midi"})
        elif fmt == "jpg":
            suffixes.update({".jpg", ".jpeg"})
        elif fmt:
            suffixes.add("." + fmt)
    return suffixes


def profile_rubric_text(profile: dict[str, Any]) -> str:
    lines = [
        f"profile: {profile.get('profile', '')}",
        f"success: {profile.get('success', '')}",
        f"auxiliary: {profile.get('auxiliary', '')}",
        f"exclude: {profile.get('exclude', '')}",
        "success_formats: " + ", ".join(profile.get("success_formats", [])),
        "auxiliary_formats: " + ", ".join(profile.get("auxiliary_formats", [])),
        "preferred_channels: " + ", ".join(profile.get("preferred_channels", [])),
        "search_terms: " + ", ".join(profile.get("search_terms", [])),
    ]
    return "\n".join(lines)
