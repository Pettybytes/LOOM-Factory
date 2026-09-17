# Apify Paid-Provider Acquisition Boundary — Platform-Neutral Design

> Phase 1 artifact. **Design + plan only. $0 spent, no Actor call, no token use.**
> STOP for Bryan's approval before Phase 2 (any code/token/migration).

## Purpose

Add a **paid-provider acquisition lane** alongside the existing local/free
adapter ladder, **platform-neutral** by contract, with **Instagram as the first
implementation** (its local adapter failed the agreed proof threshold).

The provider must not be hard-coded to Instagram: the same contracts, cost
ledger, and circuit breaker serve TikTok, Reddit, X, YouTube, Twitch, and Kick.
Only Instagram is implemented + runtime-proven first; every other platform's paid
Actor requires a separate evaluation + authorization before activation.

## Core platform policy (applies to every platform)

1. Prefer a runtime-proven free/local adapter.
2. Use a paid provider only when the local path is unsupported or crossed a
   documented failure threshold.
3. **Never** invoke Apify during discovery, qualification, scoring, Source Review
   rendering, Hold, Reject, or Save Link Only.
4. A paid acquisition requires an existing authorization for the **exact**
   canonical source URL.
5. Never silently substitute a different Actor/provider.
6. Record provider, Actor identity, version/build, actual cost, and acquisition
   job for every acquired asset.

## Two intake lanes (unchanged contracts, now provider-aware)

- **Automated tenant sourcing** — discovery collects metadata/lightweight
  evidence only; qualified candidates appear in `#source-review`; only Bryan's
  `Approve for Acquisition` authorizes a media download. Hold/Reject never
  download.
- **Manual operator sourcing** — `/submit-source` or the Chrome extension;
  `Save Link Only` stores without downloading; `Acquire for Reaction` authorizes
  that exact source. Unassigned approved assets → Personal Reaction Library.

The provider boundary is reached **only** after a candidate is
`approved_for_acquisition` / `acquire_for_reaction` — never upstream.

## Shared provider contracts (provider-neutral)

Deterministic, testable, no network at contract level:

| Contract | Responsibility |
|---|---|
| `authorization` | verify an existing acquisition authorization for the exact canonical URL before dispatch |
| `platform+canonical` | reuse `manual_intake.validate_and_canonicalize`; platform must match the Actor's allowlist |
| `actor_allowlist` | per-platform → ordered Actor allowlist (ID + developer + build pin); reject unknown Actor |
| `one_item` | reject playlists/feeds/profiles/channels/ambiguous collections; exactly one content id |
| `idempotency` | `dedup_key` (reuse acquisition contract); exactly-once dispatch per authorization |
| `run_correlation` | map provider `run_id` + Actor `build` → acquisition job id (immutable) |
| `cost_ceiling` | per-run estimate + lifetime ceiling; reserve worst-case before running |
| `output_schema` | strict validation of provider output (required fields, media type, file ref) |
| `file_retrieval` | fetch within byte/duration/time ceilings; validated redirects; https + approved host |
| `local_validation` | ffprobe + SHA-256 before finalize (reuse `acquire.parse_ffprobe_result` / `checksum_sha256`) |
| `library_finalize` | reuse Reaction Library contract (`reaction_library.plan_library_entry`) |
| `remote_delete` | delete the provider's stored copy in `finally` (never retain remotely) |
| `audit+cost_ledger` | immutable provider-run row: estimate, actual, Actor, build, run_id, status |
| `classified_failures` | typed error codes (provider/auth/not_found/one_item_violation/cost/schema/retrieval/validation) |
| `circuit_breaker` | auto-disable dispatch when lifetime ceiling or max-acquisitions is reached |

## Pilot cost controls (hard, enforced in code + persisted)

- **$1.00 lifetime ceiling** across all Apify charges (binding constraint).
- **Max 3 successful pilot acquisitions** (resolves the $1-vs-10 conflict —
  see Actor evaluation item 13; 3 × $0.29 = $0.87 < $1.00).
- Max **one** canonical URL and **one** media result per run.
- **No** paid automatic retries.
- Auto-disable dispatch when either ceiling is reached.
- Record **estimated** cost before dispatch and **actual** cost after completion.
- If exact price is unknown in advance, **reserve the worst-case permitted
  amount** before running.

## Secret handling

- Env var: `LOOM_APIFY_API_TOKEN` (already present, non-empty, in `~/.openclaw/.env`).
- Never in chat / Discord / extension / Supabase / git / shell history.
- Phase 2 adds a **non-echoing** setup + presence/format verification only
  (never prints the value). Phase 1 does **not** read or use the value.

## Required migration / schema changes (identified, NOT applied)

| # | Change | Purpose |
|---|---|---|
| 034 | `loom_provider_runs` (provider, actor_id, actor_build, run_id, acquisition_job_id FK, cost_estimate_cents, cost_actual_cents, status, output_ref, created_at) | provider run ↔ job correlation + cost ledger |
| 035 | `loom_provider_ceiling` (single row: ceiling_cents, lifetime_spent_cents, max_acquisitions, acquisitions_succeeded, disabled, updated_at) | circuit breaker state |

Plus (code only, no DDL): the existing `loom_acquisition_jobs.adapter` column
(033) gains provider-qualified values, e.g. `apify:agentx~instagram-reels-downloader`.

## Deterministic mock-test plan (Phase 2, before any real call)

A `MockProvider` (no network, no token) drives every contract. Planned tests:

1. **Authorization required** — dispatch without a matching authorization → rejected, no call.
2. **Exact-URL match** — authorization for a different URL → rejected.
3. **One-item** — playlist/feed/profile URL → `ambiguous_collection` rejected.
4. **Actor allowlist** — unknown Actor/provider → `unknown_actor` rejected; no silent substitution.
5. **Idempotency** — same dedup_key → exactly-once dispatch (second call no-op/duplicate).
6. **Run correlation** — provider run_id + build recorded on the job; immutable.
7. **Cost estimate + reserve** — estimate before dispatch; worst-case reserved when unknown.
8. **Ceiling reached** — lifetime spent or max-acquisitions hit → circuit breaker returns `disabled`, no call.
9. **Output schema** — malformed/missing media field → `bad_output` rejected.
10. **Byte/duration/time ceilings** — oversized/too-long output → `file_limit` rejected.
11. **Local validation** — ffprobe/hash failure → `validation_failed`; no library row.
12. **Remote delete** — provider copy deleted in `finally` even on failure.
13. **Library finalize** — valid file → exactly one Reaction Library entry (is_non_production for the pilot).
14. **No secret in logs** — token never in logs/payloads (presence/format only).

## Implementation sequence

- **Phase 1 (this commit):** close out local Instagram; Actor evaluation;
  provider-neutral design; migrations + mock plan. **$0. STOP for approval.**
- **Phase 2 (after approval):** add token securely (non-echoing); build
  deterministic provider adapter + mocks; cost/circuit persistence; run tests.
  **Stop before any real Actor call.**
- **Phase 3 (separately authorized):** ONE non-production proof on
  `https://www.instagram.com/reel/DdZS1QRhrXE/` — one URL, one result, Personal
  Reaction Library, local validation, delete Apify copy, report exact cost.

## STOP before (hard gates)

Storing/using the token · any Apify Actor call or spend · activating any other
platform's paid adapter · OpenShorts · OBS automation · GHL upload · publishing
or distribution.
