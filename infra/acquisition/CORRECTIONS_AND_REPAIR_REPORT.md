# Corrections + Repairs — Report (Acquire 404 incident)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Apify calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **New jobs:** 0 · **Retries clicked:** 0
**`LOOM_ACQUISITION_LIVE`:** `0` · **Adapter:** pid 62470, `live_runner: disabled`

---

## 1. Missing / incomplete corrections from earlier scopes, found and fixed here

| Gap | Where | Fix |
|---|---|---|
| In-memory store rows had **no `id`**, so the new job-outcome persistence silently skipped in that path | `acquisition_ops.MemoryLaneStore.create_job` | rows now carry an id (mirroring durable rows) + `_do_acquire` falls back to `dedup_key` |
| Job patch never stamped **`updated_at`** (the jobs table has no trigger), so an outcome's time was unrecorded | `live_runner.run` | patch now includes `updated_at` (`_now_iso()`) |
| The Gate 2 job's terminal outcome was **never persisted** (root of the audit gap) | `_do_acquire` | persists `job_patch` before the card update |
| `_settle` sliced a non-string and **swallowed the failure** | `live_runner._settle` | `_reason_text()` normalizer + explicit `settlement_failed` + log |
| Provider actor sent as a **website slug** | `live_runner._RunIdCapture` | numeric REST actor id at the wire boundary |

## 2. Duplicate-Acquire enforcement and truthful state

* Guard chain before anything can spend: `guard_action` (`duplicate_click` inside 20 s, `in_flight`,
  `already_acquired`, `not_allowed`), then `ALREADY_AUTHORIZED` when the job already exists, then the
  runner's own `attempts_allowed` / `existing_run` / `ALREADY_FINISHED` checks.
* Unique key `loom_acquisition_jobs_dedup unique (dedup_key)` is the durable backstop; a repeat
  Approve recomputes an identical key (test-asserted).
* **Truthful job/ceiling state now holds**: the job reads `failed` / `retry_count 1`, so a second
  Acquire can no longer masquerade as a first attempt, and the ceiling reflects the actual $0 charge
  rather than a bare reservation.

## 3. Actor-addressing bug — fix and proof

```python
# live_runner._RunIdCapture.start_run
run = self._transport.start_run(provider.actor_api_id(actor), input_)
```
The pinned slug stays for allowlist checks, provenance and the ledger `actor_id`; only the wire call is
translated. Mock proof: `test_live_runner_sends_the_numeric_actor_id` asserts the transport received
`xMc5Ga1oCONPmWJIa` and that the path is `/acts/xMc5Ga1oCONPmWJIa/runs`.
`test_the_wrong_path_is_what_the_slug_produces` keeps the malformed
`/acts/apify/instagram-reel-scraper/runs` path on record as the regression artifact — it is no longer
*generated*.

## 4. Settlement bug — fix and proof

`_reason_text()` normalizes dict / str / int / exception / `None`; settlement is attempted for every
failure class and a failure to settle is **never silent** (logged, and `settlement_failed: True` on the
returned record). Tests: `test_provider_failure_records_the_attempt_and_keeps_the_reservation`
(HTTP 500 → audit row + reservation held), `test_settlement_executes_for_an_integer_error_code`
(the exact shape that used to raise `TypeError` → row written, $0 actual, ceiling released),
`test_settlement_never_silently_swallows_a_failure` (ledger down → flagged, not hidden).

## 5. Durable outcome — fix and proof

`_do_acquire` now persists `outcome["job_patch"]` (status, `retry_count`, `status_reason`,
`asset_provenance`, `updated_at`) via the new `update_job` store method, and the runner records
`failure_stage` from evidence (no captured run id ⇒ `actor_start`).
Tests: `test_job_outcome_is_persisted_before_the_card_is_updated` (asserts
`log.index("job_update") < log.index("card_patch")`), `test_retry_is_reopened_and_stays_distinguishable`
(retry is explicitly authorized, the job is re-opened to `queued`, the attempt counter is never reset).

## 6. 034 audit row (exactly one)

```
run_id             no-apify-run:b1bb78e6-3601-47d9-9068-46afb45587e1
provider           apify
actor_id           apify/instagram-reel-scraper
acquisition_job_id b1bb78e6-3601-47d9-9068-46afb45587e1
status             failed_cost_known
cost_estimate_micro 2600
cost_actual_micro   0
output_ref         actor_start HTTP 404 (slug actor path): no Apify run created,
                   no dataset fetched, no media downloaded
created_at         2026-09-18T21:33:20.745963Z
```
**No Apify run id was fabricated** — the sentinel literally states none exists, and the classification
is explicit. Total `loom_provider_runs` rows: 2 (the historical successful run + this audit row).

## 7. 035 ledger release — before / after

| | Before | After |
|---|---|---|
| `lifetime_spent_micro` | 5,200 | **2,600** |
| `acquisitions_succeeded` | 1 | 1 |
| `ceiling_micro` / `max` / disabled | 1,000,000 / 10 / false | unchanged |

Reservation released = 2,600 micro (exactly the held amount), ledger delta `-2600`.
**Exactly once:** re-running the repair produced `applied: false, idempotent: true` with the ceiling
untouched — the release is gated on the run row's `reserved → final` CAS.

## 8. Gate 2 job row updated

```
id               b1bb78e6-3601-47d9-9068-46afb45587e1
status           failed
retry_count      1
status_reason    actor_start: actor_start HTTP 404 (slug actor path): no Apify run created,
                 no dataset fetched, no media downloaded
asset_provenance {adapter: apify:instagram-reel-scraper, provider: apify,
                  stage: actor_start, run_id: ""}
```

## 9. Card correction (same message, one edit)

```
message_id 1550616797865181315   (unchanged, edited 2026-09-18T21:33:58.919Z)
title      ⚠️ ACQUISITION · FAILED
Failure    `404` · at *actor_start* — Actor start returned HTTP 404 — no Apify run was
           created, no dataset fetched, no media downloaded. The reservation has been
           released; a retry is manual and separately authorized.
Cost       Actual $0.0000 · est. $0.0026 · ceiling $1.0000 · 9 acquisitions left
buttons    ⚠️ Retry Manually (disabled) | Hold (disabled) | View Error (disabled) | View Source
retry_visible: true   retry_executable: false
```
Retry stays **visible but non-executable**, and with `LOOM_ACQUISITION_LIVE=0` a click would return
"Runner unavailable — nothing was authorized" with no effects.

## 10. Regression tests and suites

`infra/acquisition/tests/test_acquire_404_regression.py` — 9 tests for every corrected behaviour
(actor addressing, settlement for all failure shapes, ordering before the card, retry semantics,
single-attempt/no-fallback). Older expectations that encoded the bug were updated rather than deleted
(`test_fallback_actor_is_never_used` now asserts the numeric id at the wire).

```
acquisition     273 passed      (was 269, with 2 expected failures → now 0)
discord-review  120 passed
total           393 passed
```

**Untouched** (verified after the repair): Gate 1 row `40560336…` (`queued`, retry 0, canonical URL,
`updated_at` unchanged), historical chrome job `d3b4bba9…` (dedup `7f9ddc86…`, `updated_at`
2026-09-17), the library row/asset, and all other ceiling amounts.

---

## Standing caveat

The corrected path has **never run against the real Apify API** — the fix is proven by mocks only, and
no retry was clicked. A future attempt is a separate, explicit authorization. Nothing was committed or
pushed.
