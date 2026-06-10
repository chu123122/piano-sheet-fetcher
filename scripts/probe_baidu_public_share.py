#!/usr/bin/env python3
"""Probe a public Baidu Netdisk share found from Bilibili.

This helper submits a public extraction code through Baidu's normal web flow,
lists visible shared files, and writes local evidence. It does not bypass login,
CAPTCHA, client-only download requirements, or anti-automation limits.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def run_curl(args: list[str], *, text: bool = True) -> str:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl is required for Baidu share probing on this machine")
    result = subprocess.run([curl, *args], check=False, capture_output=True)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"curl failed with exit {result.returncode}: {stderr}")
    return result.stdout.decode("utf-8", errors="replace") if text else result.stdout.decode("latin1")


def extract_surl(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    match = re.search(r"/s/([0-9A-Za-z_-]+)", parsed.path)
    token = match.group(1) if match else urllib.parse.parse_qs(parsed.query).get("surl", [""])[0]
    if token.startswith("1"):
        token = token[1:]
    if not token:
        raise ValueError(f"cannot extract Baidu surl from {url}")
    return token


def parse_mset(html: str) -> dict[str, Any]:
    match = re.search(r"locals\.mset\((\{.*?\})\);", html, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def curl_common(cookie: Path) -> list[str]:
    return ["-s", "-L", "-c", str(cookie), "-b", str(cookie), "-A", UA]


def fetch_text(cookie: Path, url: str, referer: str = "") -> str:
    args = [*curl_common(cookie)]
    if referer:
        args.extend(["-H", f"Referer: {referer}"])
    args.append(url)
    return run_curl(args)


def verify_code(cookie: Path, surl: str, code: str) -> dict[str, Any]:
    endpoint = "https://pan.baidu.com/share/verify?" + urllib.parse.urlencode(
        {
            "surl": surl,
            "t": str(int(time.time() * 1000)),
            "channel": "chunlei",
            "web": "1",
            "app_id": "250528",
            "clienttype": "0",
        }
    )
    body = run_curl(
        [
            *curl_common(cookie),
            "-H",
            f"Referer: https://pan.baidu.com/share/init?surl={surl}",
            "-H",
            "X-Requested-With: XMLHttpRequest",
            "-H",
            "Content-Type: application/x-www-form-urlencoded; charset=UTF-8",
            "--data",
            "pwd=" + urllib.parse.quote_plus(code),
            endpoint,
        ]
    )
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"errno": "parse_error", "body": body[:300]}


def list_dir(cookie: Path, uk: str, shareid: str, directory: str) -> dict[str, Any]:
    url = "https://pan.baidu.com/share/list?" + urllib.parse.urlencode(
        {
            "uk": uk,
            "shareid": shareid,
            "page": "1",
            "num": "100",
            "dir": directory,
            **({"root": "1"} if directory == "/" else {}),
        }
    )
    body = fetch_text(cookie, url, "https://pan.baidu.com/")
    return json.loads(body)


def success_format(filename: str) -> str:
    lower = filename.lower()
    for ext in [".pdf", ".mscz", ".mxl", ".musicxml"]:
        if lower.endswith(ext):
            return ext[1:].upper()
    if lower.endswith((".zip", ".rar", ".7z")):
        return "BUNDLE"
    if lower.endswith((".mid", ".midi")):
        return "MIDI"
    return ""


def write_report(out: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "BAIDU_PUBLIC_SHARE_PROBE.json"
    md_path = out / "BAIDU_PUBLIC_SHARE_PROBE.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Baidu Public Share Probe",
        "",
        f"- Share URL: {payload['share_url']}",
        f"- Extraction code: {payload.get('code', '')}",
        f"- Verify errno: {payload.get('verify', {}).get('errno')}",
        f"- Share UK: {payload.get('share_uk', '')}",
        f"- Share ID: {payload.get('shareid', '')}",
        "",
        "## Files observed",
        "",
    ]
    for item in payload.get("files", []):
        fmt = success_format(str(item.get("server_filename", "")))
        lines.append(f"- `{item.get('server_filename', '')}` — {item.get('size', 0)} bytes" + (f" — {fmt}" if fmt else ""))
    lines.extend(
        [
            "",
            "## Download status",
            "",
            "- This probe confirms visible public-share files and target formats.",
            "- It does not perform Baidu client/login/CAPTCHA bypass and does not claim a local PDF was downloaded.",
            "- Use the normal browser download flow, then run `organize_local_score.py` on the downloaded file.",
            "",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a Bilibili-discovered public Baidu share.")
    parser.add_argument("share_url")
    parser.add_argument("--code", default="")
    parser.add_argument("--out", default=".")
    args = parser.parse_args()

    surl = extract_surl(args.share_url)
    with tempfile.TemporaryDirectory(prefix="baidu-share-") as tmp:
        cookie = Path(tmp) / "cookies.txt"
        html = fetch_text(cookie, args.share_url)
        initial = parse_mset(html)
        verify = verify_code(cookie, surl, args.code) if args.code else {}
        html = fetch_text(cookie, args.share_url)
        state = parse_mset(html) or initial
        uk = str(state.get("share_uk", ""))
        shareid = str(state.get("shareid", ""))
        if not uk or not shareid:
            raise RuntimeError("could not recover share_uk/shareid after verification")

        root = list_dir(cookie, uk, shareid, "/")
        files: list[dict[str, Any]] = []
        for item in root.get("list", []):
            if str(item.get("isdir")) == "1":
                sub = list_dir(cookie, uk, shareid, str(item.get("path", "")))
                files.extend(sub.get("list", []))
            else:
                files.append(item)

    payload = {
        "share_url": args.share_url,
        "code": args.code,
        "verify": verify,
        "share_uk": uk,
        "shareid": shareid,
        "files": files,
    }
    json_path, md_path = write_report(Path(args.out), payload)
    print(md_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())
