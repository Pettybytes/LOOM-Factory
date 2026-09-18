# Acquired-Card UX — Fixes, Verification, and Re-baseline

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**New Apify calls:** 0 · **New downloads:** 0 · **New provider charge:** $0 · **Migrations:** 0

---

## 1. Why Preview / Play and Open in Reaction Library timed out (root cause)

The adapter resolved the **source context before acknowledging** the interaction, and
`acquisition_ops.context_for()` performs several Supabase reads. When those were slow, the
acknowledgement missed Discord's ~3 s deadline — the handler finished (the access log shows a 200
arriving *after* Discord had given up), but the user saw "didn't respond in time".

**Fix:** `handle_adapter_interaction()` now sends the deferred acknowledgement **first** and only then
resolves the context through an injected `context_provider`. Once a deferred ACK has been sent, a
private note can no longer be a second response — `DiscordTransport.ephemeral()` now posts a **webhook
follow-up** (`flags: 64`) instead, so notes are never silently dropped.

Also fail-closed: if context resolution raises, the entry point catches it, the lane short-circuits with
`CONTEXT_UNAVAILABLE`, and **nothing is written** — no job, no decision, no card rewrite.

## 2. Media attachment — what Discord could actually carry

The guild's real limit was queried, not assumed:

| | |
|---|---|
| Guild | Clippy Factory · `premium_tier 0` |
| Upload limit | **10.0 MiB** (10,485,760 bytes) |
| Asset | `instagram__DdQE_RUS0GJ__06e1da6120df.mp4` · **15.9 MiB** (16,689,411 bytes) |
| Decision | **video NOT attached** — takes the documented graceful fallback |

The card therefore shows the exact reason, sourced from one shared helper (`oversize_reason`) so the
Preview field and the upload decision cannot disagree:

> **Preview** — Video not attached — 15.9 MiB exceeds this server's upload limit of 10.0 MiB
> (premium tier 0). Playable locally in the Reaction Library · `instagram__DdQE_RUS0GJ__06e1da6120df.mp4`

A poster frame was extracted with `ffmpeg -frames:v 1` (**no transcoding**; the asset's SHA-256 was
verified identical before and after: `06e1da6120df…4b950`) and attached — Discord renders it via the
embed image (`cdn.discordapp.com/…/poster.jpg`, verified fetchable: **HTTP 200, image/jpeg, 64,010
bytes**).

**Honest limitation:** for this asset Discord cannot play the video inline, so the card does not claim
it can. Assets ≤10 MiB attach as native attachments and play in-client — that path is implemented and
unit-tested, but has not yet run against a real acquisition.

## 3. Buttons — what changed

| Before | After |
|---|---|
| `Preview / Play` (timed out) | **removed** — playback is the native attachment or the library page |
| `Open in Reaction Library` (timed out) | **link button** to a real route, omitted entirely when no route exists |
| `Hold` | kept — ACKs first, then persists durably |
| `View Error` | kept, ephemeral, ACK-first |

Link buttons carry `url` and **no** `custom_id`, so they can never invoke a handler.

## 4. Reaction Library route (the load-bearing fix)

The local helper was POST-only; it now serves read-only library pages on `127.0.0.1:8787`:

* `GET /library/<sha256>` → detail page: native `<video>` player, filename, size, duration, content
  type, **SHA-256**, platform, source URL, adapter, provider run id, acquisition status, acquired-at.
* `GET /library/<sha256>/media` → the asset, read-only, **Range-capable** (verified `206` +
  `Content-Range`), so seeking works.
* `GET /health` → `{"ok": true, "service": "loom-local-helper"}`.

The file path is resolved from the library ledger and containment-checked inside `LIBRARY_DIR`; no
caller-supplied path is honoured. Bound to loopback, so it is reachable from this Mac only — a link
button pointing there works locally and is not an internet-visible destination.

**Verified live:** page `HTTP 200` (2,069 bytes, contains the filename and SHA-256); media
`HTTP 206 · video/mp4` with a 1,024-byte ranged read.

## 5. State / action acceptance matrix

Enforced by `test_card_action_matrix.py` (11 tests) across REVIEW, APPROVED, ACQUIRING, ACQUIRED and
FAILED:

* every button is **interactive XOR a link** (never both, never neither);
* every interactive button parses, its action is routed somewhere, and — **when enabled** — the guard
  permits it in that state; locked buttons are explicitly `disabled`;
* every link button is `http(s)` and carries no `custom_id`;
* ACQUIRING locks everything (and still shows what is happening);
* ACQUIRED has **no** `Preview / Play`, and its library button is a link when a route exists and
  **absent** when one does not (its only button is then `Hold`);
* the Preview field never claims unavailable playback.

*HELD* is not a lane card state: a Hold persists through the existing review contract
(`source_review` decision) and ends the lane, leaving the asset untouched — covered by
`TestHoldIsDurableAndSafe`.

## 6. Live card repair (message `1550616797865181315`)

```
title      🎬 ACQUISITION · ACQUIRED            edited 2026-09-18T22:06:13Z
poster     attaché → cdn.discordapp.com/.../poster.jpg  (200, image/jpeg)
Preview    Video not attached — 15.9 MiB exceeds this server's upload limit of 10.0 MiB
           (premium tier 0) | Playable locally in the Reaction Library · `…06e1da6120df.mp4`
Library    ✅ Acquired · `instagram__DdQE_RUS0GJ__06e1da6120df.mp4` · sha256 `06e1da6120df…`
Cost       Actual $0.0036 · ceiling $1.0000 · 8 acquisitions left
Provenance ID `DdQE_RUS0GJ` · `apify/instagram-reel-scraper` · Authorized by Bryan · run `gpsMyaQqiBVs8Ardo`
buttons    [ Open in Reaction Library → http://127.0.0.1:8787/library/06e1da…4b950 ]  [ Hold ]
```

No dead buttons remain. The adapter was restarted (**pid 87814**) so the ACK-first fix is live;
the helper runs as **pid 82111**.

## 7. Reservation estimate re-baseline (evidence-backed, idempotent)

Apify reports `usageTotalUsd = 0.0036` per single-result run — observed on both
`0DPOKk2bEwxqVTS0i` and `gpsMyaQqiBVs8Ardo` — while the pinned estimate was `$0.0026`.
Corrected:

| | Before | After |
|---|---|---|
| `cost.REEL_SCRAPER_RESULT_MICRO` | 2,600 | **3,600** |
| `loom_provider_runs` `0DPOKk2bEwxqVTS0i`.`cost_actual_micro` | 2,600 | **3,600** |
| `loom_provider_ceiling.lifetime_spent_micro` | 6,200 | **7,200** |
| `acquisitions_succeeded` | 2 | 2 (unchanged) |

The run-row correction is gated on its **old** value and the ceiling only moves when that correction
applies — re-running reports `idempotent_noop: true` with the ceiling steady at 7,200.
(`cost_estimate_micro` on the historical row is deliberately left at 2,600: it records what we believed
at the time, and the actual is now the provider's own figure.)

Ten test assertions that had hardcoded the old estimate now track
`cost.REEL_SCRAPER_RESULT_MICRO`, so a future re-baseline updates them automatically.

## 8. Tests

```
acquisition      314 passed
discord-review   134 passed
```

New coverage: media attach/fallback (9), card action matrix (11), ACK-first ordering (5).

## 9. Still unproven / caveats

1. **Hold was not clicked live by me** — its behaviour is proven by tests (ACK-first, durable decision,
   asset untouched) and by the matrix.
2. **No real acquisition has run since these changes**; the ≤10 MiB native-attachment path is
   implemented and unit-tested but not yet exercised end-to-end.
3. The library route is a **loopback-only** destination: valid from this Mac, not from elsewhere.
4. `updated_at` on the job patch is stamped by us (the table has no trigger).

Nothing committed or pushed; no provider call, download, or charge in this pass.
