# Source Policy

Use this reference before fetching sheet music.

## Allowed Sources

- Public-domain repositories where the source page indicates the score is public domain in the relevant jurisdiction.
- Creative Commons or otherwise open-license pages where the license permits download and local storage.
- Official composer, artist, publisher, school, archive, library, or contest pages that provide a free download.
- Direct URLs supplied by the user when they state they have the right to download or archive the file.
- Bilibili-discovered public non-paid shares intentionally posted by an uploader in a video description, pinned/visible comment, dynamic, profile, or collection. Use the normal browser/cloud-drive flow only.

## Disallowed Sources

- Sites primarily offering unauthorized copies of commercial sheet music.
- Paywalled, login-gated, subscription-only, token-limited, or DRM-protected sources.
- Pages that only permit in-browser viewing when no download right is stated.
- Downloads requiring CAPTCHA, anti-bot bypass, credential use, session-cookie reuse, or hidden API calls.
- Paid resources, private groups, private messages, invite-only shares, and links that require bypassing limits or protections.

## Modern J-pop/J-hop Handling

In Bilibili-only mode, download complete score files from public non-paid shares discovered through Bilibili when they are normally accessible through the logged-in browser. Do not bypass payment, CAPTCHA, private groups, or hidden APIs.

For this skill, the target is a complete piano score. Exclude simplified numbered notation (`简谱`), waterfall videos, Synthesia-only videos, and display-only tutorials unless they link to a complete score file.

Success formats:

- Success: PDF, MSCZ, MXL, MusicXML, ZIP/RAR/7Z bundles that clearly contain those score formats, or complete score image sets.
- Not success by itself: MIDI-only, Synthesia-only, waterfall-only, simplified numbered notation, or visible-only tutorial videos.

Use only the canonical statuses from `scripts/status_enum.py`:

- `downloaded`: a local score artifact was downloaded and signature/extension validation passed.
- `download_candidate`: public non-paid direct artifact candidate; downloader may try the normal public flow.
- `login_required_downloadable`: page visibly lists a complete score file, but normal account/platform login is required.
- `manual_action_required`: normal browser/client/user action is required, such as cloud-drive confirmation, extraction-code lookup, CAPTCHA, or comment inspection.
- `paid_or_store_excluded`: paid store, purchase, patron/member storefront, or commercial listing; record only, do not download.
- `private_gate`: private message, group join, invite, or non-public gate; record only, do not automate.
- `not_full_piano_score`: simplified numbered notation, waterfall/Synthesia-only video, MIDI-only display, non-piano, or no score evidence.
- `midi_only_auxiliary`: MIDI-only evidence; auxiliary for full piano score tasks.
- `page_candidate`: ordinary source/platform page or rights/downloadability-unclear page that needs further inspection.
- `UNKNOWN`: deterministic rules conflict or evidence is insufficient; send only this small batch to AI adjudication.
- `failed`: probe/download failed or link is dead.

Legacy names such as `purchase_required`, `login_required_strong_candidate`, `rights_unclear`, `video_probe`, or `display_only` must be normalized through `status_enum.py` before final output.

## Verification Notes

Record the evidence seen on the source page. Keep it short:

- source URL
- title and composer/artist
- rights statement or reason it appears legally accessible
- date accessed
- any uncertainty

When rights are unclear, do not download. Save candidate links in `SEARCH_RESULTS.md` and ask the user for a permitted source or confirmation of rights.

For this user's Bilibili workflow, a public non-paid complete-score share found through Bilibili is considered user-authorized to download through the normal logged-in browser flow.
