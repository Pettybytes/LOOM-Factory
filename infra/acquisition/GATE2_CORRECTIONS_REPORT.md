# Gate 2 Corrections — Ledger + Card Reconciliation (Report)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none (not staged, not pushed)
**Apify calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **New Gate 2 job:** none · **Migrations:** 0
**`LOOM_ACQUISITION_LIVE`:** `0` (verified in `run.sh` line 21)

---

## 1. Correction 1 — the DdZS1QRhrXE card now reflects reality

The Gate 1 card claimed "APPROVED" while the reel was already in the Reaction Library. It was
reconciled **in place** (same message, one `PATCH`), built from the real library record:

| | Value |
|---|---|
| Message | `1550612384178249758` (unchanged id — updated in place) |
| Before | `✅ ACQUISITION · APPROVED` · `Acquire for Reaction \| Hold \| Deny \| View Source` |
| After | `🎬 ACQUISITION · ACQUIRED` · `Preview / Play \| Open in Reaction Library \| Hold` |
| Reaction Library field | `✅ Acquired · instagram__DdZS1QRhrXE__9b22f887b2ff.mp4 · sha256 9b22f887b2ff…` |
| Cost field | `Actual $0.0026` · `ceiling $1.0000 · 9 acquisitions left` |

Facts came from the existing records only — filename, sha256, 23,721,338 bytes, 55.26 s, actual cost
2,600 micro — **no invented data, no new provider call, no download, no asset change.**

**Stale-button proof** (the old Acquire custom_id, invoked against the live durable store):

```json
{"card_state_seen":"acquired","ok":false,"refusal":"already_acquired",
 "ephemeral":["Already in the Reaction Library — repeats are blocked."],
 "runner_calls":0,"provider_or_store_effects":{}}
```

The refusal happens at the **guard, before any store write, runner call, transport call, or
download** — `runner_calls: 0` and empty effects. (The runner's own preflight also classifies this
as `ALREADY_ACQUIRED`, covered by tests.)

---

## 2. Correction 2 — durable provider-run reservation + actual-cost reconciliation

New module `infra/acquisition/ledger.py`, built **on migrations 034/035 as they exist**.

**Atomicity assessment (required before proceeding):** 034/035 are plain tables — no functions, no
triggers. They are nonetheless sufficient, because a PostgREST conditional PATCH is a
compare-and-swap on the single breaker row:

```
PATCH /loom_provider_ceiling?id=eq.1
      &lifetime_spent_micro=eq.<observed>&acquisitions_succeeded=eq.<observed>
body: {lifetime_spent_micro: <observed + reserved>}
```

Postgres serialises the UPDATE: of N processes that observed the same state, exactly one matches
the filter; the rest get zero rows and re-read. **No migration was needed, so none was added.**

**Flow, per authorised Gate 2 click:**

1. **Reserve** the worst case (`$0.0026`) durably, before the provider network call.
2. **Dispatch** one bounded attempt (unchanged path).
3. **Record** one `loom_provider_runs` row (034), status `reserved`, keyed on the unique `run_id`
   captured from the transport.
4. **Reconcile** the actual charge into 035.

**Exactly-once reconciliation:** the ceiling delta is applied only on the run row's
`reserved → final` transition, itself a CAS. A duplicate reconcile, a retried callback, or a crash
replay matches zero rows and applies no delta (verified).

**Truthful failure accounting:**

| Outcome | Run status | Reservation |
|---|---|---|
| Success | `acquired` | adjusted to the actual charge; success counter +1 |
| Billed failure with known charge | `failed_cost_known` | charge kept |
| Failure with unknown charge | `failed_cost_unknown` | **kept, never credited back** — the provider may already have billed; `cost_actual_micro` is `NULL` (classified, never assumed `$0`) |
| Cheaper actual than reserved | any | difference credited back (never more than reserved) |

**Fail-closed:** no ledger ⇒ `LEDGER_UNAVAILABLE` and **no spend**; a breaker/CAS refusal ⇒
classified refusal with zero provider calls.

**Hygiene:** run rows carry only schema fields — no token, no signed URL, no cookies, no headers.

---

## 3. Required proofs

| Proof | Status | Evidence |
|---|---|---|
| Successful reservation → actual reconciliation | ✅ | `test_successful_run_reconciles_to_the_actual_charge`, `test_successful_reservation_increments_the_ceiling` |
| Billed provider failure preserves the audit record + charge | ✅ | `test_billed_provider_failure_keeps_the_charge` |
| Unknown-cost failure never falsely restores money | ✅ | `test_unknown_cost_failure_never_restores_the_reservation` (spent stays 2600, `cost_actual_micro` NULL) |
| Duplicate reconciliation / crash replay idempotent | ✅ | `test_duplicate_reconciliation_is_idempotent`, `test_replay_after_a_crash_cannot_double_charge` |
| Concurrent processes cannot double-spend the budget | ✅ | `test_two_processes_cannot_double_spend_the_remaining_budget`, `test_a_lost_cas_race_is_retried_not_double_counted`, `test_hopeless_race_raises_cas_conflict` |
| Already-acquired card cannot reach the provider transport | ✅ | card reconciliation run (`runner_calls: 0`) + `test_already_acquired_is_refused`, `test_repeat_acquisition_of_a_library_item_is_blocked` |
| Reservation happens **before** the provider call | ✅ | `test_reservation_happens_before_the_provider_call` (asserts `order.index("reserve") < order.index("provider")`) |
| Secret/signed-URL hygiene | ✅ | `test_run_row_records_no_secrets`, `test_no_signed_url_or_secret_in_the_outcome_or_library_record` |

**Suites:** acquisition **242 passed** (was 223; +14 ledger, +5 ledger-integration),
discord-review **120 passed**. Total **362 green**.

---

## 4. Adapter restart + health

Restarted only the adapter through its supervisor; `helper.py` (8787) kept PID 83512.

```json
{"status":"ok","acquisition":{"route":"AQ","route_active":true,"store":"durable",
 "gate1_approve":true,"gate2_acquire":true,"live_runner":"disabled",
 "durable_store_flag":"1","apify_token_present":true,
 "bounds":{"max_bytes":104857600,"max_seconds":300,"max_results":1,
           "max_attempts":1,"fallback":false}}}
```

New adapter PID **50455**. `apify_token_present: true` because `run.sh` now sources
`LOOM_APIFY_API_TOKEN` **by name** (value never printed) — while `live_runner: disabled` because
`LOOM_ACQUISITION_LIVE=0`. Enabling spend thus remains a deliberate two-part act.

**Defects found and fixed this pass:**

1. `LiveIO`'s dataclass `__init__` overwrote the ledger my test set beforehand → 10 failures; fixed.
2. Reconcile script used the `Bearer` scheme for a Discord **bot** token → HTTP 401; corrected to
   `Bot` (Supabase keeps `Bearer`).
3. Secret-masking artifacts (`***`) written into the script; replaced with concatenated scheme
   constants and re-verified by grep.

---

## 5. What is still unproven

1. **No real acquisition has run** — no provider call, no download, no spend in this pass.
2. The ledger has only ever been exercised against a **fake PostgREST**; it has never written a real
   `loom_provider_runs` / `loom_provider_ceiling` row (no Gate 2 has occurred).
3. `build_live_io` (real transport + downloads + Supabase writes) remains unexecuted.
4. Concurrency is proven by construction (CAS) and by simulation — not by two live processes racing
   real rows.
5. The reconciled card is a **fixture display** over real records; the ACQUIRING transition has
   still never been observed on the wire.

---

## 6. Next live proof target (recorded here only — not actioned)

```
https://www.instagram.com/reel/DdQE_RUS0GJ/
```

No card was posted for it, no production job created, no Apify call, no download. This pass
touched only the existing `DdZS1QRhrXE` card.

---

## 7. Change set

Tracked: `infra/discord-review/adapter.py` (+88/-1), `infra/discord-review/run.sh` (+7).
New (untracked): `infra/acquisition/ledger.py`, `infra/acquisition/tests/test_ledger.py`,
`infra/discord-review/reconcile_proof_card.py`, `reconcile-proof-card-result.json`, plus the
previously created lane/card/live-runner files.

Nothing staged, committed, or pushed.
