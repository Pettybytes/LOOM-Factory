# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

---

## STOP STATE — 2026-09-18 ~19:55 EDT — RANGED ACQUISITION **RUNTIME-PROVEN** (00:30–01:00 clip acquired + card repaired)

### Accepted milestone
- **Bounded ranged acquisition works end to end on a long-form source.** `ClhBXVygWbc` (05:02) yielded the approved **00:30–01:00** window: job `157184c9-7113-46c0-b7b0-10006a3e8d55` is `acquired`, `bounded_segment`, provider `local`, **$0**, range fingerprint `929e4e72…`.
- **Acquired asset:** `youtube__ClhBXVygWbc__30-60__3a891cb35f99.mp4` · 2,489,452 B · **30.000 s** · 640×480 · H.264/AAC/yuv420p/**fast-start** · sha256 `3a891cb35f99…`. Library row, job provenance and the Discord card all agree; **1 row** per source, no duplicate.
- **No 302 s source retained** (the full video was never downloaded); tmp empty; **Apify ceiling untouched at 7,200 micro / 2 successes**.
- **Card (same message, updated in place):** `🎬 ACQUISITION · ACQUIRED` with `Window source 05:02 · range 00:30–01:00 · clip 30.0 s · origin manual_submit_source`, real media facts (`0:30 · 640×480 · landscape · 2.4 MB · Audio present (AAC)`), `Local acquisition · $0 provider cost`, `Open in Reaction Library` as a **link button with no custom_id**, and the **video attached natively** (2.4 MiB fits the tier-0 10 MiB limit) with an honest "Playable in this message" note.
- **OpenShorts ingest compatibility: 9/9 PASS** (MP4, H.264, AAC, yuv420p, fast-start, geometry, duration, size, content type).
- **Suites:** acquisition **408** · discord-review **143** · local-helper **76** — all passing.

### Long-form range lane (new)
`source_window.py` is the single contract (ms canonical): one video, one approved window, `MODE_BOUNDED_SEGMENT`; the 300 s limit applies to the **clip**, never the source; a whole source is acquirable only when ≤300 s; range-aware identity means the same source+range is idempotent while a different range is a distinct asset. Download uses `--download-sections *start-end` + `--force-keyframes-at-cuts`; the delivered clip is validated against the approved window (±2 s) and then normalized per the delivery contract.

### Defects found and fixed this round
1. **Stale adapter process** rendered the FAILED card: the click had planned Gate 2 as `full_item` with **no window**, and the old `safe_record()` **stripped the `?v=` identity** (`https://www.youtube.com/watch`). Row repaired, adapter restarted, and the identity rule now keeps `?v=`.
2. **Empty probe result** was misclassified as a policy refusal → now `NO_RESULTS`, **retryable**, with the metadata probe bounded to **3 attempts** (metadata only, $0).
3. **Fast-start was never applied to pass-through media** (H.264/AAC sources skipped normalization entirely). The contract now detects the atom order and **remuxes with `-c copy -movflags +faststart`** (no quality loss, no re-encode). This changed the clip's name/hash to `…3a891cb35f99.mp4` / `3a891cb35f99…` — the pre-written `…5d2df2a03c8e` was the not-yet-fast-start file.
4. **ACQUIRING was not shown before the work** — the card stayed FAILED for 1–2 minutes. `_do_acquire` now patches the same card to `⏳ ACQUISITION · IN PROGRESS` (window visible, conflicting actions locked) **before** yt-dlp/ffmpeg runs, after the durable authorization.
5. **`poster_fallback` previews swallowed the reason** — the card now states the exact size-limit reason and claims no playback when Discord didn't receive the video.

### Still open
- No UI yet for an operator-editable **suggested window** on an automatically discovered candidate (the data-layer fields are range-aware; the surface is not built).
- The ranged lane's automated intake path is wired at the data layer only.

---

## STOP STATE — 2026-09-18 ~19:30 EDT — REACTION LIBRARY DELIVERY FORMAT (every asset now H.264/AAC MP4)

### Why
Acquired YouTube media landed as **VP9 + Opus in MP4**: it played in the browser player but **QuickTime refused it** with a compatibility warning (Instagram assets were fine because they are H.264 + AAC). The fix is a **shared Reaction Library finalization contract**, not a YouTube-only workaround.

### The contract (`infra/acquisition/media_normalize.py`)
Delivery format is fixed for every path: **MP4 · H.264 · AAC · yuv420p · fast-start**. Dimensions, aspect ratio, orientation (rotation side data) and frame rate are preserved — no scaling or fps filters — and both streams are encoded in one pass so audio/video sync holds. The **≤100 MiB / ≤300 s** limits are re-validated on the *finalized* asset. Already-compliant media is **moved untouched** (no needless transcode); anything else is normalized once into a temp file, validated, and only then swapped in atomically. A failed conversion leaves the existing asset untouched and removes its temp file.

### Acquisition path
`yt-dlp` now **prefers `avc1` + `mp4a`** (`bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[ext=mp4]/bv*+ba/b`), with normalization as the reliable fallback. The finalized asset's **own** SHA-256 determines the deterministic filename, and download/staging leftovers are removed on success.

### Repair of the already-acquired asset (no re-download)
| | |
|---|---|
| old | `youtube__h3Q_tAFWY5g__d8990649b8e3.mp4` · **VP9 + Opus** · 16,407,149 B · sha256 `d8990649b8e3…` |
| new | `youtube__h3Q_tAFWY5g__06ab279dcb13.mp4` · **H.264 + AAC + yuv420p + faststart** · 19,977,430 B · sha256 `06ab279dcb13…` |
| geometry | 1080×1920 · 30/1 fps · **40.641 s** (unchanged) |
| re-download | **no** — the existing local file was normalized in place |

DB row (same row id, **1 row for the video**), `storage_url`, job `asset_provenance` (`delivery_format`, `normalized_from`) and the **Discord card updated in place** — `🎬 ACQUISITION · ACQUIRED`, new filename/hash, `Local acquisition · $0 provider cost`, `Open in Reaction Library` → new hash URL. A poster frame was extracted as reference data.

### Verification
- `ffprobe`: h264 / aac / yuv420p / 1080×1920 / 30/1 / 40.641 s / 19,977,430 B / faststart true.
- **macOS metadata (QuickTime-facing):** `kMDItemCodecs = ("H.264", "MPEG-4 AAC")`, `kMDItemContentType = public.mpeg-4`.
- Routes: gallery **200** (lists the new asset), detail **200**, media **206 video/mp4** (Range), poster **200**; the retired hash correctly **404**s.
- Consistency: DB filename == file on disk, DB hash == file hash, size matches, canonical URL strict, no duplicate row, **ceiling untouched (7,200 micro / 2)**, tmp **0 files**.
- Suites: acquisition **392** · discord-review **134** · local-helper **76** — all passing.

---

## STOP STATE — 2026-09-18 ~19:15 EDT — YOUTUBE ADAPTER **RUNTIME-PROVEN** (bounded local yt-dlp acquisition succeeded)

### Accepted milestone
- **YouTube exact-URL acquisition is runtime-proven.** One exact URL (`https://www.youtube.com/watch?v=h3Q_tAFWY5g`) acquired through the real lane: signed Discord click → Gate 1 → Gate 2 → one bounded local yt-dlp attempt → validated media → atomic finalize → Reaction Library row → card updated in place.
- **Acquired asset:** `youtube__h3Q_tAFWY5g__d8990649b8e3.mp4` · 16,407,149 B · 40.641 s · sha256 `d8990649b8e3…` · vp9/opus · library row canonical URL `https://www.youtube.com/watch?v=h3Q_tAFWY5g`.
- **Cost: $0.** Adapter `local:yt-dlp`, provider `local`, `provider_cost_micro 0`. The **Apify ceiling never moved** (7,200 micro / 2 successes, both Instagram). Three `local:yt-dlp` audit rows exist for the attempts (all $0, all classified) and **no YouTube row ever touched Apify**.
- **>300 s refusal proven on real metadata:** `ClhBXVygWbc` probed at **302 s** → terminal `DURATION_LIMIT` at the metadata stage, **no download**, card shows the exact duration and limit, `Acquire` removed, no `Retry`.
- **Capability flipped honestly:** `platform_capabilities.youtube` is now `runtime_proven` **because of this proof**; `can_acquire("youtube") == True`. It stayed `implemented_not_proven` until the proof actually succeeded.
- **Cards:** Short `🎬 ACQUISITION · ACQUIRED` (`Open in Reaction Library` link + `Hold`, `Local acquisition · $0 provider cost`, provenance `` `local:yt-dlp` · Authorized by Bryan ``); 5:01 terminal `DURATION_LIMIT` (`Hold | View Error | View Source`). Both updated **in place** — no replacement cards, no duplicate submissions or jobs.
- **Suites:** acquisition **374** · discord-review **134** · local-helper **76** — all passing.

### Defects found by running it live (all fixed)
1. `LOOM_ADAPTER_PROOF_PLATFORMS` was never set → the durable planner wrote `blocked_pending_platform_adapter`. Now plumbed through `~/.openclaw/.env` **and** the adapter's `run.sh` sourcing list.
2. Preflight's `_strip()` dropped the query string — for YouTube the `?v=` **is** the video, so dispatch targeted `youtube.com/watch` with no id. Preflight now compares normalized forms but **dispatches the job's canonical URL verbatim**.
3. `safe_record()` stripped every query string, destroying the identity query in stored provenance. It now keeps a single 11-char `v=` for YouTube watch URLs (signed/credential URLs are still dropped).
4. The local path hand-rolled its library row instead of the shared contract (`plan_library_entry` + `safe_record`) and sent an empty string into a **uuid** column. Both lanes now persist an identical shape.
5. `context_for` derives the card state from the **authorized job status**, so a crashed attempt that left the job `queued` made `Retry` illegal on a FAILED card. The retry is self-correcting, and `pending_job`/`authorized_job` now resolve by **content id OR fingerprint** (a card carries the content id).
6. yt-dlp can wrap the one requested video in a single-entry playlist envelope, or return top-level video fields with an empty `entries` array; both are now treated as exactly one item, and only genuine multi-item results are refused. The metadata probe also retries once (zero media bytes, $0).
7. The card's `sanitize_display_url` also stripped `?v=`, so embed and `View Source` pointed at nothing. Fixed with the same narrow allowance.

### Human clicks used
Two genuine operator clicks (Approve, then Retry Manually). The `Approve` on the 5:01 was authorized before its duration was known; it is now a terminal policy refusal.

---

## STOP STATE — 2026-09-18 ~18:45 EDT — YOUTUBE ADAPTER INCREMENT 1 (URL contract + local adapter; NOT yet wired)

### Built and tested this increment (39 new tests, suites green)
- `infra/acquisition/youtube_url.py` — strict YouTube exact-URL contract: accepts `watch?v=`, `youtu.be/<id>`, `/shorts/<id>` (tracking params/fragments stripped) and collapses every form to `https://www.youtube.com/watch?v=<id>`; rejects playlists/mixes/channels/searches/feeds/live/upcoming/embed, selection params on watch URLs (`list`, `index`, `start_radio`, …), malformed or missing ids, path-vs-query id drift, multiple videos, foreign hosts, credentials and non-http(s) schemes. Fingerprint construction is identical to `manual_intake.submission_fingerprint`, so both lanes share one identity per video.
- `infra/acquisition/ytdlp_adapter.py` — local adapter: `--no-playlist --playlist-items 1`, bounded format `bv*+ba/b`, `--merge-output-format mp4`, `--max-filesize 104857600`, `--retries 0`; a hygiene guard forbids cookies/browser-profile/username/password/netrc/header flags; bounds 300 s / 100 MiB / 1 result / 1 attempt enforced from metadata before download, during, and after; post-download validation (streams, audio, codecs, container duration, size, SHA-256, id drift); atomic finalize via `os.replace`; partials removed on failure.
- `infra/acquisition/local_acquisition.py` + `ledger.reconcile_local()` — local path reports **$0 provider cost**, writes a truthful 034 audit row (`actor_id = local:yt-dlp`), and **never touches the Apify ceiling**.

### Deliberately NOT done yet (honest gaps)
- Runner platform dispatch (a `youtube` job still routes to the Apify-shaped path).
- The card's `Local acquisition · $0 provider cost` line.
- The intake bridge from a manual `/submit_source` submission or an automated Source Engine candidate into the shared premium REVIEW card with `manual_submit_source` / `automated_source_engine` provenance.

### Capability + provenance honesty
`platform_capabilities` still reports `youtube = implemented_not_proven` and `can_acquire("youtube") == False`. I did NOT flip it: no bounded local media proof has run, so claiming runtime-proven would be false.

### Unchanged / untouched
**0** Apify calls, **0** Apify ceiling mutations, **0** real YouTube downloads, **0** cookies or account auth, **0** migrations, **0** production YouTube cards. Suites: acquisition 363 · discord-review 134 · local-helper 76.

### Only remaining input
One exact **public YouTube URL under five minutes** from Bryan. With it: finish the three wiring items, run the bounded local proof (one attempt, ≤300 s, ≤100 MiB, no cookies), and flip `youtube` to runtime-proven only if that proof succeeds.

---

## STOP STATE — 2026-09-18 ~18:30 EDT — INSTAGRAM ACQUISITION MILESTONE **RUNTIME-PROVEN** (accepted)

### Accepted milestone
- **Instagram acquisition is runtime-proven end to end**: a real Discord click → Gate 2 authorization → one bounded Apify run → bounded download → ffprobe validation → SHA-256 → atomic finalize → Reaction Library row → card updated in place. The envelope, actor-addressing and settlement defects found along the way are fixed and covered by regression tests.
- **Actual cost baseline: $0.0036 per single-result run** (Apify `usageTotalUsd`). Re-baselined from the old pinned $0.0026 → `cost.REEL_SCRAPER_RESULT_MICRO = 3_600`; the earlier successful run row was corrected with an idempotent, evidence-backed update; ceiling now 7,200 micro / 2 successes.
- **Two successful assets** in the library:
  - `instagram__DdZS1QRhrXE__9b22f887b2ff.mp4` — 23,721,338 B · 55.26 s · sha256 `9b22f887b2ff…`
  - `instagram__DdQE_RUS0GJ__06e1da6120df.mp4` — 16,689,411 B · 77.65 s · sha256 `06e1da6120df…`
- **Visible Reaction Library + gallery**: root is `~/Movies/LOOM-Reaction-Library`, centralised in `infra/acquisition/library_root.py` with a `LOOM_REACTION_LIBRARY_ROOT` override. `GET http://127.0.0.1:8787/library` renders a browsable gallery (poster, platform, duration, size, status, acquired date; each card opens its detail/player page), with `/library/<sha256>` detail, Range-capable `/media` and `/poster`. Desktop shortcut `~/Desktop/LOOM-Factory/Reaction-Library` → the real root. The old hidden dir is preserved as `~/.openclaw/loom-reaction-library.backup-2026-09-18` (nothing deleted).
- **ACK-first Discord behaviour**: every interaction is acknowledged before any store read, private notes go out as webhook follow-ups, and a failed context resolution fails closed (`CONTEXT_UNAVAILABLE`) with no writes.
- **10 MiB Discord fallback**: the guild's real upload limit is queried per click; an oversized asset attaches a poster frame (single frame, no transcoding, asset hash re-verified) and states the exact reason, with the library page as the playable route. The card never claims playback it does not have.
- **Health**: adapter `route AQ · durable store · live_runner enabled · token present · 100 MiB / 300 s / 1 result / 1 attempt / fallback false`; helper `/health` → `{"ok": true}`; `LOOM_ACQUISITION_LIVE=1`.
- **Suites**: acquisition 324 · discord-review 134 · local-helper 76 — all passing.

### Honest limitations (unchanged)
- The 15.9 MiB reel **cannot** play inline in Discord (tier-0 10 MiB limit); the ≤10 MiB native-attachment path is implemented and unit-tested but has not been exercised by a real acquisition.
- Hold has not been clicked live (proven by tests and the acceptance matrix only).
- The library link is loopback-only — valid from this Mac, not from another machine.
- The earlier successful ledger row keeps its historical `cost_estimate_micro` (2,600) while `cost_actual_micro` is now the provider's 3,600.
- Job `updated_at` is stamped by our code (the table has no trigger).

### Next adapter
**YouTube via local `yt-dlp`** — no paid provider, deterministic, no usage charge. Not started in this task.

---

## STOP STATE — 2026-09-17 ~20:55 EDT — SNAPCHAT + UNIVERSAL INVESTIGATION (read-only, $0)

### Universal + Snapchat (final read-only)
- Universal ranked (live-API): `agentx/all-video-scraper` (`etzrjsBIFSOb19k5E`, 271 users, 4.59★, $0.38/file, no usage) most reliable; `easyapi/all-in-one-media-downloader` (`iZbsVYT4VfdMxoIPL`, 888 users, rating 2.46 API vs 4.95 README, $2.99/1k **+ usage**, returns `medias[]` direct signed URLs) cheapest-advertised but flagged.
- Snapchat: dedicated low-adoption actors (`agentx/snapchat-video-downloader`, `automation-lab/snapchat-public-video-downloader`) — public Spotlight only, last priority. Not in allowlist.
- Architecture decision: **HYBRID** — platform-specific primary (proven Instagram + memo23 TikTok + yt-dlp local); universal `agentx/all-video-scraper` as INACTIVE fallback for X/Snapchat/niche. Do NOT delete the proven Instagram adapter.
- Universal economics (corrected): `agentx/all-video-scraper` = $0.38/file (~146× Instagram, deterministic, fallback-only); `easyapi/all-in-one` = headline ~$0.003 but REJECTED for trust/determinism/contract (usage charge, rating discrepancy, medias[], signed headers) — NOT price. Snapchat: `agentx/snapchat-video-downloader` (`q5qkPabhaMeWxWVGa`, $0.30/file, deterministic) = bounded-proof candidate; `automation-lab` (`LIdfZyy13VzOzEUNx`, usage, 75.7%) = conditional.
- $0 spent, no calls, no downloads, no implementation.

---

## STOP STATE — 2026-09-17 ~20:45 EDT — TIKTOK + X PROVIDER CORRECTION (read-only, $0)

### TikTok + X provider deep-dive (correction)
- `infra/acquisition/CROSS_PLATFORM_EVALUATION.md` now has ranked TikTok + X candidates (verified via live Apify API: numeric IDs + adoption).
- TikTok: recommended `memo23/tiktok-video-downloader` (`pQGdDfhFC35QToTxZ`, single-URL, $2/1k, no cookies); fallback `clockworks/tiktok-scraper` (`GdWCkxBtKWOsKjdch`, 286K users); emergency yt-dlp.
- X/Twitter: NO compliant candidate (2-user actors, usage pricing, auth walls) — DEFER. X is NOT in the LOOM platform allowlist.
- Capability: tiktok → `evaluated_not_proven` (new state). Only Instagram is runtime-proven. "Extractor exists" ≠ runtime-proven.
- $0 spent, no calls, no downloads, no implementation.

---

## STOP STATE — 2026-09-17 ~20:35 EDT — CROSS-PLATFORM EVALUATION (read-only; stop for the day)

### Cross-platform acquisition evaluation (read-only, $0, nothing built)
- `infra/acquisition/CROSS_PLATFORM_EVALUATION.md` — full comparison table + verdicts.
- **Local (yt-dlp, free) primary:** YouTube, Reddit (v.redd.it), Twitch clips + VOD, Kick clips + VOD.
- **Apify primary:** TikTok + X/Twitter (yt-dlp unreliable). Instagram already provider-proven.
- Capability: instagram → `runtime_proven_provider` (new state); `can_acquire()` now includes it.
- Bounded VOD ranges: yt-dlp `--download-sections` retrieves only the approved timestamp range (not the whole stream).
- Next implementation order documented (NOT built): wire Instagram UI → YouTube → Twitch/Kick clips → TikTok → Reddit → X → bounded VOD.

### Do not (per Bryan)
- No Actor calls, no spend, no download, no UI wiring, no ceiling change, no OpenShorts/OBS/GHL/publish.

### Note
- Instagram UI (Discord/extension) NOT wired to the provider — do NOT claim the operator workflow is production-ready.

---

## STOP STATE — 2026-09-17 ~20:22 EDT — PHASE 3 PROOF SUCCEEDED (first paid acquisition)

### Acceptance evidence (verified)
- Actor: `apify/instagram-reel-scraper` (API id `xMc5Ga1oCONPmWJIa`), build 0.0.569, run `0DPOKk2bEwxqVTS0i`.
- Output: exactly 1 result, content id `DdZS1QRhrXE`, field `videoUrl`.
- Media: h264 + aac (muxed, has_audio=true), 720x1280, 30 fps, 55.26s, 23.7 MB.
- **Actual billed cost: $0.0026** (2600 microdollars) — estimate == actual.
- Local: `~/Movies/LOOM-Reaction-Library/library/instagram__DdZS1QRhrXE__9b22f887b2ff.mp4` (sha256 `9b22f887b2ff…`).
- IDs: job `4bc99e59` (acquired) · library `acb7aa0f` · provider run `ef8c57e8`.
- Circuit breaker: lifetime_spent=2600 micro, succeeded=1 (ceiling $1.00 / max 10).
- Hygiene: signed URL volatile (not persisted); temp files removed; source_host redacted.

### Key findings (verification gaps resolved)
- Output field = `videoUrl` (confirmed). `videoUrl` is already muxed WITH audio — no separate `audioUrl` needed.
- One URL → one result (count=1); playable MP4 with audio confirmed.
- Zero-result billing still unverified (this run produced 1 result).

### Next
- Instagram acquisition is now runtime-proven end-to-end at $0.0026/Reel. Nothing blocked.
- Still out of scope: OpenShorts / OBS / GHL upload / publishing / distribution.

---

## STOP STATE — 2026-09-17 ~20:05 EDT — PHASE 2 COMPLETE (provider boundary built; awaiting migration + runtime-proof auth)

### Phase 2 — paid-provider boundary implemented (deterministic, $0)
- `infra/acquisition/provider.py` — platform-neutral boundary: pinned actor
  allowlist (reel-scraper default + instagram-scraper INACTIVE fallback),
  single-URL/maxResults=1 input, output validation (exactly-one, content-id
  match, https+approved-host+no-creds video URL, 5-min duration ceiling),
  authorization verification, provenance (signed URL never persisted).
- `infra/acquisition/cost.py` — microdollar reservation + reconciliation +
  circuit breaker ($1 ceiling + max 10 successes).
- `infra/acquisition/apify_client.py` — token load (presence-only; TOKEN_MISSING
  discloses nothing) + Transport interface + HttpTransport (untested).
- `infra/acquisition/orchestrate.py` — end-to-end dispatch with injected
  transport/downloader/finalizer (mock-testable).
- Migrations `034_loom_provider_runs.sql` + `035_loom_provider_ceiling.sql`
  **APPLIED + verified** (Bryan, 2026-09-17 20:06 EDT; both tables HTTP 200,
  ceiling seed row = $1.00 / max 10 / disabled=false).
- Tests: 380 passing (acquisition 109 = +33 provider; content 55; local-helper
  76; discovery 62; discord-review 78).

### Phase 2 must-NOTs honored
- No Actor call, $0 spent, no token use, no media download, no migration
  applied, no fallback activation, no other platform, no OpenShorts/OBS/GHL.

### Next — Phase 3 (UNAUTHORIZED)
- One real Reel proof on `https://www.instagram.com/reel/DdZS1QRhrXE/`.
- Expected max cost: $0.0026 (one metadata result). Verify zero-result billing,
  exact output field name, one-URL→one-result, playable muxed MP4 with audio,
  signed-URL expiry.
- Requires: migrations 034/035 (APPLIED + verified). Remaining: explicit
  Phase 3 go-ahead for the single Reel proof.

---

## STOP STATE — 2026-09-17 ~19:45 EDT — LOCAL INSTAGRAM CLOSED OUT; APIFY PHASE 1 DONE (awaiting approval)

### Local Instagram — crossed pivot threshold (verified + closed out)
- Final proof failed locally with `INSUFFICIENT_FMP4_SET` (no assemblable
  manifest/init+segments set). The extension failed closed BEFORE any helper POST —
  helper.log shows no `/acquire` request post-restart; no new job row; library +
  tmp dirs empty; no signed URL leaked. Honest record: an `INSUFFICIENT_FMP4_SET`
  failure is pre-dispatch, so it creates NO acquisition-job row.
- Instagram capability → `blocked_local_fmp4_incomplete` (new state). Candidates
  ladder → `["chrome-extension", "apify"]`. Local code retained, NOT deleted,
  NOT further repaired.

### Apify paid-provider fallback — Phase 1 (broadened provider search, $0)
- DECISION (2026-09-17): **HOLD** `agentx` / `thenetaji` / `crawlerbros`.
- Scalable path found: **official Apify Actors** return a direct video CDN URL in
  the base metadata result (no "stored file" premium, no usage charge) at
  **$0.0026–0.0027/Reel** — ~4× under the ≤$0.01 target, ~110× cheaper than
  `agentx`. See `infra/acquisition/APIFY_INSTAGRAM_ACTOR_EVALUATION.md`.
- Top 3: (1) `apify/instagram-reel-scraper` (official, $0.0026, 145K users) =
  recommended; (2) `apify/instagram-scraper` (official, $0.0027, 396K users) =
  fallback; (3) browser-extension manual = $0 emergency route.
- Provider-neutral boundary: `infra/acquisition/APIFY_PROVIDER_BOUNDARY.md`
  (15 shared contracts, 2 intake lanes, cost controls, secret handling,
  migrations 034/035 identified, 14-case mock-test plan).
- Budget: keep $1 hard ceiling — at $0.0026/Reel it covers ~380 Reels; prior
  "max 3" constraint obsolete. One URL + one result per run, no auto-retry.
- $0 spent. No Apify call. Token present-but-unused (presence verified only).

### STOP — awaiting Bryan's approval before Phase 2
- Phase 2 (only after approval): add token securely; build provider adapter +
  mocks; cost/circuit persistence; tests; stop before any real call.
- Phase 3 (separately authorized): ONE proof on
  `https://www.instagram.com/reel/DdZS1QRhrXE/`.
- Hard gates unchanged: no token use, no Apify call/spend, no other platform
  activation, no OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~19:10 EDT — SESSION HANDOFF (fMP4 built; proof authorized, NOT run)

### Git
- `master` @ `1d6a53b` "feat(acquisition): fMP4 assembly path", pushed to origin/master, clean tree (HEAD == origin).

### Migrations 018–033 — ALL APPLIED + verified
- 018–030 applied (briefs/discovery/topic/reviews/manual-submissions/tenants/routing).
- 031 `loom_acquisition_jobs` + 032 `loom_reaction_library` applied + verified (HTTP 200).
- 033 `adapter` + `is_non_production` columns applied + verified (HTTP 200 on both).

### Completed + runtime-proven (unchanged)
- Manual Source Intake (card wizard) + tenant routing (`/assign-source`) + Source
  Review Inbox (`/source-review`) — human-accepted.

### Acquisition Boundary + Reaction Library contracts (built + tested)
- `infra/acquisition/`: acquisition.py, reaction_library.py, platform_capabilities.py,
  media_filter.py, browser_flow.py.

### Chrome extension + localhost helper (architecture + security)
- extension (MV3): `Acquire for Reaction` flow; activeTab+scripting+debugger+storage;
  instagram + `127.0.0.1:8787` host perms only; no cookies/history; signed URLs volatile.
- helper `127.0.0.1:8787`: token + `chrome-extension://` origin auth; https + approved
  Meta/IG CDN suffix; validated redirects; reject creds/ports/private/suffix-confusion;
  one-item / ≤100 MiB / ≤5 min; atomic finalize; classified reasons.

### Instagram tool findings
- yt-dlp: anonymous "login required". gallery-dl: "redirect to login".
- Cobalt (sidecar): anonymous resolves but returns JPG cover (misclassification) — not video-capable.

### Instagram human-proof history (failures → repairs)
- browser probe found Instagram CDN video traffic (human-proven).
- `/reel/` → `/reels/` redirect → content-id normalization repair (supports /reel/, /reels/, /p/).
- unexpected-navigation abort repair (content-id lock + reattach).
- direct `.mp4` candidates were fMP4 fragments (moof/styp/sidx), NOT standalone MP4.
- ffprobe failure + leftover `.part` forensic (3.4 MB moof fragment) → cleanup + signature-classification repairs.
- duplicate failed-job false-success UX repair (distinguish duplicate vs saved).
- **NO valid Reaction Library asset produced yet (0 library rows).**

### fMP4 assembly implementation (commit 1d6a53b)
- `manifest.py`: MPD/M3U8 parse → init + ordered segments + audio detection.
- `assemble.py`: plan_assembly + concatenate + fixed ffmpeg args + failed-job retry.
- `fmp4.py`: download manifest/segments → concatenate → ffmpeg remux/mux.
- helper: dispatch manifest/segments/direct; retry; aggregate ceilings; cleanup on all failures.
- extension: prefer manifest, fallback init+ordered fragments; Capturing/Assembling phases.
- signed-URL non-persistence; atomic finalization; failed-job retry.

### Tests
- **325 passing** (62 discovery + 55 content + 78 discord-review + 61 acquisition + 69 local-helper).

### Helper status + safe restart (token NOT recorded)
- Running PID 66821 on `127.0.0.1:8787` (token configured).
- Restart: `cd infra/local-helper && pkill -f helper.py; sleep 1; rm -f helper.pid helper.log; chmod +x run.sh; nohup ./run.sh > helper.log 2>&1 & echo $! > helper.pid`.

### Authorized proof (freshly authorized, NOT yet run)
- `https://www.instagram.com/reel/DdZS1QRhrXE/` — one item, ≤100 MiB aggregate, ≤5 min, Personal Reaction Library, non-production. **NOT run after fMP4 assembly.**

### Pivot boundary
- Allow THIS proof + at most ONE additional controlled Reel proof.
- If reliable local assembly cannot be achieved → evaluate Apify as an Instagram-only fallback under a **$1 initial hard ceiling**.

### Deferred
- TikTok/Reddit/X/YouTube/Twitch/Kick adapters; OpenShorts; OBS automation; GHL; publishing/distribution.

---

## STOP STATE — 2026-09-17 ~18:35 EDT — ACQUIRE NAVIGATION ABORT FIXED (awaiting re-test)

**Human test FAILED ("Failed: tab navigated") then diagnosed.** Root cause: the
Acquire-flow auto-reload ("replay if idle") triggered a tab navigation that the
onUpdated handler aborted on, before handing a candidate to the helper. "Inspect
Playing Media" (no reload) proved detection works (2 direct_video candidates, 0
rejections).

**Fix (`infra/acquisition/browser_flow.py` + background.js):**
- Removed the auto-reload — Acquire now attaches + observes (same path as Inspect).
- Navigation classified by content id: same-content reload tolerated + re-inspected
  (onDetach -> reattach + Network.enable); different content id fails closed.
- Deterministic selection (media-type priority + most-recent) + exactly-once handoff.
- 12 deterministic tests (browser_flow). 293 total.

Authorized proof unchanged: reel `DdZS1QRhrXE`. No download during repair.

---

## STOP STATE — 2026-09-17 ~18:20 EDT — LOCAL ACQUISITION HELPER + ACQUIRE FLOW BUILT (awaiting final auth)

**Browser-assisted Instagram resolution is HUMAN-PROVEN** (1 direct MP4
candidate: `scontent-det1-1.cdninstagram.com`, `video/mp4`, 0 rejections).

**Built the local acquisition helper + extension "Acquire for Reaction" flow:**
- `infra/local-helper/` — validate.py (https + approved host suffix + no creds/
  port + suffix-confusion), acquire.py (validated-redirect download + byte
  ceiling + atomic rename + ffprobe + sha256 + payload building), helper.py
  (127.0.0.1:8787 POST /acquire, token + chrome-extension origin auth, strict
  schema + 8 KB body limit, redacted result — never the signed URL).
- `infra/chrome-extension/` v0.4.0 — primary `Acquire for Reaction` button +
  phases (Inspecting → Media found → Sending → Downloading → Validating → Saved/
  Failed). Full signed URL held in volatile memory only, POSTed to the helper.
- Migration `033_loom_acquisition_adapter.sql` (adapter + is_non_production
  columns) — **staged, NOT applied**.
- Token: high-entropy, `infra/local-helper/.token` + `config.js` (gitignored).
- Instagram capability: resolution browser-assisted HUMAN-PROVEN; acquisition
  `implemented_not_proven`.
- Latency: 12s is a fail-closed MAX, not a wait — `Acquire for Reaction` stops +
  detaches on the first unambiguous direct MP4 (~1–3s autoplay), deterministic
  selection for multi-candidate, replay-if-idle, actionable retry.

**NOT done (gated):** migration 033 application; real MP4 download; helper start.

---

## STOP STATE — 2026-09-17 ~18:05 EDT — NETWORK PROBE REPAIRED (inconclusive test diagnosed)

**First network-probe human test was INCONCLUSIVE (not a failure):** zero
candidates + a raw `null` (popup-close state loss) while the video was playing.
NOT classified as blocked.

**Repaired the test flow** (`infra/chrome-extension/` v0.3.0):
- State persisted in `chrome.storage.session` → reopening the popup never shows
  raw `null`.
- Explicit phases: Ready → Attaching → Inspection active (replay/reload now) →
  Collecting → Complete | No new media traffic observed | Failed(reason), with a
  live 12s countdown + attached/detached status.
- **Inspect + reload Reel** option (attach first → clear in-memory candidates →
  reload active tab → observe fresh load) — fixes the likely "already buffered"
  cause.
- Cached responses captured (`fromDiskCache`/`fromMemoryCache`).
- Redacted rejection counts by reason (hostname/scheme/resource-type/content-type/
  image/analytics-ad/accepted) — `media_filter.py` `classify_request` +
  `summarize_inspection` (never null) + 10 new tests.

Instagram VIDEO still NOT classified blocked. No download, no cookies, no Apify/
OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~17:55 EDT — CHROME blob/MSE CONFIRMED; NETWORK PROBE BUILT

**Chrome stage-1 result:** `isBlobOrMse: true`, `isDirectUrl: false` — the simple
`<video>.src` path is NOT feasible. Wording updated to "Blob/MSE detected —
direct source unavailable; network inspection required" (not a generic failure).

**Built the second-stage network probe** (`infra/chrome-extension/` v0.2.0):
- Stage 1 (activeTab+scripting): video element + `buffered` + `<source>` +
  `performance` resource entries (media-like URLs).
- Stage 2 (`debugger`): user-initiated bounded 12s CDP Network probe — attach only
  on explicit click, active tab only, metadata only (never bodies/cookies/auth),
  auto-detach on timeout/navigation/close, redacts signed tokens.
- Filtering contract `infra/acquisition/media_filter.py` (https + approved
  Meta/IG CDN host suffix + video/audio/manifest signature; rejects JPG/HTML/ads)
  + 15 deterministic tests. JS mirrors it.

Instagram VIDEO still `blocked_pending_adapter`. No download, no cookies, no
Apify/OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~17:45 EDT — INSTAGRAM VIDEO: ALL TOOL PATHS FAIL; CHROME-EXT BUILT

**Ladder result (anonymous, no cookies):**
- yt-dlp: ❌ "login required"
- Cobalt: ⚠️ resolves but returns **`.jpg` cover** for video (misclassification —
  confirmed on BOTH video reels `DdQE_RUS0GJ` + `DdZS1QRhrXE`). Not video-proof.
- gallery-dl 1.32.12: ❌ "HTTP redirect to login page"
- Chrome extension: **built** (`infra/chrome-extension/`, MV3, activeTab+scripting+
  instagram host only) — browser-assisted capture of direct media URL; feasibility
  UNPROVEN (pending Bryan's install + open-Reel test).

Instagram VIDEO acquisition = `blocked_pending_adapter`. No cookies, no JPG
download, no tunnel fetch, no Apify/OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~17:35 EDT — COBALT SIDECAR PROVEN (anonymous Instagram resolution works; image finding)

**Migrations 031 + 032 APPLIED + verified (both tables HTTP 200).**

**Cobalt sidecar installed + anonymous resolution PROVEN:**
- `infra/cobalt/docker-compose.yml` — image pinned by arm64 digest (cobalt 11.7.1),
  host `127.0.0.1:9000` only, API-key auth (gitignored `keys.json`), Instagram-only
  service, `DURATION_LIMIT=300` (5 min). Fixed loopback-binding gotcha.
- POST `/` resolution test: `status:"tunnel"` → Cobalt resolves Instagram
  **anonymously (no cookies)**.
- **Finding:** the acceptance URL `…/reel/DdQE_RUS0GJ/` resolves to a **`.jpg`
  image** (all three modes) — NOT a video. **JPG is NOT accepted as video proof.**
  Instagram VIDEO acquisition stays `implemented_not_proven` (not runtime-proven).
  Pending Bryan's verification: photo/carousel → he supplies a confirmed video
  Reel; or it plays as video → record as Cobalt misclassification. Cobalt sidecar
  KEPT RUNNING. No tunnel fetch until a confirmed video Reel + explicit go-ahead.

No media downloaded. No cookies. No OpenShorts/OBS/GHL/publish.

---

## STOP STATE — 2026-09-17 ~17:25 EDT — ADAPTER EVALUATION COMPLETE (awaiting install/proof approval)

**Acquisition adapter ladder evaluated; first output produced (commit `a4884e1`).**

- Adapter ladder: yt-dlp → streamlink → self-hosted Cobalt sidecar → gallery-dl →
  Chrome extension + local helper → (external provider, last resort).
- Tool inventory: yt-dlp 2026.03.13 + ffmpeg 8.0.1 + Docker 28.4.0 + Node 22 +
  supabase CLI present; streamlink + gallery-dl NOT installed.
- Capability matrix updated: yt-dlp-extractor platforms → `implemented_not_proven`;
  Instagram stays `blocked_pending_adapter` (anonymous yt-dlp = "login required").
- Cobalt: AGPL-3.0; run as separate contained sidecar (no source copied into LOOM),
  pin version, 127.0.0.1 bind, API-key protected; needs `cookies.json` only for
  auth-required content (we will not provide cookies).
- Full assessment in `infra/acquisition/ADAPTER_EVALUATION.md` (architecture, 14-dim
  platform matrix, Cobalt deployment, Chrome-extension design, Instagram proof
  plan, $0 cost, install/security decisions).
- Tests: **218 pass** (62 discovery + 55 content + 78 discord-review + 23 acquisition).

**NOT performed (awaiting approval):** media download, service install (Cobalt/
gallery-dl/streamlink), Chrome extension build, migrations 031/032 application,
Apify, cookie extraction.

---

## STOP STATE — 2026-09-17 ~17:10 EDT — ACQUISITION BOUNDARY V1 CONTRACTS BUILT (awaiting proof authorization)

**Acquisition Boundary V1 — deterministic contracts: DONE (no download).**

Product-semantics correction applied: `Acquire for Reaction` is PRIMARY (records
authorization + creates an idempotent job immediately; allows Unassigned; routes
to Personal Reaction Library; never authorizes publishing). `Save Link Only` is
secondary. `Cancel` is third. No ambiguous `Submit Source` label.

Built (all deterministic, zero download):
- `infra/acquisition/platform_capabilities.py` — capability states + honest
  acquisition-readiness matrix (NO platform runtime-proven yet; YouTube =
  implemented_not_proven; others blocked_pending_adapter).
- `infra/acquisition/acquisition.py` — durable idempotent acquisition-job
  contract (`plan_acquisition_job`, `dedup_key`, status machine, collection
  rejection, bounded-range validation, `persist_job`).
- `infra/acquisition/reaction_library.py` — Personal Reaction Library contract
  (sha256 dedup, deterministic filename, bounded retention, immutable provenance).
- Migrations `031_loom_acquisition_jobs.sql` + `032_loom_reaction_library.sql`
  (SQL files staged; **NOT applied — Bryan applies**).
- Plan/capability matrix + storage proposal in `infra/acquisition/ACQUISITION_BOUNDARY.md`.
- 22 new deterministic tests.

Tests: **217 pass** (62 discovery + 55 content + 78 discord-review + 22 acquisition).

**STOP GATE — not crossed:** no real media download. Awaiting Bryan review of
storage destination (proposed: GHL Media Storage files + Supabase ledger) + the
adapter limits + proof target, and explicit authorization for ONE bounded,
non-production acquisition proof.

Stop conditions remain: no publishing/distribution; no Chrome extension; TikTok
held; Instagram/Twitch/Kick `blocked_pending_adapter`.

---

## STOP STATE — 2026-09-17 ~16:50 EDT — CARD-BASED INTAKE WIZARD DEPLOYED (awaiting Bryan test)

**Manual Source Intake — card-based ephemeral wizard: DONE (commit `10430c8`).**

Replaced the linear `/submit-source` slash form with a compact ephemeral card:
- `/submit-source` takes only the required `url` (Discord min-1-input).
- Validates + canonicalizes without persisting; returns an ephemeral "Manual
  Source Intake" card (platform, canonical URL, destination, format, timestamps,
  note, candidate-only notice).
- `Choose Destination` + `Choose Format` select menus (active `loom_tenants` +
  "Unassigned / General"); `Add Details` modal (start/end/note); `Submit Source`
  (green, disabled until format chosen); `Cancel` (gray).
- Persists ONLY on Submit → final ephemeral confirmation card (submission id,
  platform, destination, format, routing, platform-support, duplicate/new,
  candidate-only, "No media downloaded").
- In-memory expiring session store (15 min TTL) keyed by unguessable token;
  atomic consume guards double-submit/replay; compact deterministic custom_ids
  (`mi:<action>:<token>`); no secrets/user data in custom_ids.
- Fix: strip Instagram `stkn` tracking param in canonicalization.

Commands re-registered (url-only `submit-source`). Adapter restarted; `/health` 200.
Tests: **195 pass** (62 discovery + 55 content + 78 discord-review; +17 intake).

**NOT run: acceptance test.** Awaiting Bryan's real Discord Instagram test.

Stop conditions remain: no Chrome extension; no Acquisition Boundary;
TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support`.

---

## STOP STATE — 2026-09-17 ~16:45 EDT — HUMAN ACCEPTANCE REPAIR COMPLETE (awaiting Bryan re-test)

**Manual Source Intake V1.1 Human Acceptance Repair: DONE (commit `149a7b4`).**

Diagnosis confirmed:
1. **Stale adapter** — the running process (PID started Sep 15 22:53) predated the
   type-2 slash handler (code modified Sep 17). `Unsupported interaction.` came
   from the stale process, NOT the committed code. Restarted via `supervise.sh`;
   `/health` 200; now running the current handler.
2. **Free-form `destination`** — `/submit-source` `destination` is now a Discord
   **autocomplete** option (sourced from active `loom_tenants` + "Unassigned /
   General"). Added `itype == 4` APPLICATION_COMMAND_AUTOCOMPLETE handler.
3. **Result message** now surfaces BOTH dimensions: `Routing: Awaiting tenant
   assignment` / `Assigned to <tenant>` AND `Platform support: Supported` /
   `Pending Instagram support` — plus canonical URL (tracking stripped),
   submission ID, and candidate-only wording (no analysis / score / acquisition /
   download).

Commands re-registered (source-review / submit-source / assign-source: 200 OK).
Tests: **178 pass** (62 discovery + 55 content + 61 discord-review; +3 new).

**NOT re-run: acceptance test.** Per stop conditions, awaiting Bryan's Discord
re-test of the Instagram Reel URL before proceeding.

Stop conditions remain: no Chrome extension; no Acquisition Boundary;
TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support`.

---

## STOP STATE — 2026-09-17 ~16:35 EDT — SESSION HANDOFF (human acceptance FAILED)

**Manual Source Intake V1.1: deterministic + runtime-proven, but NOT human-acceptance complete.**

### Status matrix (explicit)
| Layer | Status |
|---|---|
| Code built | ✅ |
| Deterministic-tested | ✅ 175 tests (62 discovery + 55 content + 58 discord-review) |
| Automated runtime-proven | ✅ routing 15/15; manual-intake 12/12 |
| Human-acceptance | ❌ FAILED (2 defects) |
| Chrome extension | ⛔ NOT started / not authorized |
| Acquisition Boundary | ⛔ NOT started / not authorized |

### Human acceptance test — FAILED
Bryan invoked `/submit-source` in Discord with an Instagram Reel. Defects observed:
1. **`destination` is free-form** — required Bryan to type `unassigned`; no controlled options/autocomplete. Intended UX: selectable `Unassigned / General` + active production tenants (from `loom_tenants`).
2. **Bot replied ephemeral `Unsupported interaction.`** — the registered command reached the generic fallback, not the slash-command handler.

### Root-cause hypothesis (NOT yet diagnosed — diagnose before editing)
The COMMITTED `adapter.py` already has the type-2 slash handler
(`itype == 2` → `_handle_slash_command` → `_handle_submit_source_command` at
lines ~1657/1678/1726) and the `Unsupported interaction.` fallback at ~1670.
This strongly implies the RUNNING supervised adapter process is STALE (started
before the slash-command code) and must be restarted to pick up the current
handler. Command registration (Discord API) and deployed handler versions likely
differ. `destination` needs Discord choices or autocomplete sourced from active
`loom_tenants` + Unassigned.

### Instagram acceptance-test URL (recorded)
`https://www.instagram.com/reel/DdQE_RUS0GJ/?utm_source=ig_web_copy_link&stkn=MzRlODBiNWFlZA==`

Intended successful result (both must show):
- Routing: **Awaiting tenant assignment**
- Platform support: **Pending Instagram support**
- Plus: canonical URL with tracking params removed; submission ID; candidate-only
  status; NO analysis, score, acquisition or download.

### Next-session first authorized task
`Manual Source Intake V1.1 Human Acceptance Repair` — diagnose BEFORE editing:
1. Is the deployed/running adapter using the current command handler (check
   `infra/discord-review/adapter.log` / `supervisor.log`; restart via `supervise.sh`).
2. Why does the registered command hit `Unsupported interaction` (stale process).
3. Do command registration vs deployed handler versions differ.
4. Implement Discord choices/autocomplete for `destination` from active tenants + Unassigned.

### Stop conditions
- Do NOT begin the Chrome extension.
- Do NOT begin Acquisition Boundary.
- Do NOT re-run the acceptance test until the flow is fixed and Bryan confirms.
- TikTok/Instagram/Twitch/Kick remain `accepted_pending_platform_support` (no scraping/activation).

---

## STOP STATE — 2026-09-17 ~16:30 EDT

**Manual Source Intake V1.1 — Unassigned Routing + Controlled Tenant Assignment: COMPLETE (built + tested + runtime-proven).**

- Migrations **028/029/030 APPLIED**: `loom_tenants` registry (seeded `dreemzy`), `loom_routing_events` audit, per-tenant dedup (`unique(duplicate_fingerprint, tenant_id)`) + `qualification_required`.
- **tenant vs profile resolved**: `tenant_id` = owning business/account; `profile_id` = content niche (derived server-side, never asked from Bryan). `/submit-source` now takes a single `destination` (`unassigned` or active tenant).
- **`/assign-source`** command registered (id `1550241802446180463`) — assign / reassign / copy.
- `tenant_routing.py` — pure `plan_routing` (assign/reassign/copy/collision/blocked/noop) + `route_submission` (downstream lock, collision, requalification).
- **175 deterministic tests** (62 + 55 + 58).
- **Routing runtime proof 15/15** (unassigned → assign → reassign → copy → collision → downstream block → immutable audit).
- **Routing changes set `qualification_required=true`; scores are never copied or fabricated across tenants.**

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~16:15 EDT

**Manual Source Intake V1: COMPLETE (built + tested + runtime-proven).**

- Migration **027 `loom_manual_submissions` APPLIED** (first-wins dedup).
- **`/submit-source`** guild command registered (id `1550237873637425192`).
- `manual_intake.py` — strict platform allowlist (YouTube/Reddit/TikTok/Instagram/Twitch/Kick); SSRF/credential/private-host/port/scheme rejection; deterministic canonicalization (no fetch/redirect); dedup; status mapping.
- **160 deterministic tests** (62 discovery + 55 content-intelligence + 43 discord-review).
- **Runtime proof 12/12** (command registered; YouTube accepted + canonicalized + persisted; dedup first-wins; TikTok pending-platform; SSRF/credential/host/scheme rejections; proof profile excluded).
- **A manual submission is a CANDIDATE only — never approval for acquisition, never analyzed, never a fabricated score.**

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~15:40 EDT

**Source Review Inbox: COMPLETE (built + tested + runtime-proven).**

- Migration **024 `loom_source_reviews` APPLIED** (first-decision-wins per `duplicate_fingerprint`).
- **`/source-review`** guild slash command registered (Clippy Factory, id `1550229456462028881`).
- `source_review.py` — combined candidate cards (market + content + selection, honest content-confidence), Approve-for-Acquisition / Hold / Reject.
- `adapter.py` — type-2 slash command + source-button routing.
- **128 deterministic tests** (62 discovery + 55 content-intelligence + 11 source-review).
- **Safe runtime proof 8/8** (Discord card post+delete; decision persistence idempotent; production-queue exclusion).
- **APPROVE FOR ACQUISITION records authorization only — triggers nothing** (no download/OpenShorts/publish).

Zero download. No OpenShorts/Acquisition/publishing. Proof profile auto-excluded.

> **Fixture distinction (recorded):** the Linus Tech Tips video used in the demo card is a UI/test fixture ONLY. Its appearance does NOT constitute tenant relevance or source-selection acceptance. Before production, candidate qualification must enforce the selected tenant's niche, audience, content strategy, exclusions and desired reaction formats — a generally-popular video must not enter a tenant's queue merely because it performs well globally.

---

## STOP STATE — 2026-09-17 ~15:30 EDT

**Content Intelligence Operationalization V1.1: COMPLETE (recurring pipeline runtime-proven).**

- A/B (same transcript): **Gemini Flash-Lite vs Qwen** — Gemini is the default transcript-triage provider (2.6s vs 146s; 560 vs 5798 output tokens; both schema-valid + evidence-grounded).
- **Pinned model:** `gemini-3.5-flash-lite` (resolved from `gemini-flash-lite-latest`); Qwen `qwen/qwen3.5-9b` (OpenRouter/SiliconFlow) as explicit secondary (no silent fallback).
- **Real provider layer** promoted: `providers.py` (GeminiProvider + QwenProvider — classified failures, cost/token accounting, model identity persistence, no credential logging).
- **Recurring `pipeline.py` (Pass1Pipeline):** discovery → Topic Intelligence → market shortlist → transcript-only (yt-dlp skip-download, 0 media bytes) → Content Intelligence → Combined Selection → idempotent Supabase. Hard limits + $0.05 run cost ceiling.
- **Selection queue contract:** `selection_queue()` — completed selections only (7 outcomes).
- **117 deterministic tests** (62 discovery + 55 content-intelligence).
- **Recurring-pipeline runtime proof 9/9** ($0.00071, non-production, idempotent, excluded).

Zero full-video download. No Acquisition/OpenShorts/Discord/publish. Proof profile auto-excluded.

---

## STOP STATE — 2026-09-17 ~15:05 EDT

**Content Intelligence Preflight V1 + Combined Source Selection V1: BUILT + TESTED + Pass-1 RUNTIME-PROVEN.**

- Evidence-level contract (metadata/text/static-visual/bounded-media) + per-dimension gating + confidence ceilings.
- Strict Content Assessment schema + validation (rejects malformed / out-of-bounds / evidence-exceeding claims).
- Combined Source Selection (35% market + 45% content + 15% fit + 5% provenance) + separate confidence + decision rules.
- Bounded-media probe contract (temp lifecycle, hard limits, no full download).
- Migrations 025/026 **APPLIED** (Bryan, 2026-09-17).
- **104 deterministic tests** (62 discovery + 42 content-intelligence).
- **Pass-1 real proof 13/13 PASS** (Gemini Flash-Lite, transcript-only, cost ≈ $0.002 < $0.05 ceiling):
  - real YouTube candidate (Linus Tech Tips `qgLaCZyKv_8`), English auto-captions via yt-dlp (skip-download, 0 video/audio bytes);
  - market=59 (Topic Intelligence) + content_worthiness=68 (Gemini) → selection=60, outcome=standard_review;
  - evidence-gated (level 1: no visual/audio claims; confidence capped 0.33);
  - real transcript-derived timestamps + reasons; idempotent Supabase rows.

Zero full-video download. No Acquisition asset, no OpenShorts job, no Discord
card, no publishing/distribution. Proof profile auto-excluded from production.

---

## STOP STATE — 2026-09-17 ~13:50 EDT

**Topic Intelligence V1: SEGMENT COMPLETE (built + tested + runtime-proven — local AND Supabase).**

- Deterministic opportunity scoring layer built on top of Source Discovery V1.
- Platform-aware normalization (YouTube vs Reddit) without pretending metrics are identical.
- Velocity computed ONLY from valid time-separated observations (honest, no fabrication).
- 7-signal weighted score → opportunity class (strong/moderate/weak/stale/duplicate/incomplete) + confidence + reason codes + missing-data flags.
- `content_format` routing field added (default `hold_unassigned`).
- Assessment store: SQLite (built+tested) + Supabase (built + runtime-proven).
- Migrations 022/023 **APPLIED** (Bryan, 2026-09-17).
- Real-data proofs: local SQLite **28/28 PASS**; Supabase **13/13 PASS**.

Zero cost. Zero downstream side effects. No media download, no OpenShorts job,
no publication, no distribution. No candidate advanced beyond `manual_ingest_only`.

**No source was downloaded, processed, clipped, published, or distributed.**

---

## STOP STATE — 2026-09-16 ~02:15 EDT

**Automated Source Discovery V1: SEGMENT CLOSED.**

- **Segment 2 — Foundation: COMPLETE.**
- **Segment 3 — YouTube connector milestone: RUNTIME-PROVEN (Supabase-backed).**
- **Segment 3 — Reddit connector milestone: RUNTIME-PROVEN (Supabase-backed, 12/12).**

Zero cost. Zero downstream side effects (no download, no OpenShorts job, no
publication, no distribution).

**No source was downloaded, processed, clipped, published, or distributed.**

---

## Segment boundary (roadmap overlap resolved — SEGMENT CLOSED)

- **Segment 2 — Automated Source Discovery V1 Foundation (COMPLETE).**
  Exit criteria met: Source Brief contract; normalized discovery schema;
  identity + duplicate handling (deterministic tests); shared connector
  interface; qualification-before-acquisition gate; three profile fixtures
  validate.
- **Segment 3 — Initial Connector Proofs (FIRST MILESTONE RUNTIME-PROVEN).**
  Real YouTube source discovered → normalized → deduplicated → evidence stored
  → qualification applied → one invalid/duplicate rejected → one eligible source
  passed to the manual-ingest boundary → no automatic publication.
- **Segment 3 — Reddit connector milestone (RUNTIME-PROVEN):** connector built
  via first-party RSS (no credential, no scraping); 38 deterministic tests pass;
  25 real r/technology posts discovered, stored, deduped, qualified; explicit
  duplicate/cross-post + policy rejections verified; one eligible source reached
  manual-ingest boundary. Supabase proof 12/12.

TikTok remains **held**. X deferred. Instagram/Facebook planned. Do not start
TikTok until explicitly authorized.

---

## What was built (`infra/discovery/` — Topic Intelligence V1)

| Module | Responsibility |
|---|---|
| `normalization.py` | Platform-aware signal normalization → `NormalizedSignals` |
| `velocity.py` | Velocity from valid time-separated observations (honest) |
| `topic_intelligence.py` | Deterministic scoring + reason codes + opportunity class |
| `prove_topic_intelligence.py` | Bounded $0 real-data proof (local SQLite) |
| `tests/test_topic_intelligence.py` | 24 deterministic tests |

Plus `content_format` on `discovery_record.py` + both stores; migrations
`022_loom_topic_assessments.sql` and `023_loom_discovered_sources_content_format.sql`.

See `infra/discovery/TOPIC_INTELLIGENCE.md` for the full model.

## What was built (`infra/discovery/`)

| Module | Responsibility |
|---|---|
| `source_brief.py` | Versioned, tenant-scoped Source Brief contract + validation |
| `discovery_record.py` | Normalized discovery record schema + validation (reserves Topic Intelligence fields; computes none) |
| `identity.py` | Canonical URL normalization, content-ID extraction, tenant/global fingerprints, duplicate classification (no paid model) |
| `connector.py` | Shared connector interface (pagination, rate limits, auth-by-name, provenance, retry, health, cost, versioning) |
| `qualification.py` | Qualification-before-acquisition gate (deterministic, explicit rejection reasons) |
| `store.py` | Durable store (SQLite, dependency-free) — idempotent upsert + append-only metric history |
| `store_supabase.py` | Production Supabase store (service-role key by NAME only, no DDL, no download) |
| `connectors/youtube.py` | YouTube/search + competitor-channel discovery (first-party RSS, no credential) |
| `connectors/__init__.py` | Connector registry (youtube only; reddit/tiktok planned) |
| `prove_youtube_connector.py` | Bounded live connector proof |
| `prove_supabase_acceptance.py` | 12-criteria Supabase-backed acceptance proof |
| `fixtures/` | 3 non-provisioned profile Source Briefs (design/test examples only) |
| `tests/test_discovery.py` | 34 deterministic unit tests |

Plus `n8n-workflows/LOOM-06-Source-Discovery.json` (shared, tenant-driven,
**inactive** — never deployed/activated).

## Live YouTube proof (real, non-mocked — 2026-09-16)

- **Method:** first-party RSS feed (`/feeds/videos.xml?channel_id=`), no
  credential, no scraping. Keyword *search* (Data API v3) correctly raises
  `AuthRequiredError` for `YOUTUBE_DATA_API_V3_KEY` (name only, absent).
- **Channel:** Linus Tech Tips `UCXuqSBlHAE6Xw-yeJA0Tunw` (public, real).
- **Discovered:** 15 real candidates (e.g. `3xngArcFpek` "I Promise you this is
  NOT Stupid", `3PGKMwgjla0` "Steam Frame Review - This Changes Everything").
- **Normalized + stored:** 15/15, idempotent (fingerprint-keyed).
- **Duplicate rejected:** re-observing `3xngArcFpek` → upsert `updated`, row
  count unchanged; gate returned `rejected` with reason `duplicate (repost):
  global content id already observed`.
- **Qualified:** 15/15 `manual_review` (brief requires manual review).
- **Manual-ingest boundary:** `3xngArcFpek` passed → STOP (no download, no
  OpenShorts, no clip, no publish, no GHL post).
- Reproducible: `python3 infra/discovery/prove_youtube_connector.py`.

## Tests

`python3 -m unittest discover -s infra/discovery/tests` → **38 passed**.

## Reddit connector (RUNTIME-PROVEN, Supabase-backed 12/12)

`infra/discovery/connectors/reddit.py` — subreddit + bounded keyword discovery
via first-party Reddit RSS (no credential, no scraping, no auth evasion).
Captures post id, canonical URL, subreddit, author, title, body excerpt,
publication time, observed metrics (content_type + source URL refs), provenance
(honest note: RSS omits score/ups/comments). Cross-post dedup via author-agnostic
`crosspost_key`. 25 real r/technology posts discovered.

`prove_supabase_reddit.py` → 12/12 PASS against production Supabase: stored
(25/25) → replay idempotent → explicit exact-duplicate + cross-post rejection
→ policy rejection (missing provenance) → manual-ingest boundary only → no
forbidden modules / RSS-GET-only → proof profile excluded from production queue.

## Supabase acceptance proof (12/12 PASS, production Supabase)

`python3 infra/discovery/prove_supabase_acceptance.py` → 12/12 criteria pass:
real candidates discovered (n=15) → normalized + stored (15/15) → replay
idempotent (15, not 30) → metric-history append (1→2, prior preserved) →
explicit repost rejection → manual-ingest boundary only → no forbidden modules
imported / RSS-GET-only. See "Persisted state".

## Production-queue exclusion of acceptance/test profiles

`profile_id` now travels on every discovered record (from its Source Brief) and
is persisted. `is_non_production(profile_id, tenant_id)` marks acceptance/test/
example profiles. Normal production queue queries use
`production_sources()` / `production_fingerprints()` /
`production_global_fingerprints()`, which **exclude non-production profiles by
default**. The acceptance profile `supabase-acceptance-proof` is therefore
excluded from production queries automatically.

## Persisted state (production Supabase, kept intentionally)

- `loom_source_briefs`: 2 rows — profiles `supabase-acceptance-proof` (YouTube)
  and `reddit-acceptance-proof` (Reddit). Both non-production acceptance
  evidence, KEPT.
- `loom_discovered_sources`: **40 retained proof records — 15 YouTube +
  25 Reddit**, 40 distinct fingerprints (dedup holds). All non-production and
  auto-excluded from production queues.
  - YouTube: `qualification_status` 1 `manual_review` / 14 `pending`;
    `metric_history` on sampled candidate = 2 observations.
  - Reddit: 25 rows, all with `body_excerpt`; 1 `manual_review` / 24 `pending`.
  - `processing_eligibility`: 40/40 `manual_ingest_only`.

## Migrations

| # | Change | Status |
|---|--------|--------|
| 018 | `loom_source_briefs` (versioned tenant-scoped briefs) | **Applied** (Bryan, 2026-09-16) |
| 019 | `loom_discovered_sources` (normalized records, fingerprint-unique, append-only metric history) | **Applied** (Bryan, 2026-09-16) |
| 020 | `loom_discovered_sources.profile_id` (+ index) | **Applied** (Bryan, 2026-09-16) |
| 021 | `loom_discovered_sources.body_excerpt` | **Applied** (Bryan, 2026-09-16) |
| 022 | `loom_topic_assessments` (Topic Intelligence V1) | **Applied** (Bryan, 2026-09-17) |
| 023 | `loom_discovered_sources.content_format` | **Applied** (Bryan, 2026-09-17) |
| 024 | `loom_source_reviews` (Source Review Inbox) | **Applied** (Bryan, 2026-09-17) |
| 025 | `loom_content_assessments` (Content Intelligence V1) | **Applied** (Bryan, 2026-09-17) |
| 026 | `loom_source_selections` (Combined Selection V1) | **Applied** (Bryan, 2026-09-17) |
| 027 | `loom_manual_submissions` (Manual Source Intake V1) | **Applied** (Bryan, 2026-09-17) |
| 028 | `loom_tenants` (controlled destination registry) | **Applied** (Bryan, 2026-09-17) |
| 029 | `loom_routing_events` (assignment/reassignment audit) | **Applied** (Bryan, 2026-09-17) |
| 030 | `loom_manual_submissions` per-tenant dedup + `qualification_required` | **Applied** (Bryan, 2026-09-17) |

> Migrations 010–017 remain applied + live-verified (unchanged). 020 added
> `profile_id`; the 15 YouTube acceptance rows were backfilled to
> `supabase-acceptance-proof` and are excluded from production queries.
> 021 added `body_excerpt` (Reddit body capture).

## Git state

- **LOOM-Factory `master`** · HEAD `4cedf07` · clean · **pushed** to
  `origin/master` (https://github.com/Pettybytes/LOOM-Factory.git).
- Commit chain (this discovery build):
  1. `13a8dc6` — Source Brief contract + normalized schema + fixtures + migrations 018/019
  2. `0e23c54` — deterministic identity + duplicate handling
  3. `4b1da3e` — shared connector interface + qualification gate
  4. `8f03e34` — YouTube connector + durable store + live proof + tests
  5. `5ab5283` — README + shared n8n discovery workflow
  6. `d10f002` — explicit exact-duplicate classification
  7. `205f1bf` — docs: discovery V1 handoff
  8. `708d472` — production Supabase store + full acceptance proof (12/12)
  9. `4cedf07` — profile_id on records + production-queue exclusion
- No secret values in any commit. No destructive git/db operations. Unrelated
  dirty files (nested OpenShorts repo) left untouched.

## Costs incurred

**$0.00.** No paid model calls. No new paid service. RSS is free. Docker
Desktop left running (existing containers up: `loom-tunnel-proxy`,
`openshorts-backend`, `openshorts-renderer`, `openshorts-frontend`).
`caffeinate` already active (PID 55082).

## Credentials required (names only — never values)

- `YOUTUBE_DATA_API_V3_KEY` — needed to enable YouTube keyword *search*
  (Data API v3). Channel discovery already works via free RSS without it.
- Reddit: **none** — first-party RSS works with no credential. The public JSON
  API is 403 unauthenticated and the OAuth API requires app registration
  (neither used).
- Supabase DDL path — either the Supabase **SQL editor** or the **database
  password** for `supabase link` + `supabase db push`.

## Manual actions required (Bryan)

1. (Optional) Provide `YOUTUBE_DATA_API_V3_KEY` for YouTube keyword search.
2. (Nothing blocking — migrations 018–021 all applied.)

## Next action

- Migrations 018–030 all **APPLIED**. Manual Intake V1.1 routing **RUNTIME-PROVEN (15/15)**.
- **Next segment: Acquisition Boundary** — ingest candidates Bryan approved-for-acquisition (still no download/publish without further authorization); optionally wire accepted YouTube/Reddit manual submissions into the existing qualification pipeline.
- **Pass 2 (bounded media)** — deferred; needs separate approval.
- **TikTok/Instagram/Twitch/Kick** — submissions accepted as `accepted_pending_platform_support` (no scraping/activation).

---

## 🔒 Locked architecture (editor path — unchanged, do not modify)

```text
durable clean source (loom_media.clean_source_url, filesafe CDN)
  → session-bound temporary OpenShorts job (output/loom-<session>/, reconstructed)
  → native OpenShorts endpoints (/api/clip/rerender, /api/hook, /api/subtitle, …)
  → immutable LOOM version (register_edited_asset RPC, render-<major>.<minor+1>)
  → exactly one Discord final_publication review card
```

- The OpenShorts editor is **NOT stateless**: it needs a live `output/<job_id>/`
  workspace (metadata JSON + clip files) and an in-memory `jobs[id]` entry.
- LOOM's adapter (`infra/discord-review/adapter.py`) rehydrates that workspace on
  demand from the durable clean source, so editing survives OpenShorts job eviction.
- The client (Discord Activity / editor) never chooses the temp job id; it is
  derived server-side from the edit session and translated transparently in
  `_handle_editor_api`.

## ✅ Real E2E proof (editor, unchanged)

Fresh non-production clip `loom-e2e-proof-job:0` with its original OpenShorts job
**deleted**, run end-to-end: launch-token exchange → `/discord/editor/bootstrap`
(durable) → `/api/status` rehydration ("Job recovered from disk") →
`/api/clip/rerender` (trim 1.0→5.0) → `/api/hook` → `/api/subtitle` (karaoke) →
`/discord/editor/complete` (one `render-1.1` + one card) → replay idempotent.
Card message id `1549614172055281724`. Script
`infra/supabase/prove_e2e_rehydrate.py`.

## ♻️ Temporary workspace lifecycle (unchanged)

`rehydrate_edit_job()` creates `output/loom-<edit_session_id[:12]>/` per session.
Retain while active/retryable; delete after completion + grace; clean abandoned
workspaces with bounded retention; never delete another tenant/session workspace.
Re-hydration is idempotent.

## 1. Repository state

- **LOOM-Factory** `master` · HEAD `d10f002` · clean · pushed to origin.
- **OpenShorts (nested)** `loom/factory-mvp-baseline-fixes` · HEAD `7f47de5` ·
  pre-existing dirty/untracked (do NOT touch): `dashboard/index.html`,
  `docker-compose.yml`, `security_utils.py` (modified); `aborted-runs/`,
  `security_utils.py.bak-20260908212436` (untracked).
- **Recovery artifacts** (`openshorts-recovery/`) preserved:
  `0002-discord-editor.patch` md5 `b0867c9ec0e20d6550929ee8bce7a4b7`;
  `0001-fix-caption-contract.patch` md5 `c631f000b6b5e904dbffab942d9fba9e`.

## 2. Runtime + infrastructure (unchanged)

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → Caddy `:8081`

| Component | Process/container | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host, supervised) | `127.0.0.1:8090` | `/health` 200 |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | Up |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback) | Up (root 404 normal) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | Up |
| OpenShorts frontend (Vite) | `openshorts-frontend` | `0.0.0.0:5175→5173` | Up |

Adapter supervision: `infra/discord-review/supervise.sh` → `supervisor.log`;
launcher `run.sh` (sources `~/.openclaw/.env`); stdout/stderr → `adapter.log`.

Caddy `:8081` routes (unchanged): `/edit /edit/*` (CSP frame-ancestors Discord) ·
`/discord/interactions*` → 8090 (Ed25519) · `/discord/editor/*` → 8090 ·
`/discord/card*` → 8090 (Bearer tunnel token) · `/api/*` → 8090 · default → Vite 5175.
`:8082` → `/relay*` n8n callback relay.

## 3. Discord IDs (unchanged)

- **App:** "LOOM Clip Review" · App ID `1548188833386995742`
- **Guild:** "Clippy Factory" · Guild ID `1546266463663423518`
- **Review channel:** resolved at runtime by name `clip-review`.
- **Reviewer (Bryan):** `688976899094872148`

## 4. Migrations — 010–021 (all applied + live-verified)

| # | Change | Status |
|---|--------|--------|
| 010 | `tenant_distribution_profiles.publisher_user_id` | Applied |
| 011 | `loom_media.content_type` | Applied |
| 012 | `loom_edit_sessions` + `loom_edit_corrections` + lineage | Applied |
| 013 | `register_edited_asset` RPC (service_role only) | Applied |
| 014 | `loom_edit_sessions.session_token_hash` | Applied |
| 015 | editor launch context | Applied |
| 016 | `loom_edit_sessions.draft` (jsonb) | Applied |
| 017 | `loom_media.clean_source_url/hash/size/filename` | Applied |
| 018 | `loom_source_briefs` | Applied (Bryan, 2026-09-16) |
| 019 | `loom_discovered_sources` | Applied (Bryan, 2026-09-16) |
| 020 | `loom_discovered_sources.profile_id` (+ index) | Applied (Bryan, 2026-09-16) |
| 021 | `loom_discovered_sources.body_excerpt` | Applied (Bryan, 2026-09-16) |

## 5. Credential inventory (names + locations only — never values)

**`~/.openclaw/.env`:** `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
`LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
`LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`, `LOOM_N8N_API_KEY`,
`LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY`, `LOOM_GHL_TOKEN`,
`LOOM_GHL_LOCATION_ID`.

**`infra/tunnel-proxy/.env`:** `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
`LOOM_GEMINI_KEY`.

> ⚠️ Never record or print secret values. Reference the files only.
> `YOUTUBE_DATA_API_V3_KEY` is **absent** (needed only for keyword search).

## 6. Deferred / NOT in scope

- **Discord Activity (iframe) embedding** — deferred.
- **Temporary workspace sweep** — policy documented; enforcement deferred.
- **Missing preview font** `fonts/NotoSerif-Bold.ttf` — cosmetic, deferred.
- **Topic Intelligence scoring** — reserved fields only in discovery; no scoring
  implemented (correctly out of scope for this segment).
- **Reddit connector** — RUNTIME-PROVEN (Supabase 12/12).
- **TikTok connector** — HELD until explicitly authorized.
- **GHL subaccounts / social accounts / Discord tenant spaces for the three
  profiles** — deliberately NOT created (profiles are fixtures only).

## 7. Next segment

**Acquisition Boundary** — the next segment. Source Review Inbox + Manual Source
Intake are complete: `/source-review` surfaces BOTH halves (market + content →
Combined Selection) and records approve-for-acquisition / hold / reject;
`/submit-source` accepts manual URLs (YouTube/Reddit → `accepted`; TikTok/
Instagram/Twitch/Kick → `accepted_pending_platform_support`). Acquisition would
ingest only approved-for-acquisition candidates (and optionally wire accepted
YouTube/Reddit submissions into qualification) — still requires explicit
authorization before any download/publish. **Pass 2 (bounded media)** deferred
(separate approval). No automatic download/processing/publication at any point.
