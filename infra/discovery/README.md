# LOOM — Automated Source Discovery V1

Tenant-driven, deterministic discovery of eligible source moments — with no
automatic download, processing, scoring, or publication.

## What this is

A shared foundation (one contract + one workflow, not per-tenant clones) that
turns a **Source Brief** ("what a tenant wants to find") into a **normalized
discovery record** ("an eligible, traceable source"), then stops at the
**manual-ingest boundary**. Discovery never authorizes publication.

## Modules

| Module | Responsibility |
|---|---|
| `source_brief.py` | Versioned, tenant-scoped Source Brief contract + validation |
| `discovery_record.py` | Normalized discovery record schema + validation (reserves Topic Intelligence fields, computes none) |
| `identity.py` | Canonical URL normalization, content-ID extraction, tenant/global fingerprints, duplicate classification (no paid model) |
| `connector.py` | Shared connector interface (pagination, rate limits, auth-by-name, provenance, retry, health, cost, versioning) |
| `qualification.py` | Qualification-before-acquisition gate (deterministic, explicit rejection reasons) |
| `store.py` | Durable store (SQLite, dependency-free) with idempotent upsert + metric-history preservation |
| `connectors/youtube.py` | YouTube/search + competitor-channel discovery (first-party RSS, no credential) |
| `prove_youtube_connector.py` | Bounded live connector proof (real data; stops before download) |

## Pipeline

```
Source Brief ─► Connector ─► Normalized Record ─► Identity/Dedup
                                                    │
                         Qualification gate ◄───────┘
                                │
                       manual-ingest boundary (STOP)
```

## Non-production fixtures

`fixtures/` holds three **example** Source Briefs (design/test fixtures only):

- `brief_bryan_face_reaction.json` — Bryan face/reaction profile
- `brief_pets_ranking.json` — pets ranking/commentary profile
- `brief_wild_moments.json` — wild-moments/story commentary profile

These are **not** provisioned tenants. Do NOT create GHL subaccounts, social
accounts, Discord tenant spaces, or tenant rows for them.

## Tests

```sh
python3 -m unittest discover -s infra/discovery/tests -v
```

## Live connector proof

```sh
python3 infra/discovery/prove_youtube_connector.py
```

Proves: real YouTube candidates discovered via first-party RSS → normalized →
stored (idempotent) → duplicate rejected explicitly → at least one qualified →
passed to manual-ingest boundary → **STOP** (no download, no clip, no publish).

## Boundaries (enforced)

- No paid model calls (dedup is pure SHA-256 + text normalization).
- No automatic download / OpenShorts / clip / publish / distribution.
- No Topic Intelligence scoring in this segment (fields reserved only).
- Reddit/TikTok planned but NOT implemented; X deferred.
- Keyword search (`youtube/search`) requires `YOUTUBE_DATA_API_V3_KEY`
  (referenced by name only) — absent, so search fails deterministically.
  Channel discovery uses first-party RSS and needs no credential.
- No credentials or secret values in code, logs, or commits.
