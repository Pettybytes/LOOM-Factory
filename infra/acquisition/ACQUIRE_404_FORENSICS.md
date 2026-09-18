# Acquire Attempt #1 — 404 Failure Forensics (read-only)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Retries / resubmissions / new jobs / Apify calls:** none · **Corrections applied:** none
**Logs and state:** preserved as-is

---

## 1. Signed Discord interaction and ACK timing

```
adapter.log: [17:26:48] POST /discord/interactions <- 127.0.0.1     (66th interaction served)
```
No `invalid signature` and no 401 for interactions — Ed25519 verification passed, so the request
genuinely came from Discord. The ACK is emitted **before** any store or provider work (ordering
asserted by tests), and Discord applied the resulting FAILED card, which only happens after a valid
initial response. *As before, the `[adapter] … 200 -` access line is not yet flushed, so ACK latency
is inferred (≈2 s of headroom), not instrumented — stated plainly rather than guessed at.*

## 2. Job / run correlation

| | Value |
|---|---|
| Gate 2 job | `b1bb78e6-3601-47d9-9068-46afb45587e1` |
| dedup_key | `d84885c438eeba7e97c4269140750dff575c3ae77785ed4b2635877c80c80f03` |
| Type / status | `acquire_for_reaction` / **`queued`** (never updated — see defect #3) |
| `correlation_id` | `a3935fe97af53220986aebcaa641cee3d9406d1501b39e3664bba8363f2212b4` → **Gate 1 row `40560336…`** ✅ |
| canonical_url | `https://www.instagram.com/reel/DdQE_RUS0GJ/` ✅ |
| retry_count | 0 |
| Provider-run ID | **none — no `loom_provider_runs` row was written** |
| Apify run ID | **none captured** (the start call 404'd before returning a run id) |

Job created `2026-09-18T21:26:50.187Z`; ceiling reservation applied `21:26:51Z`.

## 3. The exact stage that returned 404

**The Actor *start* endpoint** — `POST .../acts/<actor>/runs`.

Evidence: the failure surfaced as `404 — HTTP Error 404: Not Found`, i.e. an
`urllib.error.HTTPError` whose `.code` is `404`, raised from `transport.start_run(...)`. The dataset
endpoint is only reached *after* a successful start, and no run id was ever captured, so
`get_dataset_items` never ran. The media-download URL was never constructed (no signed URL exists).

## 4. Sanitized endpoint actually used

```
POST  https://api.apify.com/v2/acts/apify/instagram-reel-scraper/runs
                                                     ^^^^^^^^^^^^^^^^^^^^^^^^  website slug
```
Structure only — no token, no query values, no headers. The REST API requires the **numeric** actor id:

```
POST  https://api.apify.com/v2/acts/xMc5Ga1oCONPmWJIa/runs      ← correct
```
The malformed path contains a slash inside the actor segment (slug `apify/instagram-reel-scraper`),
which resolves to a non-existent route. The repo already knows this: `provider.actor_api_id()` exists
for exactly this mapping, and the earlier successful proof used the numeric id (commit
*"use numeric Apify actor id (not website slug) for REST API"*).

## 5. Did Apify create a real run despite the local 404?

**No evidence of a run.** The 404 came from the *start* call, so no actor run was created and no run id
exists in any durable record. Per the instruction not to make external calls before reporting, I did
**not** query Apify to double-confirm — that remains the one check I did not perform.

## 6. Run status / actual cost from Apify

Not applicable: there is no run id to read. No Apify API call was made.

## 7. Durable ledger state (migration 035 + 034)

| | Before | After |
|---|---|---|
| `lifetime_spent_micro` | 2,600 | **5,200** (reserve +2,600 at 21:26:51Z) |
| `acquisitions_succeeded` | 1 | 1 (no success recorded) |
| `ceiling_micro` / `max_acquisitions` | 1,000,000 / 10 | unchanged |
| `disabled` | false | false |
| Provider-run row | — | **absent** (should have been `failed_cost_unknown`) |
| Actual cost | — | **not reconciled** |

**The reservation is still held (2,600 micro)** and no audit row explains it. Holding the reservation
when the charge is unknown is the *intended* conservative behaviour — the money is never freed in case
the provider already billed — **but here it is held for the wrong reason**: reconciliation never ran
(defect #2). Effect: the breaker silently shows $0.0052 spent with only one success and no run record.

## 8. No media, partial, or library artifacts

* `~/.openclaw/loom-reaction-library/library/` → only the pre-existing
  `instagram__DdZS1QRhrXE__9b22f887b2ff.mp4`
* `~/.openclaw/loom-reaction-library/tmp/` → **does not exist** (no `.part` files anywhere)
* `loom_reaction_library` → 1 row (`DdZS1QRhrXE`); nothing for `DdQE_RUS0GJ`
* No signed URL was ever obtained, so none could be persisted or logged

## 9. Historical chrome job untouched

`d3b4bba9-4e1c-49d8-b6c6-f7bf4a8fc742` — still `acquire_for_reaction` / `failed`,
`asset_provenance.adapter = chrome_network_capture`, dedup `7f9ddc86…`, `retry_count 0`,
`updated_at = 2026-09-17T22:37:40Z` (unchanged). Gate 1 row `40560336…` also unchanged
(`queued`, canonical URL intact).

## 10. Root cause and smallest correction (NOT applied)

**Defect #1 — primary, caused the 404.** The live runner hands the transport the **website slug**
(`provider.ACTOR_REEL_SCRAPER`) instead of the REST actor id, so `HttpTransport.start_run` builds
`/acts/apify/instagram-reel-scraper/runs`.
*Smallest correction:* translate the actor at the transport boundary — e.g. in `_RunIdCapture`
call `provider.actor_api_id(actor)` before delegating to the real transport — while keeping the slug for
allowlist checks, provenance and the ledger. One-line change; no contract change.

**Defect #2 — swallowed ledger failure (why nothing was recorded).** `live_runner._settle` passes
`output_ref=reason[:200]`, but `reason` is `outcome["error"]["code"]`, which is the **int 404**, so
slicing raises `TypeError`; `_settle` catches it and returns silently, so *neither* the provider run row
*nor* the reconciliation happened.
*Smallest correction:* `output_ref=str(reason)[:200]`.

**Defect #3 — audit gap.** Nothing persists the runner's `job_patch`, so the Gate 2 job row still reads
`queued` / `retry_count 0` after a failed attempt: a retry would look like a first attempt.
*Smallest correction:* after the run, persist `outcome["job_patch"]` (status, retry_count,
status_reason, asset_provenance) to the job row.

## 11. Regression test added (mocked HTTP only)

`infra/acquisition/tests/test_acquire_404_regression.py` — 5 tests, no network:

| Test | Purpose |
|---|---|
| `test_the_wrong_path_is_what_the_slug_produces` | reproduces the malformed `/acts/apify/…/runs` path |
| `test_dispatch_classifies_the_404_exactly_as_seen_live` | asserts `error == 404`, message `HTTP Error 404: Not Found`, exactly one attempt |
| `test_no_retry_after_the_failure` | a second invocation adds no provider call |
| `test_runner_failure_keeps_the_reservation_and_records_the_attempt` | **guarded expected failure** — captures defect #2 (no run row) |
| `test_live_runner_must_use_the_numeric_actor_id` | **guarded expected failure** — captures defect #1 (slug reaches the transport) |

Suites: **acquisition 269 (2 expected failures), discord-review 120** — green, with both defects
visible as guarded failures rather than a red suite.

## 12. Confirmation

* No correction applied · no retry · no resubmission · no new job · no Apify call · no external call
* No media file, temp partial, or Reaction Library row created
* Logs and current state preserved · no credentials or signed URLs exposed
* Nothing committed or pushed

**Live state right now:** adapter pid 57818, `live_runner: enabled`, `LOOM_ACQUISITION_LIVE=1`; the
card for `DdQE_RUS0GJ` shows FAILED with a **⚠️ Retry Manually** button. Do not press it — defect #3
means a retry would look like a first attempt, and defect #1 would 404 again anyway.
