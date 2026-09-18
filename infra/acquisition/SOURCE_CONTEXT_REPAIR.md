# Source Context Root-Cause Fix + Gate 1 Row Repair (Report)

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Migrations:** 0 · **Provider calls:** 0 · **Downloads:** 0 · **Spend:** $0 · **Gate 2 rows created:** 0
**Acquire invoked:** no

---

## 1. Corrected root cause

**Before:** the card's buttons carried only a content id, and the lane context was built from
*existing job rows* — so for a fresh REVIEW card there were no rows, `canonical_url` came out empty,
and that empty value was persisted into the Gate 1 row. Nothing durable anchored the source.

**Now:** the context is re-derived at click time from a **durable, authoritative record**, through an
existing contract — **`loom_manual_submissions` (migration 027)** — with `loom_acquisition_jobs` as a
secondary source. No migration was needed.

What the durable context carries: `platform`, canonical `source_url`, `platform_content_id`,
`duplicate_fingerprint`, `tenant_id`/`profile_id`, `status`, `submitter`, plus a card reference token
(`card_ref=ch:<channel>/msg:<message>`) in `reviewer_note`. The card identity that reaches the handler
is the **verified Discord interaction** (channel + message id) plus the content id in the custom id —
never an unsigned, client-supplied URL, never in-memory state, never text scraped from the embed.

Resolution order and failure mode (`source_context.resolve_context`):

1. `loom_manual_submissions` by `platform_content_id` (authoritative)
2. `loom_acquisition_jobs` by `platform_content_id`
3. nothing durable → **`NO_DURABLE_CONTEXT` / empty context — fail closed, never guess**

Wiring: `acquisition_ops.context_for()` now fills `canonical_url`/`platform` from
`store.source_context(ref)` (implemented by both the memory and Supabase stores), so the fix applies to
new cards automatically and is independent of process state.

## 2. Card/context identity

| | Value |
|---|---|
| Canonical content | `DdQE_RUS0GJ` |
| Canonical URL | `https://www.instagram.com/reel/DdQE_RUS0GJ/` |
| Durable origin | `loom_manual_submissions` row `e878a1dd-298d-45b0-88f4-7347a6961964` (fingerprint `baf2f7a4…`), submitted 2026-09-17T20:51:32Z |

**Validation runs before anything is persisted** (`source_context.normalize_instagram_url`):

* `/reels/<id>/`, `/p/<id>/`, `/reel/<id>/` → **all normalize to the single canonical form** (proved)
* query string and fragment dropped; the stored value had no trailing slash and is normalized on read
* rejected: non-https, foreign hosts, credentials in the URL, signed/credential params (`sig`, `oh`,
  `token`, `signature`, `x-amz-*`, `access_token`, `cookie`, `authorization`), non-reel paths
  (`/stories/`, bare id), malformed ids, and any **content-id mismatch/drift**
* an invalid or drifted *stored* value is refused rather than used, and a lookup failure yields **no
  context** instead of a fabricated one

## 3. Exactly one row repaired

```
row        40560336-bbe9-468e-9230-5fa880c9eef1     (Gate 1, approved_for_acquisition)
rows_updated  1

before  canonical_url: ""
after   canonical_url: "https://www.instagram.com/reel/DdQE_RUS0GJ/"

only_canonical_url_changed : true
created_at_unchanged       : true
unchanged: id, dedup_key (a3935fe9…), authorization_type, actor (passportpetty#688976899094872148),
           retry_count (0), max_bytes (104857600), max_duration_seconds (300), status (queued),
           correlation_id (""), authorized_at
```

**Historical row `d3b4bba9…` was not touched** — it still reads `acquire_for_reaction` / `failed` /
`chrome_network_capture`, dedup `7f9ddc86…`, retry 0.

**No other row was written.** The durable context row already existed from yesterday's intake; it was
read, not created or modified.

## 4. Existing Discord card updated in place

```
message_id  1550616797865181315      (same message — edited, not reposted)
link        https://discord.com/channels/1546266463663423518/1550207989569421444/1550616797865181315
title       ✅ ACQUISITION · APPROVED   (unchanged)
buttons     Acquire for Reaction | Hold | Deny | View Source
View Source url = https://www.instagram.com/reel/DdQE_RUS0GJ/     ← restored
edited      2026-09-18T21:24:37.449Z
```

## 5. Restart safety

Only the discord-review adapter was restarted (via its supervisor): pid `52412` → **`57818`**;
`helper.py` (8787) untouched. Health after restart: `route: AQ, route_active: true, store: durable,
live_runner: enabled, apify_token_present: true`.

**The same card resolved the same context after the restart**, purely from durable rows:

```json
{"durable_context": {"platform":"instagram",
                     "canonical_url":"https://www.instagram.com/reel/DdQE_RUS0GJ/",
                     "content_id":"DdQE_RUS0GJ", "fingerprint":"baf2f7a4…",
                     "origin":"loom_manual_submissions"},
 "lane_context": {"state":"approved",
                  "canonical_url":"https://www.instagram.com/reel/DdQE_RUS0GJ/",
                  "platform":"instagram","content_id":"DdQE_RUS0GJ"}}
```

Gate 2 planning reads **the repaired canonical URL** and the planned job is new and isolated:

| Check | Value |
|---|---|
| Planned type / status | `acquire_for_reaction` / `queued` |
| Planned dedup_key | `d84885c4…` ≠ historical `7f9ddc86…` (cannot upsert over it) |
| Correlation | `correlation_id = a3935fe9…` → the **new Gate 1 row only** |
| Inherited attempts | **none** — `retry_count = 0`; a 2nd attempt needs explicit authorization |
| Actor | allowlisted `apify/instagram-reel-scraper` — never `chrome_network_capture` |
| Sources consulted | `pending_job(DdQE_RUS0GJ)`, `library_rows`, `existing_run`, `ceiling` — **no reference to the historical id or dedup key** |

## 6. Preflight proof — no transport invoked

```json
{"preflight_ok": true,
 "actor": "apify/instagram-reel-scraper",
 "canonical_url": "https://www.instagram.com/reel/DdQE_RUS0GJ/",
 "reserved_micro": 2600,
 "provider_not_called": true}
```

`NO_URL` is gone. No refusal remains **except** the deliberate spend-time gates that only a real click
reaches. Nothing was created or mutated:

| Metric | Value |
|---|---|
| Gate 2 rows for target | 1 — the historical failed row only (unchanged) |
| Jobs total | 6 (unchanged by this task) |
| Provider runs | 1 total, **0 for target** |
| Cost reserved | $0.0000 · **ledger never touched** (planning path raises if it is) |
| Ceiling | 2,600 micro / 1 success / max 10 — unchanged |
| Library rows | 1 (target absent), library dir 1 file — no download |
| Asset sha256 | `9b22f887b2ff…ae096` — unchanged |

## 7. Regression tests

**264 acquisition + 120 discord-review = 384 passing** (22 new in `test_source_context.py`).

| Required item | Covered by |
|---|---|
| 1. Card references a durable authoritative context, not in-memory / not client URL | `test_context_is_tied_to_the_card_content_id`, `test_custom_id_carries_only_action_and_content_id` |
| 2. Context resolves after restart | `test_resolution_survives_a_restart` + live post-restart resolve (§5) |
| 3. `/reels/` + `/p/` → one canonical form, expected id validated | `test_reels_and_p_forms_normalize_to_one_canonical_form`, `test_content_id_drift_is_rejected` |
| 4. Reject invalid/drift/signed/credentialed/foreign before persistence | `test_signed_or_credentialed_urls_are_rejected`, `test_foreign_hosts_and_non_reel_paths_are_rejected`, `test_payload_is_built_only_from_a_valid_canonical_url` |
| 5. Context tied to the card's canonical content id | `test_context_is_tied_to_the_card_content_id`, `test_unknown_content_id_resolves_to_nothing` |
| 6. Repaired URL is what Gate 2 planning reads | `test_planning_uses_the_repaired_canonical_url_and_preflight_passes`, `test_an_empty_canonical_url_still_fails_closed` |
| 7. Historical row untouched | `test_planning_never_consults_the_historical_row`, `test_planned_job_is_new_and_isolated_from_the_historical_failure`, `test_a_repaired_row_keeps_its_authorization_identity` |
| 8. No provider/transport/download/reservation/run/ceiling/library writes | `test_planning_makes_no_provider_download_ledger_or_write_call` (assertion-guarded fakes), §6 metrics |
| 9. Exactly one Gate 1 row; repeat Approve idempotent | `test_repeat_approve_recomputes_an_identical_dedup_key` + `loom_acquisition_jobs_dedup unique (dedup_key)` |

---

## Still unproven / next step

* No acquisition has run: no provider call, no download, no reservation, no Gate 2 row. Concurrency and
  the ledger remain proven only against fakes.
* The adapter is currently running with `LOOM_ACQUISITION_LIVE=1` and `live_runner: enabled`, so a real
  **Acquire for Reaction** click would spend (~$0.0026, ceiling `$1.00`, 9 acquisitions left). That click
  is the next authorized step and was **not** taken here.
