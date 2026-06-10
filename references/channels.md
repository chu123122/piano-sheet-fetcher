# Channel Playbook

Default scope: Bilibili only. Use already logged-in browser state when available. Bilibili may point to public non-paid cloud-drive shares; those external shares are in scope only when discovered from Bilibili pages, comments, dynamics, profiles, or collections.

## Query Expansion

Build Bilibili query groups from the song title:

- Full-score Chinese: `<title> 钢琴谱`, `<title> 五线谱`, `<title> 完整谱`, `<title> 附谱`
- File hints: `<title> pdf`, `<title> mscz`, `<title> musicxml`, `<title> mxl`, `<title> 谱包`
- Comment/link hints: `<title> 网盘`, `<title> 提取码`, `<title> 谱在简介`, `<title> 谱在评论`, `<title> 置顶`
- Exclusion-biased: `<title> 钢琴谱 -简谱 -瀑布流`

Prefer exact title plus artist/source title when the song name is ambiguous.

For repeatable runs, generate the query plan first:

```powershell
python scripts\generate_search_queries.py "ただ君に晴れ" --artist "Yorushika" --alias "Tada Kimi ni Hare" --alias "只为你而放晴" --out .\sheet-music\ただ君に晴れ-yorushika\QUERY_PLAN.md
```

Record which query produced each candidate in the candidate JSON. This makes later reruns auditable instead of depending on memory.

## Bilibili

Search Bilibili and web results with Chinese/Japanese terms. For promising videos:

1. Run `probe_video_pages.py` on promising URLs for public metadata. If it finds only simplified notation, waterfall, or display-only content without file links, mark as `not_full_piano_score`.
2. Inspect title, description, tags, UP profile, collection, dynamic posts, and visible comments.
3. Look for `附谱`, `谱在简介`, `评论区`, `置顶`, `网盘`, `提取码`, `pdf`, `midi`, `mscz`, `工程文件`.
4. Copy visible page/comment text into `extract_score_links.py` to recover netdisk links and extraction codes.
5. For Baidu/Quark/Aliyun/Lanzou/123pan links, use the normal public-share UI only. Download public non-paid complete score files and save the extraction code in metadata.
6. Stop and record an artificial action point if download requires CAPTCHA, private message, joining a group, asking the uploader, manual cloud-drive confirmation, or any paid step.
7. Continue searching after a first success to collect multiple complete-score versions when available.

Use the already logged-in browser session when available. Do not inspect browser secrets, cookies, or credential stores.

## Cloud Drives

Allowed when the link is public, non-paid, and discovered from Bilibili:

- Google Drive, Dropbox, OneDrive, GitHub Releases/raw files
- Baidu Pan, Quark, Aliyun Drive, Lanzou, 123pan public shares

Save the Bilibili source URL, uploader, description/comment/dynamic location, original share page URL, extracted access code, visible title, file name, and hash.

## Score Platforms And Local Files

For MuseScore, Sheethost, POPPIANO, creator stores, and official paid stores:

1. Probe the public page first and record visible file names, score type, account/subscription/purchase requirements, and rights signals.
2. Do not use hidden APIs, session-cookie replay, download-token guessing, or viewer scraping to bypass login/subscription/purchase controls.
3. If the user obtains the file through the normal site flow, run `organize_local_score.py` to copy the PDF/MID/MSCZ/ZIP into the song folder and write source metadata.

Example:

```powershell
python scripts\organize_local_score.py ".\Downloads\Tada Kimi Ni Hare.pdf" --song "ただ君に晴れ" --artist "Yorushika" --source-url "https://sheet.host/sheet/6dezKU" --arranger "hecap1105" --acquired-by "sheethost-login" --out .\sheet-music
```

## Ranking

Rank candidates in this order:

1. Bilibili uploader-posted public non-paid PDF/MSCZ/MXL/MusicXML/score bundle links.
2. Bilibili pinned/comment links from the same uploader account.
3. Bilibili dynamic/profile/collection links from the same uploader account.
4. Public non-paid cloud-drive shares discovered from Bilibili comments/descriptions.
5. Reposts or unclear uploads, only as candidates unless they expose a complete public non-paid score file.
6. Exclusions: simplified notation, waterfall, Synthesia-only, MIDI-only, paid resources.

## Candidate Report

When search results are mixed, create a JSON list with `song`, `title`, `url`, `channel`, `format`, `access`, `evidence`, and `notes`, then run:

```powershell
python scripts\render_candidate_report.py .\candidates.json --out .\sheet-music
```

Use `access`/probe fields as source evidence only; final `status` must come from `scripts/status_enum.py` after `classify_candidates.py`. If a creator-linked page exposes file names but routes through login, final status is `login_required_downloadable`. If it is only a platform/video/index page, final status is `page_candidate`. Include `query`, `upstream`, and `next_probe` when known. The report becomes the local artifact when no legally downloadable full score is available.

## Score Page Probe

When a score page advertises files but does not expose direct URLs, or when a platform page needs access-gate evidence, run:

```powershell
python scripts\probe_score_pages.py "https://sheet.host/sheet/..." --out .\sheet-music\song\SCORE_PAGE_PROBE.md
```

You can pass several URLs to produce a platform sweep report:

```powershell
python scripts\probe_score_pages.py "https://sheet.host/sheet/..." "https://musescore.com/..." "https://www.midishow.com/..." --out .\sheet-music\song\SCORE_PLATFORM_PROBE.md
```

Classify pages that list files but route downloads through login as `login_required`, not as failed downloads. For HTTP 403, timeout, account, subscription, or membership notes, use the normal browser/account flow if the user wants those files.
