# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-16 01:15 EDT (America/Detroit)

## Segment 2 — Automated Source Discovery V1: SEGMENT CLOSED

- **Segment 2 — Foundation: COMPLETE.**
- **Segment 3 — first YouTube connector milestone: RUNTIME-PROVEN
  (Supabase-backed).**

Zero cost. Zero downstream side effects. No source was downloaded, processed,
clipped, published, or distributed.

## Segment boundary (resolved)

- **Segment 2 — Foundation (COMPLETE):** Source Brief contract; normalized
  discovery schema; identity + duplicate handling (deterministic tests);
  shared connector interface; qualification-before-acquisition gate; three
  profile fixtures validate.
- **Segment 3 — Initial Connector Proofs (first milestone RUNTIME-PROVEN):**
  real YouTube source discovered → normalized → deduplicated → evidence stored
  → qualification applied → one duplicate rejected → one eligible source passed
  to manual-ingest boundary → no automatic publication.

Reddit/TikTok remain planned (not started). X deferred. **HOLD Reddit until
explicitly authorized.**

## What was built

```
infra/discovery/
  source_brief.py           # Source Brief contract + is_non_production() marker convention
  discovery_record.py       # normalized discovery record + validation (carries profile_id)
  identity.py               # canonical URL norm + fingerprints + dedup (no paid model)
  connector.py              # shared connector interface
  qualification.py          # qualification-before-acquisition gate
  store.py                  # SQLite durable store (idempotent upsert, append-only metric history)
  store_supabase.py         # production Supabase store (service-role key by NAME only)
  connectors/youtube.py     # YouTube/search + channel discovery (first-party RSS)
  prove_youtube_connector.py
  prove_supabase_acceptance.py
  fixtures/                 # 3 non-provisioned profile briefs
  tests/test_discovery.py   # 34 deterministic tests
n8n-workflows/LOOM-06-Source-Discovery.json   # shared, inactive
infra/supabase/018_loom_source_briefs.sql      # APPLIED
infra/supabase/019_loom_discovered_sources.sql # APPLIED
infra/supabase/020_loom_discovered_sources_profile.sql # APPLIED (15 rows backfilled)
```

## Supabase acceptance proof (12/12 PASS, production Supabase)

Real YouTube data (Linus Tech Tips): 15 candidates discovered via first-party
RSS → normalized + stored (15/15) → replay idempotent (15, not 30) →
metric-history append (1→2, prior preserved) → explicit repost rejection →
manual-ingest boundary only → no forbidden modules imported / RSS-GET-only.
Script: `infra/discovery/prove_supabase_acceptance.py`.

## Production-queue exclusion (acceptance/test profiles)

`profile_id` travels on every discovered record (from its Source Brief) and is
persisted. `is_non_production()` marks acceptance/test/example profiles. Normal
production queue queries use `production_sources()` / `production_fingerprints()`
/ `production_global_fingerprints()`, which exclude non-production profiles by
default. Profile `supabase-acceptance-proof` is non-production acceptance
evidence and is KEPT (not deleted).

## Tests

`python3 -m unittest discover -s infra/discovery/tests` → **34 passed**.

## Git

- LOOM-Factory `master` HEAD `4cedf07`, clean, **pushed** to origin.
- Chain: `13a8dc6` → `0e23c54` → `4b1da3e` → `8f03e34` → `5ab5283` →
  `d10f002` → `205f1bf` → `708d472` → `4cedf07`.
- No secrets committed; no destructive ops; nested OpenShorts dirty files untouched.

## Costs

$0.00. No paid model calls, no new paid service. `caffeinate` active; Docker left running.

## Credentials required (names only)

- `YOUTUBE_DATA_API_V3_KEY` — for keyword search (absent; channel RSS works without it).

## Manual actions required (Bryan)

- (Optional) Provide `YOUTUBE_DATA_API_V3_KEY` for keyword search.
- Migrations 018–020 are all applied; no further SQL required.

## Next

**HOLD.** Reddit + TikTok start only on explicit authorization. No automatic
download/processing/publication at any point.
