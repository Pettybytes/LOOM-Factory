# Acquisition Boundary V1 — Plan, Capability Matrix, and Storage Proposal

> Status: **deterministic contracts built + tested (no download)**.
> Next gate: Bryan review + authorization for ONE bounded, non-production
> acquisition proof. No real media download has been performed.

---

## 1. Purpose

The Acquisition Boundary is the ONLY path from a saved/reviewed source to an
acquired asset. It converts an explicit authorization into an **idempotent
acquisition job** — it does not itself download, analyze, publish, or
distribute. Those are separate, separately-authorized steps.

---

## 2. Corrected semantics (2026-09-17)

Bryan's normal reason for submitting a source is to **acquire it for reaction**.
A link-only bookmark is occasionally useful but must not be the primary outcome.

| Action | Consequence |
|---|---|
| **Acquire for Reaction** (primary) | record explicit authorization → create idempotent acquisition job immediately → allow Unassigned → route future asset to Personal Reaction Library → download only when adapter is runtime-proven → **never** authorizes publishing/distribution |
| **Save Link Only** (secondary) | persist candidate metadata only; no acquisition job |
| **Cancel** (third) | persist nothing; no submission, no job |

No ambiguous "Submit Source" label. Existing link-only submissions are NOT
retroactively interpreted as acquisition authorization.

---

## 3. Platform capability matrix (acquisition readiness — honest)

Acquisition readiness is separate from URL intake. A platform whose URL intake
works is **not** automatically acquirable.

| Platform | Capability | Reason |
|---|---|---|
| YouTube | `implemented_not_proven` | yt-dlp integrated (transcript-only proven, 0 media bytes); bounded media acquisition proof not yet run |
| Reddit | `blocked_pending_adapter` | host-diverse media (v.redd.it / external); per-host resolver not yet implemented |
| Twitch | `blocked_pending_adapter` | clip + bounded VOD adapter not yet implemented |
| Kick | `blocked_pending_adapter` | clip + bounded VOD adapter not yet implemented |
| Instagram | `blocked_pending_adapter` | Reels download requires undocumented/authenticated endpoints |
| TikTok | `blocked_pending_adapter` | held pending explicit authorization; no first-party adapter |

**Capability states:** `runtime_proven` · `implemented_not_proven` ·
`blocked_pending_adapter` · `unsupported_with_reason`.

**Rule:** only a `runtime_proven` adapter may download. Every other platform
yields an acquisition job with status `blocked_pending_platform_adapter`
(authorization recorded; auto-eligible when the adapter is later enabled — the
source need not be rediscovered or re-approved).

---

## 4. Acquisition job contract (`infra/acquisition/acquisition.py`)

A durable, idempotent job retains: canonical source URL + fingerprint, platform
+ platform content id, authorization type (`acquire_for_reaction` |
`approved_for_acquisition`), actor + timestamp, destination (tenant id,
unassigned `''`, or `personal_reaction_library`), requested timestamp range,
acquisition mode (`full_item` | `bounded_segment`), status + reason, retry
count, byte/time ceilings, resulting asset provenance, rights/usage note, and
audit + correlation ids.

**Statuses:** `queued` · `running` · `acquired` ·
`blocked_pending_platform_adapter` · `failed`.

**Idempotency:** `dedup_key = sha256(fingerprint + authorization_type + mode +
requested range)` (unique in DB) — double-click / redelivery cannot duplicate.

**Bounded media:** prefer a timestamp window; pre-roll/post-roll handles;
duration/byte ceilings before durable storage; **reject playlists / feeds /
profiles / channels / ambiguous collections** (no single-item content id);
never acquire a whole stream when a bounded moment is requested; clean temp
partials on failure; persist final assets only after validation.

---

## 5. Personal Reaction Library contract (`infra/acquisition/reaction_library.py`)

Tenant-neutral store for acquired source assets.

- **Dedup:** by content hash (sha256) — identical bytes stored once.
- **Filename:** deterministic, disambiguating (`<platform>__<content_id>__<hash12>.<ext>`).
- **Retention:** bounded (`retention_days`, default 30); expires unless consumed.
- **Provenance:** immutable (fingerprint, platform, content id, canonical URL, job id).
- **Cleanup:** temp partials removed on failure; expired assets purged by sweep.

---

## 6. Storage destination (PROPOSAL — requires Bryan review)

- **Files** → GHL Media Storage (Bryan's primary location, a dedicated
  `loom-reaction-library` folder / filesafe CDN). Reuses the approved canonical
  media store; **no new cloud vendor**.
- **Metadata + provenance** → Supabase `loom_reaction_library` (ledger; Supabase
  does not replace GHL media storage).

No acquisition happens until Bryan confirms this destination and the adapter
limits + proof target.

---

## 7. Security invariants

- Reuse URL validation + SSRF/credential/private-host/port/scheme rejection.
- No cookie theft / browser-session extraction; no DRM/paywall/auth bypass.
- No arbitrary shell args derived from URLs; strict subprocess timeouts +
  output-path confinement.
- No secrets in Discord, logs, DB, or repo. Rights/usage note + provenance on
  every asset.

---

## 8. Stop gate

No real media download has been performed. The next step requires Bryan's
explicit authorization for **one bounded, non-production acquisition proof**
(platform, adapter limits, storage destination, and proof target to be
confirmed). Publishing/distribution and the Chrome extension remain NOT started.
