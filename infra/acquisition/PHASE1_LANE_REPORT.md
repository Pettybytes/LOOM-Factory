# Phase 1 — Approved Instagram Acquisition Lane: Acceptance Review

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none (not staged, not pushed)
**Apify calls:** 0 · **Spend:** $0 · **Downloads:** 0 · **Migrations:** 0 · **Production DB writes:** 0
**Asset:** `instagram__DdZS1QRhrXE__9b22f887b2ff.mp4` — sha256 **unchanged**, mtime unchanged

---

## 1. What is real, what is mocked, what still needs runtime proof

### Real (implemented, unit-tested, exercised locally)

| Capability | Where |
|---|---|
| Premium operator card, 5 states, platform-agnostic | `acquisition_card.py` |
| Gate 1 — Approve qualifies a source and creates **exactly one pending job** (no spend) | `approved_lane.plan_pending_job` |
| Gate 2 — Acquire for Reaction creates **exactly one spend-authorized job** | `approved_lane.plan_authorized_job` |
| Spend-authorization enforcement (a pending job can never dispatch) | `approved_lane.require_spend_authorization`, called first by the runner |
| Pilot-bound clamping (**≤100 MiB, ≤5 min**, clamp-down only, never widen) | `approved_lane.enforce_pilot_bounds` |
| **One attempt**, explicit retry only, **no fallback actor** | `approved_runner.run_approved_job` |
| One exact URL / one item (`resultsLimit=1`, `username=[url]`, no premium stored-file flag) | reused `provider.build_input` |
| Duplicate-click, in-flight, already-acquired, illegal-transition guards | `acquisition_card.guard_action` |
| Repeat-acquisition protection against the Reaction Library | `approved_lane.library_has_content` |
| Secret hygiene (no signed URL / cookie / auth header in records or logs) | `approved_lane.safe_record`, `provider.build_provenance`, `sanitize_display_url` |
| **Acknowledge-first** interaction (type 6) before any store or runner work | `acquisition_ops.handle` |
| **Update-in-place** (`PATCH …/messages/@original`) — one card tracks the whole flow | `acquisition_ops.DiscordTransport` |
| Adapter routing for `AQ:<code>:<ref>` custom ids | `adapter.py` (`_handle_acquisition_button`) |
| Durable store over the existing tables (`loom_acquisition_jobs`, `loom_reaction_library`) | `acquisition_ops.SupabaseLaneStore` |
| Poster frame extracted from the retained asset for preview | `card-previews/posters/…jpg` |
| Rendered evidence from the live payload | `render_card_previews.py` |

### Mocked (deliberately, and proven against the REAL orchestrator)

* Provider **transport / downloader / finalizer** are injected mocks — the acquisition path runs
  through the real `orchestrate.dispatch`, so routing, validation, cost reservation and
  reconciliation are genuinely executed; only the network and the disk writes are simulated.
* No Apify run was made. No money moved.

### Still needs runtime proof (not claimed as done)

1. **A live click in the real Discord surface.** Nothing was posted to Discord (not authorized),
   so the deployed adapter has not yet served an acquisition interaction end-to-end.
2. **`SupabaseLaneStore` writes.** Implemented over the existing tables but **not exercised** —
   creating jobs in the production database is gated behind `LOOM_ACQUISITION_DURABLE_STORE=1`
   and no production row was written.
3. **The live spend path.** `_acquisition_live_runner()` returns `None` by default and refuses to
   spend even when `LOOM_ACQUISITION_LIVE=1`: the Apify transport + token, bounded downloader and
   finalizer wiring is a separate explicit authorization.
4. **One real provider acquisition** under the bound ($0.0026, ceiling $1.00 / 9 remaining).

**No migration is required.** Gate separation is expressed with two rows, because
`loom_acquisition_jobs.dedup_key = f(fingerprint, authorization_type, mode, range)` already makes
"qualification" and "spend authorization" independently idempotent records; gate 2 links to gate 1
via `correlation_id`.

---

## 2. Click-to-acquisition flow (exact behaviour)

```
1. REVIEW card          → [Approve] [Deny] [Hold] [View Source]
2. Approve (gate 1)     → ACK type 6 immediately
                        → plan_pending_job (authorization_type=approved_for_acquisition,
                          status=queued, status_reason=pending_acquire_authorization)
                        → create_job (idempotent on dedup_key)
                        → card PATCHed in place → APPROVED
                        → spends nothing, downloads nothing
3. APPROVED card        → [Acquire for Reaction] [Hold] [Deny] [View Source]
4. Acquire (gate 2)     → ACK type 6 immediately
                        → refuse if not approved / already acquired / already authorized
                        → plan_authorized_job (authorization_type=acquire_for_reaction,
                          correlation_id=pending job's dedup_key)
                        → runner: require spend authorization → clamp bounds →
                          pin actor → ONE dispatch (1 URL, 1 item) → finalize
                        → card PATCHed in place → ACQUIRED (library, hash, actual cost)
                          or FAILED (classified error, manual retry only)
5. ACQUIRED card        → [Preview / Play] [Open in Reaction Library] [Hold]
   FAILED card          → [⚠️ Retry Manually] [Hold] [View Error] [View Source]
```

Rejections are always ephemeral and never mutate state: `duplicate_click`, `in_flight`,
`already_acquired`, `not_allowed`, `NOT_APPROVED`, `ALREADY_AUTHORIZED`, `ALREADY_FINISHED`,
`ATTEMPTS_EXHAUSTED`, `ACTOR_INACTIVE`, `ALREADY_DISPATCHED`.

---

## 3. Files changed

**Tracked, modified (1 file, +58 lines, no deletions):**

```
infra/discord-review/adapter.py   +58   AQ: route, _handle_acquisition_button,
                                        _acquisition_store, _acquisition_live_runner
```

**Untracked, new (11 paths):**

| File | Lines | Purpose |
|---|---|---|
| `infra/acquisition/approved_lane.py` | 271 | two gates, bounds, enforcement, hygiene |
| `infra/acquisition/approved_runner.py` | 167 | the single bounded attempt |
| `infra/discord-review/acquisition_ops.py` | 520 | adapter surface: ack-first, update-in-place, stores |
| `infra/acquisition/acquisition_card.py` | 680 | card module (previous pass, extended this pass) |
| `infra/acquisition/render_card_previews.py` | 329 | rendered evidence harness |
| `infra/acquisition/tests/test_approved_lane.py` | 182 | lane tests (24) |
| `infra/acquisition/tests/test_approved_runner.py` | 246 | runner tests (24) |
| `infra/discord-review/tests/test_acquisition_ops.py` | 327 | operator-surface tests (22) |
| `infra/acquisition/tests/test_acquisition_card.py` | 339 | card tests (38) |
| `infra/acquisition/card-previews/` | — | 9 PNGs + `posters/` + `manifest.json` |
| `infra/acquisition/ACQUISITION_CARD_REPORT.md` | 186 | previous pass's card report |

`git status`: 1 modified, 11 untracked. **Nothing staged, committed, or pushed.**
`infra/supabase/` untouched — no migration added or modified.

---

## 4. Test results

```
python3 -m unittest discover -s infra/acquisition/tests   →  195 passed   (was 145)
python3 -m unittest discover -s infra/discord-review/tests →  100 passed   (was  78)
```

Coverage highlights: gate separation and idempotency · pending job cannot dispatch ·
one-attempt + explicit-retry-only · no fallback actor · bound clamping and never-widening ·
content-id mismatch / zero results / multiple results failures · signed-URL scrubbing ·
ack-before-store **ordering assertion** · duplicate-click suppression · repeat-acquisition block ·
card update-in-place per state · no credential in any payload.

---

## 5. Rendered evidence

`infra/acquisition/card-previews/`

| File | Shows |
|---|---|
| `card-review.png`, `card-approved.png` | gate 1 before/after |
| `card-acquiring.png` | locked buttons while a run is in flight |
| `card-acquired.png` | **real poster frame**, library record, sha256, actual cost |
| `card-acquired-NoPosterFallback.png` | honest placeholder when no poster exists |
| `card-failed.png` | classified error + manual retry |
| `card-review-narrow.png`, `card-acquired-narrow.png` | 340 px narrow behaviour |
| `card-states-contact-sheet.png` | all five states |
| `posters/instagram__DdZS1QRhrXE__poster.jpg` | single frame, `ffmpeg -frames:v 1` |

The asset was **not** copied, re-downloaded, transcoded, or modified; only a single still was
extracted, and the sha256 + mtime were re-verified afterwards.

---

## 6. Self-review — defects found and fixed during this pass

1. **Runner fed a human name into the provider allowlist.** `job["actor"]` is the *authorizing
   person*; the first implementation passed it to `provider.actor_active`, which would have failed
   every real run with `UNKNOWN_ACTOR`. Fixed: `approved_lane.default_provider_actor()` pins the
   adapter per platform, and the human stays on the job for audit only.
2. **URL normalisation desynchronised the job from the dispatch.** Stripping the query from the
   dispatched URL but not the job copy would trip `provider.verify_authorization` → `URL_MISMATCH`.
   Fixed: the stripped URL is written back onto the effective job.
3. **Failure card hid the classified code.** The `Failure` field showed only prose; it now reads
   `` `CODE` — message `` so an operator can act on it.
4. **Preview renderer silently overwrote the poster render** with the fallback because the poster
   flag was global rather than per job. Fixed with explicit per-job output names.
5. **Stray sentinel import** (`orchestrator_shim`) written into the runner during authoring —
   removed.

Also corrected during authoring: the `FAILED` card no longer claims "not yet spent" (a failed
attempt may still have been billed), and the fixture no longer carries an invented creator handle.

---

## 7. Known limits (stated plainly, not hidden)

* The 3-second ACK is respected by construction (the ACK is the first thing sent and is the HTTP
  response itself), but only a live Discord interaction can prove it under real latency.
* The duplicate-click ledger is **process-local** by design; the durable guarantee against
  duplicate jobs is the unique `dedup_key`, not the ledger.
* `ACQUIRING` state depends on the durable store to survive an adapter restart; with the
  process-local store a restart returns the card to `APPROVED`.
* Discord cannot embed the acquired MP4 from disk; in-client playback needs an attachment
  (22.6 MiB, little headroom under the 25 MiB upload ceiling) or a hosted URL. The poster frame is
  the embeddable preview and is delivered as `attachment://poster.jpg`.
* One card is one source. There is no batch or queue UI in this pass — per the scope boundary.

---

## 8. Next (requires explicit authorization)

1. Enable the durable store and post one real card to confirm the live interaction path.
2. Wire the authorized Apify transport + bounded downloader + finalizer into
   `approved_runner` via `LOOM_ACQUISITION_LIVE=1` for exactly one acquisition ($0.0026).
3. Observe the real card transitions on the wire, then close the loop in `CURRENT_STATE.md`.
