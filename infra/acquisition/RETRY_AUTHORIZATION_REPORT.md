# Retry Authorization — Proof + Armed State (pre-click report)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Retry clicked:** no · **Handler invoked directly:** no · **Apify calls:** 0 · **Downloads:** 0 · **Spend:** $0
**New reservations / run rows / library rows:** none · **Adapter:** pid 66540 · **`LOOM_ACQUISITION_LIVE=1`**

---

## 1. Mocked retry-authorization proof

`infra/acquisition/tests/test_retry_authorization.py` — **12 tests, all passing**, no network. The
provider path is the real `orchestrate.dispatch` with a mocked transport; the ledger is the real
`DurableLedger` over an in-memory PostgREST stand-in.

| Required claim | Test | Result |
|---|---|---|
| Separate explicit retry authorization (not a repeat Acquire) | `test_retry_is_a_separate_explicit_authorization` | PASS |
| `retry_count = 1` preserved before execution | `test_retry_count_is_one_before_execution` | PASS |
| Row reopened with the counter intact, marker recorded | `test_job_row_was_reopened_with_the_original_counter` | PASS |
| Exactly one additional bounded attempt (attempts → 2) | `test_exactly_one_additional_bounded_attempt` | PASS |
| Wire call uses numeric Actor id `xMc5Ga1oCONPmWJIa` | `test_wire_call_uses_the_numeric_actor_id` | PASS |
| Provenance keeps the readable slug `apify/instagram-reel-scraper` | `test_provenance_keeps_the_readable_slug` | PASS |
| Exactly 2,600 micro reserved | `test_reserves_exactly_2600_micro` | PASS |
| Correlation stays with Gate 1 `40560336…` | `test_stays_correlated_to_the_gate1_row` | PASS |
| Historical chrome job `d3b4bba9…` never consulted | `test_historical_chrome_job_is_never_touched` | PASS |
| Second retry refused without another authorization | `test_a_second_retry_is_refused` | PASS |
| No automatic retry, no fallback actor | `test_nothing_retries_automatically_and_no_fallback` | PASS |
| Previous audit row is not reused | `test_previous_audit_row_is_not_reused` | PASS |

**Extra defect found and fixed by this proof:** the attempt limit was enforced *inside* the runner,
after `live_runner` had already reserved budget — so a refused second retry still held 2,600 micro and
would have left an audit row for a run that was never permitted. The check now runs **before any
reservation**, and the proof asserts a refused retry produces **no provider call, no reservation, and
no ceiling movement** (ceiling stays 2,600).

## 2. Audit row immutability (read-only check)

```
run_id      no-apify-run:b1bb78e6-3601-47d9-9068-46afb45587e1
status      failed_cost_known   actual 0   estimate 2600
rows for this job: 1            total run rows: 2
```
The sentinel row is unchanged and **cannot be reused**: a retry's run id is either the real Apify run id
captured from the transport, or the deterministic fallback `<dedup_key>:2` — never the sentinel
(asserted by `test_previous_audit_row_is_not_reused`).

## 3. Armed state — health and ceiling before the click

All ten health assertions pass:

```
route=AQ ✅ · route_active ✅ · store=durable ✅ · live_runner=enabled ✅ · apify_token_present ✅
max_bytes=104857600 ✅ · max_seconds=300 ✅ · max_results=1 ✅ · max_attempts=1 ✅ · fallback=false ✅
```

Ceiling before the click: **2,600 micro spent · 1 success · ceiling $1.00 · max 10 · not disabled →
$0.9974 and 9 acquisitions remaining.** Gate 1 row `40560336…` is `queued`, `retry_count 0`, canonical
URL intact. Historical `d3b4bba9…` untouched (`failed`, dedup `7f9ddc86…`). Library holds only the
earlier `DdZS1QRhrXE` asset.

## 4. Card state (updated in place)

```
message_id 1550616797865181315        (same message, edited 2026-09-18T21:37:40.741Z)
title      ⚠️ ACQUISITION · FAILED
Failure    `404` · at *actor_start* — Actor start returned HTTP 404 — no Apify run was created,
           no dataset fetched, no media downloaded. The reservation was released. Retry Manually
           is a NEW explicit authorization and may spend up to $0.0026 (the per-item bound);
           further retries each need another authorization.
Cost       Actual $0.0000 · est. $0.0026 · ceiling $1.0000 · 9 acquisitions left
buttons    ⚠️ Retry Manually (enabled) | Hold (enabled) | View Error (enabled) | View Source
retry_visible: true   retry_executable: true   hold_executable: true   spend_statement_present: true
```

## 5. Suites and guards

```
test_retry_authorization      12 passed
acquisition                  285 passed
discord-review               120 passed
```

No provider call, no reservation, no provider-run row, no download, no library mutation, no
direct handler invocation, nothing committed or pushed.

---

## Next action — Bryan

**Click `⚠️ Retry Manually` on the card exactly once.**

Expected: **one** bounded attempt that reserves $0.0026, calls the API with numeric actor id
`xMc5Ga1oCONPmWJIa` (provenance still `apify/instagram-reel-scraper`), stays correlated to Gate 1
`40560336…` and never touches the historical chrome job. On success the card becomes ACQUIRED and the
asset lands in the Reaction Library; on failure it returns to FAILED with a classified reason and the
ledger either keeps or releases the reservation per the actual charge. A **second** retry requires a
fresh authorization.
