# Gate 1 Runtime Proof — Approved Instagram Acquisition Lane

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none (not staged, not pushed)
**Apify calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **Provider runs created:** 0
**Asset:** `DdZS1QRhrXE` sha256 + mtime **unchanged**

---

## 1. Precondition correction (done BEFORE posting, as required)

**Finding:** real `Deny`/`Hold` were implemented as ephemeral acknowledgements — no durable record —
while the existing review contract (`loom_source_reviews`, first-decision-wins) requires durability.

**Correction applied to `infra/discord-review/acquisition_ops.py`:**

* `LaneStore` gained `record_decision(...)` / `decision_for(...)`.
* `MemoryLaneStore` implements first-wins decisions locally.
* `SupabaseLaneStore` persists through the **existing** contract —
  `source_review.source_decision_payload` + `source_review.persist_decision` → `loom_source_reviews`
  (no new table, no migration).
* `handle()` now routes `Hold`/`Deny` through **ACK-first → durable persist → update-in-place**
  (card becomes the existing "Decision recorded" / "Already decided" embed, components cleared).
* A later `Deny` after a `Hold` cannot overwrite the first decision (`created: false`).
* A dead `Hold` branch that would have shadowed the durable path was removed.

**Documented distinction:** only *invalid or duplicate interaction attempts* remain ephemeral
(`duplicate_click`, `in_flight`, `already_acquired`, `not_allowed`) — they are courtesy messages that
mutate nothing. Real operator decisions are always durable.

Regression guards added: `TestDurableDecisions` (5 tests) + `test_guard_rejection_is_the_only_ephemeral_case`
+ `test_persistable_job_keeps_its_schema_columns`.

**Suites after correction:** acquisition **196 passed**, discord-review **105 passed**.

---

## 2. Gate 1 runtime proof — what was exercised

Harness: `infra/discord-review/prove_gate1_lane.py` (one card, one Approve, one duplicate replay).

| Step | Result |
|---|---|
| Card posted to designated channel | **#source-review**, guild `1546266463663423518`, channel `1550207989569421444` |
| Message | `1550612384178249758` |
| Live card | https://discord.com/channels/1546266463663423518/1550207989569421444/1550612384178249758 |
| Poster frame | real extracted still, uploaded as `poster.jpg` and referenced as `attachment://poster.jpg` |
| Approve → acknowledgement | type **6** (deferred update) in **0.1 ms** — far inside Discord's ~3 s deadline |
| Approve → update in place | **925 ms**, same message, title became **`✅ ACQUISITION · APPROVED`** |
| Pending job created exactly once | `created: true` |
| Repeat Approve *inside* the window | `duplicate_click` — ephemeral only, **no patch, no job** |
| Repeat Approve *outside* the window | idempotent (`created: false`), card re-patched, **still one job** |

Post-approve card read back live from Discord (independent of the harness):

```
title      : ✅ ACQUISITION · APPROVED
color      : 3066993  (green)
thumbnail  : cdn.discordapp.com/attachments/.../poster.jpg   (Discord's own copy)
components : Acquire for Reaction | Hold | Deny | View Source
footer     : Instagram · Authorized. Acquisition is a separate, explicit action. ·
             dreemzy · acquisition-card-proof · 1 URL · 1 item · ≤5 min · ≤100 MiB ·
             breaker $1.00 / 10 · 🧪 FIXTURE — downstream locked
```

---

## 3. Exactly one pending qualification job (durable row)

```
id                 : d8b7e566-edca-4123-b1a8-fbcfaa1f8b78
dedup_key          : 50f0da5dc21311d81368b492f02520e497413edb9ac121146792eb355efbb12d
authorization_type : approved_for_acquisition      ← Gate 1 only (qualify, NOT spend)
status             : queued
canonical_url      : https://www.instagram.com/reel/DdZS1QRhrXE/
platform           : instagram
platform_content_id: DdZS1QRhrXE
destination        : personal_reaction_library
actor              : gate1-runtime-proof          (honest attribution)
max_bytes          : 104857600   (100 MiB pilot bound)
max_duration_secs  : 300         (5 min pilot bound)
retry_count        : 0
asset_provenance   : null        (nothing downloaded)
```

Query `loom_acquisition_jobs?duplicate_fingerprint=eq.DdZS1QRhrXE` returned **exactly one row** after
three Approve attempts. No `acquire_for_reaction` row was created — Gate 2 was never touched.

---

## 4. Zero spend, zero side effects (verified, not assumed)

| Check | Before | After |
|---|---|---|
| `loom_provider_runs` rows | 1 | **1** (none for this job) |
| `loom_provider_ceiling` | `lifetime_spent_micro=2600`, `acquisitions_succeeded=1` | **identical**, `updated_at` unchanged |
| `loom_reaction_library` rows | 1 | **1** |
| Cost reserved | — | **$0.0000** (Gate 1 never reserves) |
| Apify calls / downloads | — | **0 / 0** |
| Asset sha256 | `9b22f887b2ff…ae096` | **identical** |
| Asset mtime | Sep 17 20:22:09 | **identical** |

Cost displayed on the card is the **estimate only**; once approved, the real cost of $0.0026 is charged
by Apify on completion — not by the approval. Nothing was charged here.

---

## 5. Stored URL canonicalization / credential hygiene

* `canonical_url` stored: `https://www.instagram.com/reel/DdZS1QRhrXE/`
* No query string, no fragment, no `stkn`/`utm_source`, no signed parameters, no cookies, no auth headers.
* `signed=no`, `credentials=no` asserted programmatically.
* Screens / records: the only Discord CDN URL in existence is Discord's own attachment URL, which LOOM
  never persists (the payload references `attachment://poster.jpg`).
* A `safe_record` regression was caught and fixed before this run: the scrubber was dropping the
  `authorization_type` **schema column** because its name contains "authorization". Credential *carriers*
  are still dropped; `*_type` fields survive.

---

## 6. Honest limits of this proof

1. **The click was delivered straight into the handler**, not over Discord's interactions endpoint —
   only Discord can produce a valid Ed25519 signature, so signature verification was bypassed for the
   driven interaction. Every other step (card build, guards, gates, durable Supabase write, update
   payload) is the production path.
2. **Update-in-place used the bot token** (`PATCH /channels/{id}/messages/{id}`) rather than the
   interaction-token webhook route. The visible Discord outcome is identical; the wire route differs.
3. **The running adapter service was not restarted** (it still serves the pre-change build), so the
   `AQ:` route is not yet live in the running process. Clicking the buttons on the posted card *right now*
   would be rejected by the old process. Restarting the adapter is the next step and needs your go-ahead.
4. `ACQUIRING`/`ACQUIRED`/`FAILED` transitions and Gate 2 were **not** exercised live — out of scope here.

---

## 7. Next steps (each needs explicit authorization)

1. Restart the adapter with the new build (+ `LOOM_ACQUISITION_DURABLE_STORE=1`) so the live route serves
   `AQ:` interactions, then re-verify with one real human click.
2. Wire the authorized Apify transport for one Gate 2 acquisition ($0.0026) under the existing $1 ceiling.
3. Update `CURRENT_STATE.md` with the Gate 1 proof and the Gate 2 boundary.
