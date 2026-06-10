---
name: piano-sheet-fetcher
description: Script-driven multi-channel workflow for finding other-user-uploaded complete piano score files by song name. Scripts own channel selection, query generation, public metadata search, link/code extraction, gate classification, candidate scoring, dedupe, direct public download, local organization, and report rendering. AI is invoked only once for batched UNKNOWN/conflict candidates.
---

# Piano Sheet Fetcher

Goal: given a song name, find public non-paid complete piano score artifacts and save them locally when a direct public download is available. Success formats are PDF, MSCZ, MXL, MusicXML, full score image sets, or score bundles that clearly include those formats. MIDI-only is auxiliary evidence, not a successful full-score fetch.

## Runtime Ownership Contract

Follow the deterministic script ownership rules in this section and the source policy under `references/`.

Hard rule:

> Same input/same output and enumerable rules -> script. Semantic ambiguity/world knowledge/conflicting evidence -> AI.

Runtime default:

- Scripts own the main path.
- AI is exception-based only: classify scripts emit `UNKNOWN`/conflict candidates; AI adjudicates that small batch once.
- Gate states are deterministic script states. Login, paid store, CAPTCHA, private message, group join, cloud-client requirement, and Baidu/Quark client gates do not trigger AI.
- Direct download is attempted only for public direct artifact links such as GitHub/raw, Google Drive public file, Dropbox/OneDrive public file, Lanzou/123pan direct-public style links, or score-site direct artifacts.
- Baidu Netdisk and SheetHost login-gated candidates are recorded with evidence; do not bypass login/client/anti-automation.

## Script Pipeline

1. **Input/profile/channel selection — script**
   - Use profile defaults. Multi-channel is the current core direction when the user asks for song search.
   - Bilibili-only remains available only when the user explicitly narrows scope.

2. **Query generation — script, optional cached AI alias fill**
   - Use `scripts/generate_search_queries.py`.
   - Script handles title, artist, known aliases, romanization-like aliases supplied by user, and format keywords.
   - Unknown aliases/translations may be asked once and cached outside runtime if needed.

3. **Search + public metadata probes — script**
   - Default product entry: `scripts/find_piano_scores.py`
   - Bilibili source probe: `scripts/bilibili_full_score_hunt.py` (raw evidence only; no final classification)
   - Video pages: `scripts/probe_video_pages.py`
   - Score pages/platforms: `scripts/probe_score_pages.py`
   - Multi-source test harness: `scripts/run_multisource_phase2a.py`

4. **Link/code/format extraction — script**
   - Use `scripts/extract_score_links.py` on visible descriptions/comments/copied text/metadata.

5. **Gate status — script state machine**
   - Canonical status source: `scripts/status_enum.py`.
   - Final candidate status must be one of: `downloaded`, `download_candidate`, `login_required_downloadable`, `manual_action_required`, `paid_or_store_excluded`, `private_gate`, `not_full_piano_score`, `midi_only_auxiliary`, `page_candidate`, `UNKNOWN`, `failed`.
   - Platform-specific hints such as MuseScore/SheetHost are stored in `classification`, `platform`, `reasons`, or `evidence`, not as extra status names.
   - Gate mapping does not call AI.

6. **Candidate classification — script**
   - Use `scripts/classify_candidates.py`.
   - Output contract per candidate:

```json
{
  "candidate_id": "...",
  "status": "downloaded|download_candidate|login_required_downloadable|manual_action_required|paid_or_store_excluded|private_gate|not_full_piano_score|midi_only_auxiliary|page_candidate|UNKNOWN|failed",
  "score": 0,
  "match": {"piano_score": "success|auxiliary|exclude|UNKNOWN"},
  "reasons": ["..."],
  "evidence": ["原文片段"],
  "needs_ai": false
}
```

7. **UNKNOWN/conflict adjudication — AI once per run**
   - Only if `unknown_candidates.json` is non-empty.
   - Use `templates/unknown_adjudication_prompt.md`.
   - AI returns JSON only; scripts consume the result or the operator applies it.
   - Do not send already deterministic paid/login/private/direct cases to AI.

8. **Dedupe + ranking — script**
   - Use `scripts/dedupe_candidates.py`.
   - Canonical URL, score, duplicate count, and status decide order.

9. **Direct public download — script**
   - Use `scripts/fetch_sheet.py` for direct artifact URLs or public pages that expose a direct artifact.
   - Do not use it for paid/login/client-gated pages.
   - Validate file signatures before counting success; `application/octet-stream` alone is not enough.

10. **Organize local files — script**
   - Use `scripts/organize_local_score.py` after a file is downloaded normally or supplied by the user.

11. **Report — script render only**
   - `scripts/find_piano_scores.py` writes `RESULT.md` / `RESULT.json` for user-facing runs.
   - `scripts/dedupe_candidates.py --report` can render ranked candidate audits.
   - `scripts/render_candidate_report.py` is a legacy-compatible pure renderer only; it must not classify or override status.
   - Reports are evidence output, not AI judgement.

## Quick Commands

Default product command:

```powershell
python path\to\piano-sheet-fetcher\scripts\find_piano_scores.py "<song-title>" --out ".\sheet-music"
```

Optional metadata:

```powershell
python path\to\piano-sheet-fetcher\scripts\find_piano_scores.py "<song-title>" --artist "<artist>" --alias "<alias>" --alias "<another-alias>" --out ".\sheet-music"
```

Generate queries:

```powershell
python path\to\piano-sheet-fetcher\scripts\generate_search_queries.py "ただ君に晴れ" --artist "Yorushika" --alias "Tada Kimi ni Hare" --out ".\sheet-music\song\QUERY_PLAN.md"
```

Probe pages:

```powershell
python path\to\piano-sheet-fetcher\scripts\probe_video_pages.py "https://www.youtube.com/watch?v=..." "https://www.bilibili.com/video/BV..." --out ".\sheet-music\song\VIDEO_PROBE.md"
python path\to\piano-sheet-fetcher\scripts\probe_score_pages.py "https://sheet.host/sheet/..." --out ".\sheet-music\song\SCORE_PAGE_PROBE.md"
python path\to\piano-sheet-fetcher\scripts\probe_baidu_public_share.py "https://pan.baidu.com/s/..." --code "abcd" --out ".\sheet-music\song"
```

Bilibili source probe only:

```powershell
python path\to\piano-sheet-fetcher\scripts\bilibili_full_score_hunt.py "<song-title>" --artist "<artist>" --out ".\sheet-music"
```

Classify + isolate AI batch:

```powershell
python path\to\piano-sheet-fetcher\scripts\classify_candidates.py ".\candidates.json" --out ".\classified.json" --unknown-out ".\unknown_candidates.json"
```

Dedupe + report:

```powershell
python path\to\piano-sheet-fetcher\scripts\dedupe_candidates.py ".\classified.json" --out ".\ranked.json" --report ".\CANDIDATE_REPORT.md" --title "ただ君に晴れ"
```

Direct download only:

```powershell
python path\to\piano-sheet-fetcher\scripts\fetch_sheet.py "https://example.org/free-score.pdf" --out ".\sheet-music"
```

Organize already obtained files:

```powershell
python path\to\piano-sheet-fetcher\scripts\organize_local_score.py ".\Downloads\Tada Kimi Ni Hare.pdf" --song "ただ君に晴れ" --artist "Yorushika" --source-url "https://sheet.host/sheet/6dezKU" --arranger "hecap1105" --acquired-by "sheethost-login" --out ".\sheet-music"
```

## Search Rules

- Multi-channel search is now the default implementation direction: Bilibili, YouTube/video descriptions, public search result pages, SheetHost/MuseScore-like score pages, and public cloud/file links.
- Bilibili-only mode is a scoped mode, not the general product direction.
- Prefer creator/uploader-posted links and visible public description/comment text.
- Skip paid stores and membership-only resources as downloads; record them as exclusions.
- SheetHost pages that list PDF/MID but route through login are `login_required_downloadable`.
- MuseScore and ordinary score-platform pages are `page_candidate` unless normal downloadability is confirmed; keep platform detail in `classification`/`platform`.
- Baidu/Quark/Aliyun/123pan links with extraction codes are `manual_action_required` unless a direct artifact can be downloaded normally.
- Treat MIDI-only as `auxiliary` unless paired with PDF/MSCZ/MXL/MusicXML or a score bundle.
- Simplified numbered notation, waterfall/Synthesia-only videos, and display-only tutorials are exclusions.
- Do not bypass account walls, payment, CAPTCHA, anti-bot controls, region restrictions, DRM, watermarking, or download limits.

## Output Contract

```text
sheet-music/
  query-title/
    RESULT.md
    RESULT.json
    raw_candidates.json
    classified.json
    unknown_candidates.json
    ranked.json
    downloads/
      title-source.pdf
      title-source.pdf.source.json
```

## Failure Behavior

Stop and report evidence when:

- `unknown_candidates.json` is non-empty and AI adjudication has not been run.
- only paid, private-gated, login-gated, or rights-unclear candidates exist.
- only simplified notation, waterfall/Synthesia, MIDI-only, or display-only pages are found.
- direct download fails or returns a non-score artifact.
- the next step requires manual login, CAPTCHA, private message, joining a group, cloud-client download, or purchase.

Give the user ranked candidate links, statuses, evidence, and the exact local files produced.
