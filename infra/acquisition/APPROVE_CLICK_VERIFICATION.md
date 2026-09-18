# Approve Click — End-to-End Verification (DdQE_RUS0GJ)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Acquire invoked:** no · **Provider calls:** 0 · **Downloads:** 0 · **Spend:** $0
**Provider-run rows added:** 0 · **Ceiling changes:** 0 · **Library rows added:** 0

---

## Verdict summary

| # | Claim | Result |
|---|---|---|
| 1 | Genuine signed interaction through the live AQ route | ✅ |
| 2 | Acknowledged inside Discord's deadline | ✅ (inferred from timestamps; see note) |
| 3 | Same message updated in place to APPROVED | ✅ (with a presentation gap) |
| 4 | Exactly one new Gate 1 row for DdQE_RUS0GJ | ✅ row exists — **but its `canonical_url` is EMPTY** ⚠️ |
| 5 | No Gate 2 row created | ✅ |
| 6 | No provider call / download / reservation / run row / ceiling / library change | ✅ |
| 7 | Repeated interaction cannot create another Gate 1 row | ✅ |
| 8 | Not linked to historical `d3b4bba9` | ✅ |
| — | Fresh Gate 2 click creates a NEW Apify-path job, cannot touch the historical failure | ✅ job creation ✅ — **but preflight refuses `NO_URL`** ⚠️ |

**Headline:** the click worked end to end, and the verification found that **a Gate 2 click would
currently be refused at preflight (`NO_URL`)** because the Gate 1 row was created with an empty
canonical URL. That refusal is fail-closed — no spend, no mutation — but it blocks the live proof
until the context plumbing is fixed.

---

## 1. Genuine signed interaction through the live AQ route

```
[17:18:55] POST /discord/interactions <- 127.0.0.1     (65th interaction served)
```
No `invalid signature` and no 401 for interactions anywhere in the recent log — the Ed25519 check
passed, so the request genuinely came from Discord. The handler then ran to completion: the card was
edited and a durable row was written, neither of which happens on a rejected interaction.

*Honest note:* the `[adapter] … 200 -` access line for that request is not yet in the log (only the
pre-handler line is flushed). The proof that the handler completed is the card edit + the row, not
that log line.

## 2. Acknowledged within Discord's deadline

| Event | Timestamp (UTC) |
|---|---|
| Interaction received | 21:18:55 |
| Gate 1 row created (store work) | 21:18:56.368905 |
| Card edit applied by Discord | 21:18:56.901000 |

The ACK is emitted **before** any store work by construction (the ordering is asserted by tests), so
the acknowledgement landed ≈0.4 s before the first write and well inside Discord's 3 s window.
Discord applied the interaction result, which it only does after a valid initial response.
*Acknowledged as inference, not instrumentation — the live handler has no latency counter.*

## 3. Message updated in place

```
message_id      1550616797865181315   (unchanged — updated, not reposted)
title           ✅ ACQUISITION · APPROVED
buttons         Acquire for Reaction | Hold | Deny
edited          true  ·  2026-09-18T21:18:56.901Z
```

⚠️ **Presentation gap:** the APPROVED card has **no `View Source` link**. Cause is the same defect as
§4 — the lane context carried an empty canonical URL, so the button builder (which only renders a
link button for a non-empty URL) omitted it.

## 4. Exactly one new Gate 1 row — with an empty canonical URL

```
id                 40560336-bbe9-468e-9230-5fa880c9eef1
authorization_type approved_for_acquisition      ← Gate 1 only
status             queued
platform_content_id DdQE_RUS0GJ
canonical_url      ""                            ← ⚠️ EMPTY
retry_count        0
max_bytes          104857600    (100 MiB pilot bound)
max_duration_secs  300          (5 min pilot bound)
asset_provenance   null
actor              passportpetty#688976899094872148   ← the real Discord user
```

`jobs_total` went **5 → 6**; exactly one new row. The actor field proves the row came from Bryan's
real Discord interaction, not from a script.

**Root cause:** `acquisition_ops.context_for()` builds the lane context from *existing job rows*
keyed on the card's `ref`. For a fresh REVIEW card there are no rows yet, so `canonical_url` is
empty — and that empty value flows into `plan_pending_job(canonical_url=ctx.canonical_url)` when
Approve creates the row. The same gap removed the `View Source` button in §3.

## 5. No Gate 2 row created

`acquire_for_reaction` rows for this content id: **1** — the historical `d3b4bba9`, unchanged
(id, dedup_key `7f9ddc86…`, status `failed`, `chrome_network_capture` provenance).

## 6. No spend, no side effects

| Metric | Value |
|---|---|
| Provider runs | 1 total, **0 for this target** |
| Cost reserved | $0.0000 |
| Ceiling | 2,600 micro / 1 success / max 10 / not disabled — **unchanged** |
| Library rows | 1 (target absent) |
| Library directory files | 1 — no download |
| Asset sha256 | `9b22f887b2ff…ae096` — **unchanged** |

## 7. Duplicate interaction is safe

```
stored dedup_key      a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4
recomputed dedup_key  a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4  ← identical
constraint            loom_acquisition_jobs_dedup unique (dedup_key)
```
A second Approve recomputes the *same* key, so the insert is ignored by the unique constraint; the
lane also short-circuits first (`pending_job` found → `created: false`), covered by tests.

## 8. Distinct from the historical failure

| | New Gate 1 row | Historical row |
|---|---|---|
| id | `40560336…` | `d3b4bba9…` |
| dedup_key | `a3935fe9…` | `7f9ddc86…` |
| type | `approved_for_acquisition` | `acquire_for_reaction` |
| provenance | none (`null`) | `chrome_network_capture` |
| link | `correlation_id` empty | n/a |

## Gate 2 safety proof (no Acquire invoked)

A fresh Gate 2 click **would create a NEW job** and **cannot touch the historical failure**:

| Check | Result |
|---|---|
| New job type / status | `acquire_for_reaction` / `queued` ✅ |
| New `dedup_key` ≠ historical | `d84885c4…` ≠ `7f9ddc86…` ✅ (cannot upsert over it) |
| Links only to the new Gate 1 row | `correlation_id = a3935fe9…` ✅ |
| `retry_count` starts at 0, not inherited | ✅ (and a 2nd attempt needs explicit authorization) |
| Actor selected | allowlisted `apify/instagram-reel-scraper` — never `chrome_network_capture` ✅ |
| Bounds | 100 MiB / 300 s ✅ |
| Data sources consulted | **only** `pending_job(DdQE_RUS0GJ)`; no reference to the historical id or dedup key ✅ |
| Provider called | **no** (assertion-guarded fake transport; `provider_not_called: true`) |
| **Preflight result** | ❌ **`NO_URL` — "authorized job carries no canonical URL"** |

So the isolation question is answered **yes**: the historical failure cannot be selected, retried,
mutated, or inherited from. But the click would be **refused before dispatch** because the Gate 1 row
it links to has no canonical URL — fail-closed and correct in direction, blocking in effect.

---

## Required fix (not applied — verification-only pass)

1. Carry the source's canonical URL into `LaneContext` when the card is rendered/clicked (the URL is
   already known to the card payload and to the submission), and include it in the update facts so
   the APPROVED card keeps `View Source`.
2. The existing Gate 1 row `40560336…` still has an empty URL; repairing it is a **write**, so it
   needs explicit authorization (re-create the row, or update the field).

Until then, **do not click Acquire for Reaction** — it will be refused with `NO_URL`, and no spend
can occur either way.
