# Piano Sheet Fetcher

Codex skill and script toolkit for finding public, non-paid, complete piano score artifacts by song title.

The product entry point is:

```powershell
python .\scripts\find_piano_scores.py "<song-title>" --out ".\sheet-music"
```

Optional metadata can improve recall:

```powershell
python .\scripts\find_piano_scores.py "<song-title>" --artist "<artist>" --alias "<alias>" --out ".\sheet-music"
```

## What it does

- Searches multiple public channels for user-uploaded piano score links.
- Attempts direct public downloads only for complete-score formats.
- Validates downloaded file signatures before counting success.
- Emits local reports and machine-readable candidate files.
- Separates direct downloads, login-required downloadable pages, manual-action candidates, paid/store exclusions, and unknown cases.

Success formats include PDF, MSCZ, MXL, MusicXML, and score bundles that clearly contain those formats. MIDI-only is treated as auxiliary for the complete piano score workflow.

## Output

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

## Safety and source policy

The scripts do not bypass login, payment, CAPTCHA, DRM, cloud-client, or private-message/group gates. Paid resources are recorded as exclusions. Login-required but visibly downloadable files are listed for normal user action.

See `SKILL.md` and `references/source-policy.md` for the full workflow contract.

## Main scripts

- `scripts/find_piano_scores.py` — product entry point.
- `scripts/classify_candidates.py` — deterministic candidate classifier.
- `scripts/dedupe_candidates.py` — candidate dedupe/ranking/report helper.
- `scripts/fetch_sheet.py` — direct public artifact downloader.
- `scripts/bilibili_full_score_hunt.py` — Bilibili source probe only.
- `scripts/status_enum.py` — canonical final candidate statuses.
