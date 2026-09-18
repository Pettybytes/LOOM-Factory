# Gate 2 Live Wiring + Adapter Restart — Report

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Apify calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **Gate 2 clicks:** 0 · **Migrations:** 0
**Asset:** `DdZS1QRhrXE` sha256 **and** mtime unchanged

---

## 1. Adapter restart + health verification (done, with a defect caught by it)

**Restart path:** only the Discord-review adapter was touched, via its own supervisor
(`supervise.sh`, PID 15812 → `run.sh` → `adapter.py`). The adapter was terminated with `SIGTERM`
(rc=143), the supervisor restarted it with the new build.

| | PID | Notes |
|---|---|---|
| Before | 65579 | pre-change build, `/health` = `{"status":"ok"}` (no acquisition block) |
| After 1st restart | 44185 | served but `/health` failed — defect below |
| After fix | **45120** | `/health` returns the full acquisition block |

**Defect found by the restart (and fixed):** `_ACQ_STORE` was never declared at module level in
`adapter.py`, so live `/health` raised `NameError` inside the handler and the probe got an *empty
reply*. It compiled, imported, and passed tests — because my own wiring test assigned
`adapter._ACQ_STORE` in `setUp`, which silently created the missing global. Fixes:

* declared `_ACQ_STORE = None` at module level;
* `acquisition_health()` now **degrades instead of raising** (returns `route_active: false` +
  error class) so a lane fault can never take down the liveness probe;
* two regression tests added: `test_module_initialises_the_store_global_at_import` (would have
  caught this) and `test_health_degrades_instead_of_raising`.

**No unrelated service was touched:** `helper.py` on 8787 kept the same PID (83512) throughout.

**Health proof (live, from the running service):**

```json
{"status":"ok","acquisition":{
  "route":"AQ","route_active":true,"store":"durable",
  "gate1_approve":true,"gate2_acquire":true,
  "live_runner":"disabled","durable_store_flag":"1","apify_token_present":false,
  "bounds":{"max_bytes":104857600,"max_seconds":300,"max_results":1,
            "max_attempts":1,"fallback":false}}}
```

`route_active: true` with `store: durable` is the runtime evidence that the restart loaded the new
build **with the AQ route** and the durable store, without any interaction being clicked.

**Existing card intact** (read back after both restarts):

```
id      : 1550612384178249758
title   : ✅ ACQUISITION · APPROVED
buttons : Acquire for Reaction | Hold | Deny | View Source
footer  : Instagram · Authorized. Acquisition is a separate, explicit action. · dreemzy · …
```

---

## 2. Live local-Mac runner — what was wired

`infra/acquisition/live_runner.py` (384 lines) — the only component that may perform a paid
acquisition. Everything except the Apify HTTP transport and the local ffmpeg/disk work is injected.

**Flow per Acquire:** `preflight` → bounded downloader → `approved_runner.run_approved_job`
→ injected `orchestrate.dispatch` → provider → bounded local download → ffprobe validation
→ SHA-256 → atomic finalize → Reaction Library record → cost reconciliation.

* **One URL, one item** — the job's canonical URL only; query/fragment stripped; `resultsLimit=1`.
* **≤100 MiB / ≤5 min** — clamped down via `approved_lane.enforce_pilot_bounds`, never widened.
* **One attempt** — retry only with `retry_authorized`; a second retry is refused.
* **No fallback** — the pinned adapter (`apify/instagram-reel-scraper`) is selected by
  `lane.default_provider_actor`; the job's `actor` field (the authorizing human) never reaches the
  allowlist, and the inactive fallback cannot be selected.
* **Local validation is authoritative** — ffprobe must yield a video stream, a duration, audio
  present, and a codec, with size within the byte ceiling.
* **Provenance** — provider provenance plus the library record pass through `lane.safe_record`;
  `storage_url` stays empty (no hosted/signed URL).

**Assembly points:** `acquisition_ops.build_live_runner_from_env()` (fail-closed factory) →
`adapter._acquisition_live_runner()`; `adapter.acquisition_route()` (pure) routes
`AQ:<code>:<ref>` ids into `_handle_acquisition_button`.

---

## 3. Exact environment switch + fail-closed behaviour

`run.sh` now exports (additive, merge-only):

```bash
export LOOM_ACQUISITION_DURABLE_STORE="${LOOM_ACQUISITION_DURABLE_STORE:-1}"   # Gate 1 persistence ON
export LOOM_ACQUISITION_LIVE="${LOOM_ACQUISITION_LIVE:-0}"                    # paid path OFF
```

**`LOOM_ACQUISITION_LIVE` is the single switch for the paid path, and it is OFF.** It also enables
nothing by itself: with the switch on but any prerequisite missing, the factory returns *no runner*
and an Acquire click then authorizes nothing and spends nothing.

**Note for the live proof:** the **Apify token is deliberately not in the adapter's environment** —
`run.sh` sources a fixed variable whitelist that excludes `LOOM_APIFY_API_TOKEN`, which is why
`apify_token_present: false`. So even `LOOM_ACQUISITION_LIVE=1` alone cannot spend; enabling the
live path is a two-part, deliberate act (add the token to the whitelist **and** set the switch).

**Fail-closed conditions, all returning a classified refusal with no provider call:**

| Stage | Codes |
|---|---|
| Gate/authorization | `NO_JOB`, `NOT_SPEND_AUTHORIZED`, `NO_PENDING_JOB`, `GATE1_MISMATCH`, `NOT_RUNTIME_PROVEN` |
| Target changed | `URL_CHANGED`, `CONTENT_ID_CHANGED`, `AMBIGUOUS_COLLECTION` |
| Already done / in progress | `ALREADY_ACQUIRED`, `IN_FLIGHT`, `ALREADY_DISPATCHED`, `ALREADY_FINISHED`, `ATTEMPTS_EXHAUSTED` |
| Actor | `UNKNOWN_ACTOR`, `INACTIVE_ACTOR` (fallback never used) |
| Provider/config | `TOKEN_MISSING`, `BREAKER_DISABLED`, `CEILING_REACHED`, `MAX_ACQUISITIONS_REACHED`, `NO_BUDGET`, `NO_ESTIMATE` |
| Media validation | `SIZE_LIMIT`, `DURATION_LIMIT`, `MEDIA_INVALID`, `AUDIO_MISSING`, `CODEC_MISMATCH`, `NO_DURATION`, `BAD_HASH`, `BYTE_LIMIT`, `EMPTY_DOWNLOAD`, `DOWNLOAD_FAILED`, `FFPROBE_*` |
| Provider output | `EMPTY_RESULT`, `MULTIPLE_RESULTS`, `CONTENT_MISMATCH`, `MISSING_VIDEO_URL`, `HOST_NOT_ALLOWED`, `CREDENTIALS` |

A refusal never pauses the card: the operator sees the classified FAILED card, and a retry is an
explicit, separately authorized click.

---

## 4. Tests (the eight required proofs)

```
python3 -m unittest discover -s infra/acquisition/tests     → 223 passed  (was 196, +27)
python3 -m unittest discover -s infra/discord-review/tests  → 120 passed  (was 105, +15)
```

| Required proof | Covering tests |
|---|---|
| 1. Restarting activates AQ routing | `test_adapter_imports_the_acquisition_lane`, `test_aq_custom_ids_route_to_the_acquisition_lane`, `test_other_custom_ids_are_untouched_by_the_new_route`, `test_health_surface_reports_the_lane` + live `/health` |
| 2. Approve can never reach the live runner | `test_approve_cannot_reach_the_live_runner`, `test_approve_creates_exactly_one_pending_job_and_never_spends` |
| 3. Acquire is the only initial spend trigger | `test_only_spend_actions_cost_money`, `test_acquire_requires_a_separate_spend_authorization_job`, `test_gate1_only_job_is_refused` |
| 4. Retry explicit and single-shot | `test_one_attempt_only`, `test_retry_is_explicit_and_single_shot`, `test_retry_requires_explicit_authorization`, `test_retry_is_allowed_once` |
| 5. Duplicate Acquire cannot double-call the provider | `test_duplicate_acquire_produces_exactly_one_provider_call`, `test_duplicate_click_suppressed`, `test_in_flight_is_refused`, `test_already_dispatched_is_refused` |
| 6. Already-acquired content cannot be acquired again | `test_already_acquired_is_refused`, `test_repeat_acquisition_of_a_library_item_is_blocked`, `test_library_match_by_content_id` |
| 7. Provider failure → classified FAILED card | `test_zero_results_fails_without_fallback`, `test_failed_run_shows_the_failed_card`, `test_media_validation` (6), `test_missing_token_is_refused`, `test_ceiling_exceeded_is_refused` |
| 8. Secrets/signed URLs never leak | `test_no_signed_url_or_secret_in_the_outcome_or_library_record`, `test_persisted_record_has_only_schema_fields`, `test_provenance_carries_no_signed_url`, `test_no_credential_reaches_a_payload`, `test_signed_url_never_survives_render` |

Plus the fail-closed matrix: 16 refusal tests, all asserting `transport.calls == []` and
`downloads == []` — i.e. **a refusal provably performs no provider call and no download.**

---

## 5. What is still unproven

1. **No real acquisition has ever run.** The live switch is off, the Apify token is absent from the
   adapter environment, and no Gate 2 click has occurred. The provider path has only ever run
   against mocks.
2. **`build_live_io` is not exercised.** The real Supabase-backed IO bundle (persist library row,
   record provider run, update ceiling, read pending job) is wired but untested against live
   Supabase — it is only reached when the live path is enabled.
3. **Provider-run / ceiling ledger writes are not wired into the dispatch return path yet.** Cost
   reservation and reconciliation run in `orchestrate`/`cost`, and `LiveIO` carries
   `record_run`/`update_ceiling`, but nothing writes those rows yet — that must land before the live
   proof so the breaker stays truthful.
4. **The adapter's AQ route has never served a real Discord interaction** (the Gate 1 proof drove
   the handler directly).
5. `ACQUIRING`/`ACQUIRED` transitions through the live service remain unobserved.

---

## 6. What remains for the single live Gate 2 proof

1. Persist provider-run + ceiling rows on completion (item 3 above) so spend is ledgered.
2. Add `LOOM_APIFY_API_TOKEN` to `run.sh`'s whitelist and set `LOOM_ACQUISITION_LIVE=1`.
3. Restart the adapter; confirm `/health` shows `live_runner: enabled`, `apify_token_present: true`.
4. Click **Acquire for Reaction** on the existing approved card
   (`1550612384178249758`) — expected ≈ **$0.0026**, ceiling `$1.00` / 9 acquisitions remaining.
5. Verify: card → ACQUIRED, one `loom_provider_runs` row, ceiling decremented by the actual,
   one `loom_reaction_library` row, provenance free of signed URLs, asset hashed and ffprobe-valid.
   On failure: classified FAILED card, no retry, breaker untouched.

**Change set:** `adapter.py` +88/-1, `run.sh` +6 (both tracked, uncommitted);
19 new untracked paths including `live_runner.py` (384), `test_live_runner.py` (351),
`test_adapter_acquisition_wiring.py` (108). Nothing staged, committed, or pushed.
