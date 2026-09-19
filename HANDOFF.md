# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-18 21:15 EDT (America/Detroit) — TWITCH RUNTIME-PROVEN (clip + VOD)

## HANDOFF — Twitch RUNTIME-PROVEN (2026-09-18)

Both signed Twitch approvals completed without another human click:

- clip `AltruisticTallBorkDancingBaby-gb-T9vlskqh7FCfl` — whole-item, 34.333 s, 25,546,606 B
  → `twitch__AltruisticTallBorkDancingBaby-gb-T9vlskqh7FCfl__b19cec0615e7.mp4`
- VOD `2852456270` — bounded 00:30–01:00, 30.000 s, 12,230,775 B
  → `twitch__2852456270__30-60__7c09e34a74da.mp4`

Both H.264/AAC/yuv420p/fast-start, hashes matching their library rows, no full VOD retained, temp clean,
$0 local, Apify ceiling unchanged at 7,200/2. Both cards show ACQUIRED with real facts, a library link,
no Retry, and a truthful poster fallback (both files exceed Discord's 10 MiB native limit).

**`TwitchAdapter.runtime_proven = True`** on genuine evidence. Fixed on the way: LOCAL_PLATFORMS
platform-name routing (Twitch took the Apify path), the Instagram-only durable URL normaliser (which sent
a Twitch click to `NO_ADAPTER`), all implicit `instagram` defaults (now fail closed as PLATFORM_MISSING),
platform threading through the media identity check, the `youtube__` filename default, a `live_origin`
scope error, and split spend policy from readiness. See CURRENT_STATE.md for the full list.

**Next:** Kick follows this same lane (register an adapter; no shared-code branches). Then finish the
remaining queued work — docs/UI polish for the automated suggested-window lane.

## HANDOFF — Twitch adapter implemented, awaiting one proof URL (2026-09-18)

Twitch is registered behind the shared long-form interface: `twitch_url.py` (strict VOD/clip identity with
stable rejection codes, tracking parameters excluded from identity) plus a real `TwitchAdapter`
(`local:yt-dlp`, `long_form`, $0 local cost model, `runtime_proven: False`). The arbitrary four-hour source
ceiling was removed for ranged mode, so multi-hour VODs are acquirable through an approved ≤300 s window
while clips remain whole-item when ≤300 s. Installed `yt-dlp 2026.08.19` already supports `twitch:vod` and
`twitch:clips`, so no new dependency is needed. 16 deterministic tests pin the contract; the shared lane is
asserted platform-neutral.

**Next:** one exact public Twitch **clip** URL, or a **VOD** URL plus a preferred window. Then: probe
metadata only, post one premium REVIEW card (a deterministic 30 s window labelled an acceptance-proof
suggestion, never an AI virality claim), stop once for the genuine Approve & Acquire, acquire one bounded
asset at $0, verify codecs/duration/size/hash/filename/fast-start and that no VOD is retained, check the
Discord/Library/OpenShorts behaviour, flip `runtime_proven` only on genuine success, then update the docs
and commit. Kick follows the same lane afterwards.

## HANDOFF — Ranged window 01:05–01:35 acquired (2026-09-18)

The second window of `ClhBXVygWbc` was completed **programmatically** by resuming the already-granted
authorization (no new click, no new authorization): job `ca0de4d6-b97b-4fa4-a342-14f33e1c2f36` is
`acquired`, `bounded_segment`, provider `local`, **$0**.

**Asset:** `youtube__ClhBXVygWbc__65-95__ab7603b460c3.mp4` · 2,614,107 B · 30.000 s · 640×480 ·
H.264/AAC/yuv420p/fast-start · sha256 `ab7603b460c3…`. One source now holds **two** library assets
(`…__30-60__3a891cb35f99.mp4` fp `929e4e72…` and `…__65-95__ab7603b460c3.mp4` fp `fad6787e…`).
No full-source download, tmp clean, ceiling untouched at **7,200 micro / 2**.

**Card** (same message, in place): `🎬 ACQUISITION · ACQUIRED`, `range 01:05–01:35 · clip 30.0 s ·
operator_adjusted`, media `0:30 · 640×480 · landscape · 2.5 MB · Audio present (AAC)`, `Local
acquisition · $0 provider cost`, video **attached natively**, `Open in Reaction Library` link, no Retry.

**Fixed this round:**
1. `context_for` resolved state from the first `acquire_for_reaction` row regardless of range — the stale
   30–60 row made a 65–95 card resolve FAILED while displaying REVIEW, so the guard refused the very
   action shown (“not available in this state”). State is now range-scoped (`store.jobs_for()` +
   `lane.row_matches_range()`).
2. A terminal Gate 1 no longer reads as APPROVED (only live `queued`/`running` does).
3. `live_runner`'s already-acquired check was **content-id-only** → false `ALREADY_ACQUIRED` for a new
   window; it now delegates to the range-aware `lane.already_acquired()`.
4. Duplicate `ACQUIRE` is an idempotent **no-op** (one provider call per authorization); a failed
   attempt is re-run through the explicit Retry action.
5. The one-click re-authorises the active range before the guarded acquire (other ranges untouched).
6. Still in force: `DetachedTransport` (no handler/socket after the ACK — the Errno 9 fix), range-bound
   `custom_id` (`AQ:aa:<ref>#<fp16>`) with `RANGE_STALE`, durable-submission window precedence.

**Suites:** acquisition **414** · discord-review **168** · local-helper **76**. New coverage:
`test_range_identity_regression.py` (exact false-refusal case, same-window idempotency, a further window
after another exists), plus the production-shaped range-state and wire-level ACK/one-click proofs.

## HANDOFF — Ranged acquisition milestone (2026-09-18)

**Bounded ranged acquisition is runtime-proven on a long-form source.** `ClhBXVygWbc` (05:02) produced the
approved **00:30–01:00** window: job `157184c9-7113-46c0-b7b0-10006a3e8d55` = `acquired`, `bounded_segment`,
provider `local`, **$0**, range fingerprint `929e4e72…`.

**Asset:** `youtube__ClhBXVygWbc__30-60__3a891cb35f99.mp4` · 2,489,452 B · 30.000 s · 640×480 ·
H.264/AAC/yuv420p/**fast-start** · sha256 `3a891cb35f99…`. One library row, job provenance and card all agree;
no 302 s source retained; tmp empty; **Apify ceiling untouched at 7,200 micro / 2**.

**Card** (same message, in place): `🎬 ACQUISITION · ACQUIRED`, `Window source 05:02 · range 00:30–01:00 ·
clip 30.0 s · origin manual_submit_source`, real media facts, `Local acquisition · $0 provider cost`,
`Open in Reaction Library` as a **link button with no custom_id**, and the **video attached natively**
(2.4 MiB fits tier-0 10 MiB) with honest "Playable in this message".

**Long-form lane:** `source_window.py` is the single contract (ms). The 300 s limit applies to the CLIP, not the
source; a whole source is acquirable only when ≤300 s; range-aware identity makes source+range idempotent and a
different range a distinct asset. Download is `--download-sections *start-end` + `--force-keyframes-at-cuts`;
the delivered clip is validated against the approved window (±2 s), then normalized per the delivery contract.

**Defects fixed:** (1) a **stale adapter process** had planned Gate 2 as `full_item` with no window and its old
`safe_record` stripped the `?v=` identity → row repaired + adapter restarted, identity rule keeps `?v=`;
(2) an **empty probe result** was misclassified as a non-retryable refusal → now `NO_RESULTS` retryable with the
metadata probe bounded to 3 attempts; (3) **fast-start was never applied to pass-through media** → the contract
now remuxes (`-c copy -movflags +faststart`), which is why the clip is `…3a891cb35f99.mp4` rather than the
pre-written `…5d2df2a03c8e.mp4`; (4) **ACQUIRING was not shown until the work finished** → `_do_acquire` now
patches `⏳ ACQUISITION · IN PROGRESS` before yt-dlp/ffmpeg starts; (5) a **poster fallback swallowed the size
reason** → it now states the exact reason and claims no playback.

**OpenShorts ingest compatibility: 9/9 PASS.** Suites: acquisition **408** · discord-review **143** ·
local-helper **76**.

**Still open:** no UI for an operator-editable suggested window on an automatically discovered candidate (data
layer is range-aware, surface is not built); the ranged lane's automated intake path is wired at the data layer
only.

## HANDOFF — Reaction Library delivery format (2026-09-18)

Acquired YouTube media had landed as **VP9 + Opus in MP4**: fine in the browser player, but **QuickTime refused it**
with a compatibility warning (Instagram assets were unaffected — they are H.264 + AAC). Fixed as **shared library
finalization behavior**, not a YouTube workaround.

**Contract** (`infra/acquisition/media_normalize.py`): MP4 · H.264 · AAC · yuv420p · fast-start, with dimensions,
aspect ratio, orientation and frame rate preserved, single-pass encode for sync, and the ≤100 MiB / ≤300 s limits
re-checked on the **finalized** asset. Compliant media is moved untouched; anything else is normalized to a temp file,
validated, then swapped in atomically. A failed conversion never touches the existing asset and cleans up its temp file.
`yt-dlp` now prefers `avc1` + `mp4a`, with normalization as the fallback; the delivered file's own SHA-256 names it.

**Repaired asset (no re-download):**
- old `youtube__h3Q_tAFWY5g__d8990649b8e3.mp4` — VP9 + Opus, 16,407,149 B, sha256 `d8990649b8e3…`
- new `youtube__h3Q_tAFWY5g__06ab279dcb13.mp4` — H.264 + AAC + yuv420p + faststart, 19,977,430 B, sha256 `06ab279dcb13…`
- geometry unchanged: 1080×1920, 30/1 fps, 40.641 s

DB row (same id, no duplicate), `storage_url`, job provenance (`delivery_format`, `normalized_from`) and the Discord
card were updated in place; a poster frame was captured as reference data; the retired hash now 404s. Verified with
ffprobe and macOS metadata (`kMDItemCodecs = H.264, MPEG-4 AAC`). Ceiling untouched at **7,200 micro / 2**.
Suites: acquisition **392** · discord-review **134** · local-helper **76**.

**Note for future paths:** any new acquisition route must finalize through `media_normalize` so no lane can ship an
incompatible asset again.

## HANDOFF — YouTube adapter runtime-proven (2026-09-18)

**YouTube exact-URL acquisition is runtime-proven.** `https://www.youtube.com/watch?v=h3Q_tAFWY5g`
was acquired through the real lane (signed Discord click → Gate 1 → Gate 2 → one bounded local yt-dlp
attempt → validated media → atomic finalize → library row → card updated in place):
`youtube__h3Q_tAFWY5g__d8990649b8e3.mp4`, 16,407,149 B, 40.641 s, sha256 `d8990649b8e3…`, vp9/opus.

**Cost $0.** Adapter `local:yt-dlp`, provider `local`. The Apify ceiling is untouched at **7,200 micro /
2 successes** (both Instagram); no YouTube row ever touched Apify; three `local:yt-dlp` audit rows
exist for the attempts, all $0 and all classified.

**Real-metadata refusal proven:** `ClhBXVygWbc` probes at **302 s** → terminal `DURATION_LIMIT` at the
metadata stage with **no download**; the card shows the exact duration and limit, `Acquire` removed and
no `Retry`.

**Capability:** `platform_capabilities.youtube` = `runtime_proven` (flipped only because this proof
succeeded); `can_acquire("youtube") == True`.

**Cards (both updated in place, no duplicates):** Short = `🎬 ACQUISITION · ACQUIRED` with
`Open in Reaction Library` + `Hold`, `Local acquisition · $0 provider cost`, provenance
`` `local:yt-dlp` · Authorized by Bryan ``; 5:01 = terminal `DURATION_LIMIT` with
`Hold | View Error | View Source`.

**Seven defects were found by running it live and are fixed** (proof-mode env never plumbed; preflight
`_strip` dropping the `?v=` identity; `safe_record` stripping identity queries; the local path
hand-rolling its library row and sending `""` into a uuid column; `context_for` state vs a crashed
retry leaving the job `queued`; yt-dlp single-entry playlist envelopes / empty `entries`; the card's
`sanitize_display_url` stripping `?v=`). Full detail in the `CURRENT_STATE.md` stop-state entry.

**Suites:** acquisition 374 · discord-review 134 · local-helper 76 — all passing.

**Human clicks used:** two genuine operator clicks (Approve, then Retry Manually).

**Still open:** the exact-URL lane currently requires the proof-mode switch (`LOOM_ADAPTER_PROOF_PLATFORMS`)
to be unnecessary now that the platform is proven — worth removing from the dispatch critic and from
`can_acquire`'s env; the Short's card shows "No preview yet" (no poster/native attachment for local
assets yet); and a second real proof for a different host (reddit/tiktok/twitch/kick) is still
`implemented_not_proven`.

## HANDOFF — YouTube adapter increment 1 (2026-09-18)

**Built + tested (39 new tests; acquisition 363 · discord-review 134 · local-helper 76 all green):**
`youtube_url.py` (strict exact-URL contract — every supported form collapses to
`www.youtube.com/watch?v=<id>`; playlists/mixes/channels/searches/feeds/live/embed, selection params on
watch URLs, id drift, multi-video, foreign hosts and credentials all rejected; fingerprint identical
to the manual-intake construction so both lanes share one identity per video); `ytdlp_adapter.py`
(local adapter: `--no-playlist --playlist-items 1`, `bv*+ba/b`, `--merge-output-format mp4`,
`--max-filesize`, `--retries 0`, a hygiene guard that forbids cookies/browser-profile/auth flags,
bounds 300 s / 100 MiB / 1 result / 1 attempt enforced before, during and after, atomic finalize,
partial cleanup); `local_acquisition.py` + `ledger.reconcile_local()` ($0 provider cost, truthful
034 audit row as `local:yt-dlp`, ceiling never touched).

**Not wired yet (gaps):** runner platform dispatch for `youtube`, the card's
`Local acquisition · $0 provider cost` line, and the intake bridge (manual `/submit_source` +
automated Source Engine candidate → shared premium REVIEW card with `manual_submit_source` /
`automated_source_engine` provenance).

**Honesty:** `platform_capabilities` still says `youtube = implemented_not_proven`;
`can_acquire("youtube") == False`. Not flipped — no bounded media proof has run.

**Untouched:** 0 Apify calls, 0 ceiling mutations, 0 real YouTube downloads, 0 cookies/account auth,
0 migrations, 0 production YouTube cards.

**Next input:** one exact public YouTube URL under five minutes from Bryan; then finish the wiring and
run the bounded local proof before claiming runtime-proven.

## HANDOFF — Instagram acquisition milestone closed (2026-09-18)

Instagram acquisition is **runtime-proven** end to end from a real Discord click (Gate 2 → one bounded
Apify run → bounded download → ffprobe → SHA-256 → atomic finalize → library row → card updated in
place). The cost baseline is now the provider's own **$0.0036** per single-result run (re-baselined
from the old pinned $0.0026; `cost.REEL_SCRAPER_RESULT_MICRO = 3_600`, earlier ledger row corrected
idempotently, ceiling 7,200 micro / 2 successes).

**Two assets** are in the Reaction Library: `instagram__DdZS1QRhrXE__9b22f887b2ff.mp4` (23.7 MB,
55.26 s) and `instagram__DdQE_RUS0GJ__06e1da6120df.mp4` (16.7 MB, 77.65 s). The library moved to the
visible **`~/Movies/LOOM-Reaction-Library`** with a browsable gallery at
`http://127.0.0.1:8787/library` (poster, platform, duration, size, status, acquired date; detail +
Range-capable media + poster routes). The root resolves through `infra/acquisition/library_root.py`
(`LOOM_REACTION_LIBRARY_ROOT` override); the old hidden directory is preserved as
`~/.openclaw/loom-reaction-library.backup-2026-09-18` — nothing was deleted.

Discord interactions are **ACK-first** (acknowledged before any store read; private notes via webhook
follow-up; context failures fail closed with no writes). Assets above the server's real upload limit
attach a **poster frame and the exact reason** rather than claiming playback — this guild is premium
tier 0, so 10 MiB is the cap and the 15.9 MiB reel plays through the library page instead.

Services healthy: adapter `route AQ · durable store · live_runner enabled · token present`; helper
`/health` ok. Tests: acquisition 324 · discord-review 134 · local-helper 76.

Limitations: no inline playback for the oversized reel; the ≤10 MiB native-attach path is implemented
and unit-tested but untested live; Hold has not been clicked live; the library link is loopback-only.
**Next adapter: YouTube via local `yt-dlp`** (no paid provider, deterministic).

## HANDOFF — end-of-day closeout

Universal (corrected): `agentx/all-video-scraper` = $0.38/file (~146× IG, deterministic,
fallback-only); `easyapi/all-in-one` = headline ~$0.003 but REJECTED for trust/determinism/
contract (usage charge, rating 2.46 API vs 4.95 README, medias[], signed headers) — NOT price.
Snapchat (resolved): `agentx/snapchat-video-downloader` (`q5qkPabhaMeWxWVGa`, $0.30/file,
deterministic, audio included, 100% success) = recommended bounded-proof candidate;
`automation-lab/snapchat-public-video-downloader` (`LIdfZyy13VzOzEUNx`, usage, 75.7%) = conditional.
Architecture: HYBRID (platform-specific primary + universal inactive fallback). Keep proven Instagram.
$0 spent, nothing built, no capability changes.

---

## HANDOFF — universal + Snapchat; architecture = hybrid

Universal: agentx/all-video-scraper (etzrjsBIFSOb19k5E) most reliable; easyapi/all-in-one
(iZbsVYT4VfdMxoIPL) cheapest but flagged (rating discrepancy, usage charge, medias[]).
Snapchat: dedicated low-adoption actors, public Spotlight only, last priority.
Decision: HYBRID — platform-specific primary + universal INACTIVE fallback (X/Snapchat).
Keep proven Instagram adapter. $0 spent, nothing built.

---

## HANDOFF — TikTok + X provider deep-dive; X deferred

TikTok: recommended `memo23/tiktok-video-downloader` (id pQGdDfhFC35QToTxZ), fallback
`clockworks/tiktok-scraper` (id GdWCkxBtKWOsKjdch, 286K users), emergency yt-dlp.
X/Twitter: NO compliant candidate (2-user actors, usage pricing, auth walls) — DEFER; not in allowlist.
Capability: tiktok → evaluated_not_proven. Only Instagram runtime-proven. $0 spent.

---

## HANDOFF — cross-platform evaluation complete; nothing built

Read-only cross-platform acquisition evaluation (TikTok/Reddit/X/YouTube/Twitch/Kick):
- `infra/acquisition/CROSS_PLATFORM_EVALUATION.md` (comparison table + verdicts).
- Local (yt-dlp, free) primary: YouTube, Reddit v.redd.it, Twitch clips+VOD, Kick clips+VOD.
- Apify primary: TikTok + X/Twitter. Instagram already provider-proven ($0.0026/Reel).
- Capability: instagram → runtime_proven_provider; can_acquire() updated.
- Bounded VOD via yt-dlp --download-sections.
- Next order documented (NOT built): Instagram UI → YouTube → Twitch/Kick → TikTok → Reddit → X → VOD.

$0 spent, no Actor calls, no downloads, no UI wiring, no ceiling change.
Instagram UI NOT wired to provider (do not claim production-ready).

---

## HANDOFF — Phase 3 proof succeeded; Instagram acquisition runtime-proven

First paid acquisition succeeded end-to-end:
- `apify/instagram-reel-scraper` (id `xMc5Ga1oCONPmWJIa`), run `0DPOKk2bEwxqVTS0i`.
- 1 result, content id DdZS1QRhrXE, field `videoUrl`.
- Media: h264+aac (muxed with audio), 720x1280, 30fps, 55.26s, 23.7 MB.
- Actual cost $0.0026 (2600 micro). Circuit breaker: spent 2600 / succeeded 1.
- Local mp4 + sha256 + job/library/provider-run records all persisted + verified.
- Signed URL volatile; temp files removed.

Instagram acquisition now runtime-proven at $0.0026/Reel.

---

## HANDOFF — Phase 2 complete; awaiting Phase 3 authorization

Paid-provider boundary implemented (deterministic, $0):
- `infra/acquisition/provider.py` — actor allowlist (reel-scraper default,
  instagram-scraper INACTIVE fallback), single-URL/maxResults=1 input, output
  validation, authorization, provenance (no signed URL).
- `infra/acquisition/cost.py` — circuit breaker ($1 ceiling + max 10).
- `infra/acquisition/apify_client.py` — token load (presence-only) + transport.
- `infra/acquisition/orchestrate.py` — mock-testable end-to-end dispatch.
- Migrations 034/035 **APPLIED + verified** (both tables HTTP 200; ceiling seed
  row = $1.00 / max 10 / disabled=false).
- 380 tests pass (acquisition 109 = +33 provider tests).

No Actor call, $0 spent, no token use, no download, no migration applied.

Next: Phase 3 (UNAUTHORIZED) — one real Reel proof on DdZS1QRhrXE, max $0.0026.
Migrations applied + verified; requires explicit Phase 3 go-ahead.

---

## HANDOFF — local Instagram closed out; Apify Phase 1 done (awaiting approval)

Local Instagram acquisition crossed the agreed proof threshold: the final proof
failed locally with `INSUFFICIENT_FMP4_SET` (no assemblable manifest/init+segments
set). Verified close-out: helper.log shows no post-restart `/acquire` request; no
new job row; library + tmp dirs empty; no signed URL leaked. Instagram capability
→ `blocked_local_fmp4_incomplete` (candidates `["chrome-extension", "apify"]`);
local code retained, not deleted, not further repaired.

Apify paid-provider fallback — Phase 1 broadened search ($0, no call, token unused):
- DECISION: **HOLD** `agentx` / `thenetaji` / `crawlerbros`.
- Scalable path: **official Apify Actors** return a direct video CDN URL in the
  base metadata result at **$0.0026–0.0027/Reel** (no stored-file premium, no
  usage charge). Top 3: (1) `apify/instagram-reel-scraper` (recommended),
  (2) `apify/instagram-scraper` (fallback), (3) browser-extension manual ($0).
- `infra/acquisition/APIFY_INSTAGRAM_ACTOR_EVALUATION.md` (v2, full ranking +
  economics) + `APIFY_PROVIDER_BOUNDARY.md` (contracts, migrations 034/035).
- Budget: keep $1 ceiling (~380 Reels at $0.0026); "max 3" obsolete.

Next: STOP for Bryan's approval before Phase 2 (token + adapter + mocks + tests).

---

## HANDOFF — fMP4 assembly built; authorized proof NOT yet run

`master` @ `1d6a53b`, pushed, clean. Migrations 018–033 applied + verified (033
adapter + is_non_production confirmed HTTP 200). fMP4 assembly path built
(manifest.py MPD/M3U8 + assemble.py + fmp4.py + helper dispatch/retry + extension
manifest-preferring capture). 325 tests pass. Helper running on 127.0.0.1:8787.
Authorized proof = `https://www.instagram.com/reel/DdZS1QRhrXE/` (one item, ≤100 MiB,
≤5 min, Personal Reaction Library, non-production) — NOT yet run after fMP4 build.
Pivot: this + at most one more Reel proof; else Apify (Instagram-only) under $1
ceiling. No valid Reaction Library asset produced yet. Latency:
12s is a fail-closed max; Acquire stops+detaches on the first direct MP4.

Acceptance URL: `https://www.instagram.com/reel/DdQE_RUS0GJ/?utm_source=ig_web_copy_link&stkn=MzRlODBiNWFlZA==`
Intended result: Routing "Awaiting tenant assignment" + Platform "Pending Instagram support" + canonical URL (tracking removed) + submission ID + candidate-only (no analysis/score/acquisition/download).

Next: Bryan re-runs the Discord acceptance test and confirms. Do NOT begin Chrome
extension or Acquisition Boundary until acceptance passes.

## Segment — Manual Source Intake V1.1 (unassigned routing + tenant assignment)

- **Migrations 028/029/030 APPLIED**: `loom_tenants` (seeded `dreemzy`), `loom_routing_events` (immutable audit), per-tenant dedup + `qualification_required`.
- **tenant vs profile**: `tenant_id` = owning business; `profile_id` = content niche (derived server-side). `/submit-source` takes `destination`; `/assign-source` (id `1550241802446180463`) does assign/reassign/copy.
- `tenant_routing.py` — pure `plan_routing` + `route_submission` (downstream lock, collision, requalification).
- **175 deterministic tests** (62 + 55 + 58). **Routing runtime proof 15/15**.

## Segment — Manual Source Intake V1

- **Migration 027 `loom_manual_submissions` APPLIED** (first-wins dedup).
- **`/submit-source`** guild command registered (id `1550237873637425192`).
- `manual_intake.py` — strict platform allowlist (YouTube/Reddit/TikTok/Instagram/Twitch/Kick); SSRF/credential/private-host/port/scheme rejection; deterministic canonicalization (no fetch/redirect); dedup; status mapping (youtube/reddit=accepted, others=accepted_pending_platform_support).
- **160 deterministic tests** (62 + 55 + 43). **Runtime proof 12/12**.
- **A manual submission is a CANDIDATE only — never approval for acquisition, never analyzed, never a fabricated score.**

## Segment — Source Review Inbox (Discord)

- **Migration 024 `loom_source_reviews` APPLIED** (first-decision-wins).
- **`/source-review`** guild command registered (id `1550229456462028881`).
- `source_review.py` (combined cards: market + content + selection; Approve-for-Acquisition / Hold / Reject) + `register_source_review_command.py` + adapter type-2 slash command.
- **128 deterministic tests** (62 + 55 + 11). **Safe runtime proof 8/8** (card post+delete, idempotent decision, production exclusion).
- **APPROVE FOR ACQUISITION records authorization only — triggers nothing.**

## Segment — Content Intelligence Operationalization V1.1 (recurring pipeline)

- **A/B:** Gemini Flash-Lite vs Qwen — Gemini is default transcript-triage provider (2.6s vs 146s; 560 vs 5798 output tokens; both schema-valid + evidence-grounded).
- **Pinned model:** `gemini-3.5-flash-lite`; Qwen `qwen/qwen3.5-9b` (secondary, no silent fallback).
- **`providers.py`** — real Gemini + Qwen providers (classified failures, cost/token accounting, model identity, no credential logging).
- **`pipeline.py`** — Pass1Pipeline (discovery → topic → shortlist → transcript → content → selection → idempotent Supabase; hard limits + cost ceiling) + `selection_queue()`.
- **`store_supabase.py`** — Supabase persistence for assessments + selections.
- **117 deterministic tests** (62 + 55). **Recurring-pipeline proof 9/9** ($0.00071).

## Segment — Content Intelligence Preflight V1 + Combined Source Selection V1

- **`evidence.py`** — 5 evidence levels + per-dimension gating + confidence ceilings.
- **`contract.py`** — strict Content Assessment schema + validation (rejects malformed / out-of-bounds / evidence-exceeding claims).
- **`selection.py`** — combined 35/45/15/5 score + separate confidence + decision rules.
- **`provider.py`** — evidence bundling, model-provider interface, deterministic MockProvider, orchestration.
- **`probe.py`** — bounded media probe contract (temp lifecycle, hard limits, no full download).
- **`store.py`** — SQLite persistence (idempotent; new evidence version → new row).
- **Migrations 025/026 APPLIED** (Bryan, 2026-09-17).
- **104 deterministic tests** (62 discovery + 42 content-intelligence).
- **Pass-1 real proof 13/13** (Gemini Flash-Lite `gemini-flash-lite-latest`, transcript-only, ≈ $0.002 < $0.05): real YouTube candidate `qgLaCZyKv_8`, English auto-captions via yt-dlp (0 video/audio bytes), market=59 + content=68 → selection=60 / standard_review, evidence-gated, idempotent Supabase rows.

## Segment — Topic Intelligence V1: RUNTIME-PROVEN (local + Supabase)

- **`normalization.py`** — platform-aware signal normalization (YouTube `views`; Reddit engagement honestly absent over RSS). Missing metrics recorded, never fabricated.
- **`velocity.py`** — velocity ONLY from ≥2 valid time-separated observations; unavailable otherwise (honest reason).
- **`topic_intelligence.py`** — 7-signal weighted score → opportunity class (strong/moderate/weak/stale/duplicate/incomplete) + confidence + data-completeness + reason codes + missing-data/low-confidence flags. No LLM, no paid service, deterministic.
- **`content_format`** routing field added (default `hold_unassigned`).
- **Assessment store:** SQLite built+tested; Supabase built + runtime-proven.
- **Migrations 022/023 APPLIED** (Bryan, 2026-09-17).
- **62 deterministic tests pass** (was 38).
- **Real-data proofs: local SQLite 28/28 PASS; Supabase 13/13 PASS** (15 real YouTube candidates, $0).
- Fixed Supabase `upsert_source` UPDATE-path parity (profile_id/body_excerpt/content_format refreshed on re-observation).
- Proof profiles auto-excluded from `production_assessments()`. No candidate advanced beyond `manual_ingest_only`.

Zero cost. Zero downstream side effects. No media download, no OpenShorts job,
no publication, no distribution.

## Segment 3 — Reddit connector milestone: RUNTIME-PROVEN (Supabase 12/12)

- **RedditConnector** (`connectors/reddit.py`) — subreddit + bounded keyword
  discovery via first-party Reddit RSS (no credential, no scraping, no auth
  evasion). Public JSON API is 403 unauthenticated; OAuth API requires app
  registration (neither used).
- Captures: post id (`t3_` stripped), canonical URL, subreddit, author, title,
  body excerpt, publication time, observed metrics (content_type + source URL
  refs), provenance (honest note: RSS omits score/ups/comments).
- Cross-post dedup: author-agnostic `crosspost_key` added to identity + gate.
- **38 deterministic tests pass** (was 34).
- **Supabase proof 12/12 PASS**: 25 real r/technology posts discovered → stored
  (25/25) → replay idempotent → explicit exact-duplicate + cross-post rejection
  → policy rejection (missing provenance) → manual-ingest boundary only → no
  forbidden modules / RSS-GET-only → proof profile excluded from production queue.

## Segment 2 — Automated Source Discovery V1: CLOSED

- **Segment 2 — Foundation: COMPLETE.**
- **Segment 3 — first YouTube connector milestone: RUNTIME-PROVEN
  (Supabase-backed).**

Zero cost. Zero downstream side effects. No source was downloaded, processed,
clipped, published, or distributed.

## Segment boundary (resolved)

- **Segment 2 — Foundation (COMPLETE):** Source Brief contract; normalized
  discovery schema; identity + duplicate handling (deterministic tests);
  shared connector interface; qualification-before-acquisition gate; three
  profile fixtures validate.
- **Segment 3 — Initial Connector Proofs (first milestone RUNTIME-PROVEN):**
  real YouTube source discovered → normalized → deduplicated → evidence stored
  → qualification applied → one duplicate rejected → one eligible source passed
  to manual-ingest boundary → no automatic publication.

Reddit is runtime-proven. TikTok is HELD. X deferred.

## What was built

```
infra/discovery/
  source_brief.py           # Source Brief contract + is_non_production() marker convention
  discovery_record.py       # normalized discovery record + validation (carries profile_id + body_excerpt)
  identity.py               # canonical URL norm + fingerprints + dedup (no paid model) + crosspost_key
  connector.py              # shared connector interface
  qualification.py          # qualification-before-acquisition gate
  store.py                  # SQLite durable store (idempotent upsert, append-only metric history)
  store_supabase.py         # production Supabase store (service-role key by NAME only)
  connectors/youtube.py     # YouTube/search + channel discovery (first-party RSS)
  connectors/reddit.py      # Reddit subreddit + keyword discovery (first-party RSS)
  prove_youtube_connector.py
  prove_supabase_acceptance.py
  prove_supabase_reddit.py
  fixtures/                 # 3 non-provisioned profile briefs
  tests/test_discovery.py   # 38 deterministic tests
n8n-workflows/LOOM-06-Source-Discovery.json   # shared, inactive
infra/supabase/018_loom_source_briefs.sql      # APPLIED
infra/supabase/019_loom_discovered_sources.sql # APPLIED
infra/supabase/020_loom_discovered_sources_profile.sql # APPLIED (15 rows backfilled)
infra/supabase/021_loom_discovered_sources_body_excerpt.sql # APPLIED
infra/supabase/022_loom_topic_assessments.sql            # APPLIED
infra/supabase/023_loom_discovered_sources_content_format.sql # APPLIED
infra/supabase/024_loom_source_reviews.sql                  # APPLIED
infra/supabase/025_loom_content_assessments.sql            # APPLIED
infra/supabase/026_loom_source_selections.sql              # APPLIED
```

## Supabase acceptance proof (12/12 PASS, production Supabase)

Real YouTube data (Linus Tech Tips): 15 candidates discovered via first-party
RSS → normalized + stored (15/15) → replay idempotent (15, not 30) →
metric-history append (1→2, prior preserved) → explicit repost rejection →
manual-ingest boundary only → no forbidden modules imported / RSS-GET-only.
Script: `infra/discovery/prove_supabase_acceptance.py`.

## Production-queue exclusion (acceptance/test profiles)

`profile_id` travels on every discovered record (from its Source Brief) and is
persisted. `is_non_production()` marks acceptance/test/example profiles. Normal
production queue queries use `production_sources()` / `production_fingerprints()`
/ `production_global_fingerprints()`, which exclude non-production profiles by
default. Profile `supabase-acceptance-proof` is non-production acceptance
evidence and is KEPT (not deleted).

## Tests

`python3 -m unittest discover -s infra/discovery/tests` → **62 passed**.

## Bounded real-data proof (Topic Intelligence)

`python3 infra/discovery/prove_topic_intelligence.py` → **28/28 PASS** (local
SQLite, 15 real YouTube candidates via first-party RSS, $0).

`python3 infra/discovery/prove_topic_intelligence_supabase.py` → **13/13 PASS**
(production Supabase, migrations 022/023 applied, $0).

## Git

- LOOM-Factory `master` HEAD `94607d7`, clean, **pushed** to origin.
- Chain: `13a8dc6` → `0e23c54` → `4b1da3e` → `8f03e34` → `5ab5283` →
  `d10f002` → `205f1bf` → `708d472` → `4cedf07` → `317cf96` → `defd4ec` →
  `9d73f12` → `d97fcf5` → `94607d7`.
- No secrets committed; no destructive ops; nested OpenShorts dirty files untouched.

## Costs

$0.00. No paid model calls, no new paid service. Reddit RSS is free.
`caffeinate` active; Docker left running.

## Credentials required (names only)

- `YOUTUBE_DATA_API_V3_KEY` — for YouTube keyword search (absent; channel RSS works without it).
- Reddit — none (first-party RSS; JSON/OAuth API not used).

## Manual actions required (Bryan)

- (Optional) Provide `YOUTUBE_DATA_API_V3_KEY` for YouTube keyword search.
- Migrations 018–026 all applied; nothing blocking.

## Next

Migrations 018–026 applied; Source Review Inbox runtime-proven (8/8). Next
segment: **Acquisition Boundary** — ingest only approve-for-acquisition
candidates (still requires explicit authorization before download/publish).
**Pass 2 (bounded media)** deferred. TikTok HELD until explicitly authorized.
No automatic download/processing/publication at any point.
