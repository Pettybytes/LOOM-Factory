# LOOM — Source of Truth

**Updated:** September 9, 2026
**Owner:** Bryan
**Engineering executor:** Bentley
**Current master blueprint:** `LOOM_FACTORY_MASTER_BLUEPRINT_REVISION_3_1.md`

**This is LOOM’s canonical operational index and decision file.**

If an older roadmap, prompt, handoff, model response, or workflow description conflicts with this document or `LOOM_FACTORY_MASTER_BLUEPRINT_REVISION_3_1.md`, the newer documents control unless Bryan explicitly approves a later amendment.

If these two current documents conflict with each other, stop and ask Bryan before proceeding.

## Required Reading Order

Every new Bentley, Claude, Gemini, or engineering session must read:

1. `LOOM_SOURCE_OF_TRUTH.md`
2. `LOOM_FACTORY_MASTER_BLUEPRINT_REVISION_3_1.md`
3. `HANDOFF.md`
4. `TOOLS.md`

`LOOM_SOURCE_OF_TRUTH.md` defines current decisions and position.

`LOOM_FACTORY_MASTER_BLUEPRINT_REVISION_3_1.md` defines the complete five-stage, 34-segment roadmap.

`HANDOFF.md` defines the latest completed work, current blockers, and exact next action.

`TOOLS.md` defines tool locations, endpoints, and credential references. Secret values must never be copied into this Source of Truth, the master blueprint, handoffs, prompts, reports, logs, screenshots, or workflow JSON.

---

# Evidence Discipline

Every status claim must use one of these labels:

* **Runtime verified** — observed through an actual execution, API response, browser test, or runtime health test.
* **Static verified** — confirmed by inspecting workflow JSON, configuration, or source code without executing the complete path.
* **Bryan reported** — personally observed or confirmed by Bryan.
* **Bentley reported** — reported by Bentley but not independently/runtime verified.
* **Planned** — accepted scope that has not been constructed.
* **Deferred** — intentionally excluded until its entry condition is met.

Construction is not runtime proof.

“Built,” “wired,” “implemented,” or “configured” must not be converted into “complete” unless the required evidence exists.

---

# Current Position

**Current stage:** Stage 1 — Factory Spine
**Current segment:** Segment 4 — Discord QC (COMPLETE, runtime verified)
**Current subsegment:** Segment 4D — Durable Decision Record (verified)
**Next segment:** Segment 5A — GHL Media Storage
**Runtime execution approval:** Granted for the single controlled Segment 3 acceptance + Segment 4 Discord QC (completed)

## Current immediate objective

Segment 4 is complete: the native Discord review gate is runtime-verified
end-to-end (bot card → signed button click → durable Hold decision → card
update + disabled controls). Next is Segment 5A — GHL Media Storage —
beginning with GHL authentication and the media-upload plane, only after the
(now-proven) decision gate.

## Current immediate objective

All three remaining Segment 3E static blockers are now resolved. The next step is Bryan and the supervising architecture review approving the static state, then one controlled Segment 3 acceptance test.

Resolved this round:

1. Callback-signing secret storage: n8n Cloud's Variables/`$env` feature is not licensed on this account (`GET /api/v1/variables` → HTTP 403, `feat:variables`). Resolved via a $0 local Caddy relay instead — see "Security" and "FACTORY-CALLBACK-001" below.
2. Each `job_id` is now locked to its first authenticated terminal callback; a later duplicate or conflicting event is rejected before candidate processing.
3. `job.completed.clips[]` was compared against installed OpenShorts source and found to be missing `start`/`end`, `viral_hook_text`, and `predicted_score` — the bounded post-callback GET chain is retained for this reason.

Continuous status polling has been removed and must not return.

## Current local runtime state

* Docker Desktop: **active — Bryan reported**
* OpenShorts containers: **active — Bryan reported**
* OpenShorts dashboard/backend: **active and confirmed by Bryan**
* n8n Cloud: **available**
* Workflows 00–03: **inactive — runtime verified through n8n API by Bentley**
* No VOD submission has been authorized.
* No Workflow 03 runtime execution has been authorized.

---

# Locked Architecture

```text
Bentley
  → n8n Cloud
    → Source / Stream / Ranking / Reaction Input
      → Local OpenShorts
        → OpenRouter / Qwen
          → Discord QC
            → Supabase Operational and Performance Ledger
              → GHL Media Storage
                → GHL Social Planner
                  → YouTube Shorts
                  → TikTok
                  → Instagram Reels
                  → Facebook Reels
```

Large media files remain local and must not traverse n8n Cloud or the public Funnel.

---

# Three Production Lanes

## Lane A — Stream/VOD Clipping

```text
Kick VOD, desktop video, local recording, OBS recording, or supported URL
→ local normalization
→ OpenShorts candidate extraction
→ Qwen scoring
→ post-production
→ Discord approval
→ Supabase record
→ GHL storage
→ GHL scheduling and publishing
```

## Lane B — Ranking Plus Bryan Reaction

```text
Topic intelligence
→ multi-source discovery
→ moment mining
→ reaction-ready ranking package
→ Bryan reacts live or records locally
→ reaction recording enters LOOM
→ source/reaction synchronization
→ Remotion/FFmpeg finishing
→ Discord approval
→ Supabase record
→ GHL storage and publishing
```

The prepared ranking compilation is reaction material. It is not automatically the final published asset.

The final publishable asset is produced after Bryan’s commentary/reaction is synchronized and edited with the source material.

## Lane C — Automated or Source-Dominant Rankings

```text
Topic intelligence
→ multi-source discovery
→ moment mining
→ ranking composition
→ optional limited narration or callouts
→ Discord approval
→ Supabase record
→ GHL storage and publishing
```

Bryan may appear minimally or not at all when the sourced material is strong enough and that treatment serves the format.

---

# Locked Decisions

## Factory and orchestration

* OpenShorts is the Factory MVP clipping chassis.
* OpenShorts is not assumed to be LOOM’s permanent clipping engine.
* OpenShorts must remain substantially intact during the MVP.
* n8n Cloud is the orchestration plane.
* Production orchestration is callback-first.
* Workflow 03 submits `POST /api/process` exactly once.
* OpenShorts sends a native authenticated terminal callback.
* Continuous production polling is prohibited.
* A bounded status check may exist only as a disconnected diagnostic tool.
* Downstream failures must never resubmit the original source.
* Candidate deduplication uses exactly `job_id:clip_index`.
* Each authenticated `job_id` may enter only one terminal-processing path.
* All workflows remain inactive during static construction.
* Runtime activation requires Bryan’s explicit approval.
* Jasmine/Derm by Erica workflows must not be modified.

## Scoring

* The current production-scoring model is `qwen/qwen3.5-9b`.
* OpenShorts’ score is excluded from Qwen-visible input to reduce anchoring.
* The OpenShorts score remains preserved in the final output and evidence record.
* Every numeric score must be an integer from 0 to 100.
* Model settings remain:

  * Temperature: 0.2
  * Maximum output: 1,200 tokens
  * Reasoning: none/excluded
* Scoring logic must remain structured and deterministic.
* Models may not silently change scoring weights, thresholds, architecture, publishing rules, or tenant policy.

## Human approval

* No candidate reaches distribution without an explicit human decision.
* MVP decisions are:

  * Approve
  * Reject
  * Hold
  * Retry system failure
* Rejected creative candidates return to the candidate population.
* LOOM does not create an unlimited custom-revision treadmill.
* Common system-level corrections may be added later when the defect ledger proves they are needed.

## Storage and data

* GHL Media Storage is canonical for approved and published media.
* GHL Social Planner is the intended scheduling and publishing plane.
* Supabase/Postgres is approved as LOOM’s structured operational, decision, experiment, asset-metadata, performance, and later learning ledger.
* Supabase does not replace GHL media storage.
* Reusable SFX, animations, overlays, transparent avatar loops, and template manifests will use private Supabase Storage plus a local hash-addressed cache.
* Cloudinary remains removed.
* Upload-Post is not part of the LOOM architecture.

## Distribution

Initial supported destinations:

* YouTube Shorts
* TikTok
* Instagram Reels
* Facebook Reels

Deferred destinations:

* X
* Reddit

Postiz may return only as an isolated X/Reddit adapter after the base Factory earns revenue. It must not replace GHL as the primary distribution plane.

## Tenants and scaling

* Start with Bryan’s primary tenant.
* Prove tenant isolation before activating tenant two.
* Do not pre-create five active operations.
* Additional channels and tenants require stable production, bounded cost, repeatable formats, and no unresolved platform issue.
* Tenant configuration belongs in Supabase.
* Tenant credentials remain in credential vaults.
* No tenant may receive another tenant’s media, metadata, credentials, prompts, destinations, schedules, decisions, or performance data.

## Infrastructure and cost

* Local Mac processing remains primary for the MVP.
* Local heavy-render concurrency begins at one.
* Do not stream and run heavy OpenShorts rendering simultaneously until thermal benchmarks prove it safe.
* No always-on cloud GPU.
* RunPod remains a deferred overflow experiment.
* Cache reusable sources, transcripts, hashes, TTS, sampled frames, and style assets.
* Initial all-in system-expense ceiling is $250 per month unless Bryan changes it.
* Cost alerts are required at 50%, 75%, 90%, and 100%.
* New discretionary AI/render work stops at the ceiling while preserving records and already-approved obligations.

## Security

* Credentials remain in their proper credential stores.
* Secret values must never appear in workflow JSON, documentation, reports, prompts, or logs.
* The OpenShorts callback-signing secret must be independent.
* It must not reuse:

  * The n8n API key
  * `LOOM Tunnel Proxy Token`
  * `LOOM Discord Alerts`
  * The OpenRouter credential
  * Any Jasmine/Derm by Erica credential
* **Resolved (Sept 8, 2026):** n8n Cloud's Variables/`$env` feature is not
  licensed on this account, which ruled out storing a shared HMAC secret
  there for Code-node verification of OpenShorts' own signature. A $0 local
  Caddy relay was added instead (existing `loom-tunnel-proxy` container,
  new loopback-only port `:8082`): OpenShorts posts its terminal callback to
  the relay, which injects a fixed private header (`X-LOOM-Relay-Token`,
  generated fresh, stored only in n8n's own Header Auth credential vault and
  in a local git-ignored `.env` file, mode 600) and forwards to n8n Cloud's
  webhook. The n8n Webhook node authenticates via a native Header Auth
  credential, `LOOM Relay Auth` — independent of every credential listed
  above. **Explicit trade-off:** this authenticates the Mac→n8n hop, not
  OpenShorts' own per-payload HMAC signature; OpenShorts' `webhook_secret`
  is no longer sent or verified anywhere in this pipeline. Flagged as a
  future hardening item, not silently treated as signature verification.
* The current n8n API key was regenerated and verified September 8, 2026, has full access, and does not expire.
* `Jasmine-Phase1/docs/TOOLS.md` is the current canonical local reference for the n8n API credential.
* Older n8n API keys are superseded.
* Bryan rotated the LOOM Factory alerts Discord webhook and updated the corresponding n8n credential successfully — **Bryan reported**.

---

# Current Technical Asset Register

## OpenShorts

* Repository: `mutonby/openshorts`
* Local branch: `loom/factory-mvp-baseline-fixes`
* Backend: loopback port 8000
* Bearer-token proxy: loopback port 8081
* Dashboard: `http://localhost:5175`
* Secured bridge: Tailscale Funnel + Caddy
* Missing/wrong/correct bearer-token behavior: 401/401/200 — **runtime verified**
* `noplaylist: true`: **runtime verified**
* Upload-Post gate bypass: **browser verified**
* Native terminal callback implementation: **static verified from installed OpenShorts source**
* Native callback accepts `webhook_url` and optional `webhook_secret`.
* Native callback uses HMAC-SHA256 over the raw body when a secret is supplied.
* Signature header:

  * `X-OpenShorts-Signature`
  * Format: `sha256=<hex>`
* Terminal event types:

  * `job.completed`
  * `job.failed`
* OpenShorts performs callback delivery retries according to its installed implementation.
* No custom Python callback emitter is required.

## n8n workflows

| Workflow                                  | ID                 | Current status                                                                                                              |
| ----------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `LOOM — 00 — Factory Failure Handler`     | `IYB1Pp8cTMrghaoK` | Inactive; statically constructed and verified; full runtime verification pending                                            |
| `LOOM — 01 — Score Candidate Clip`        | `Vttea1FqX4TVn4yH` | Inactive; scoring runtime previously verified; final 0–100 calibration run pending                                          |
| `LOOM — 02 — Ingest OpenShorts Candidate` | `6RNa6TXeppCQ7bhh` | Inactive; real candidate retrieval/scoring previously verified; Manual Trigger and sub-workflow routes statically confirmed |
| `LOOM — 03 — Factory Job Orchestrator`    | `x1ZYzsLc6Jaow0Wm` | Inactive; callback-first static correction in progress; runtime test not approved                                           |

## n8n credential references

* `LOOM Tunnel Proxy Token`
* `LOOM Discord Alerts`

The values must never be retrieved or reproduced in reports.

---

# Five Strategic Stages

## Stage 1 — Factory Spine

Build and verify source intake, OpenShorts processing, Qwen scoring, Discord approval, Supabase operational records, GHL storage, GHL scheduling, tenant isolation, and cost controls.

## Stage 2 — Ranking and Reaction Growth Engine

Build topic intelligence, multi-source discovery, semantic moment finding, ranking composition, reaction-session preparation, flexible reaction ingestion, source/reaction synchronization, dynamic layouts, benchmarking, backlog generation, and Channel 1 launch.

## Stage 3 — Evidence-Led Editor

Use measured production defects to improve boundaries, dead air, hooks, captions, layouts, speaker treatment, thumbnails, styles, and fast system-level corrections.

## Stage 4 — Cultural and Brand Editor

Add cultural profiles, English/Colombian Spanish/bilingual treatment, tenant voices, multilingual output, and cultural QC only after core formats prove themselves.

## Stage 5 — Learning, Distribution, and Business Intelligence

Collect and normalize performance, improve source/topic selection, improve boundaries/editing/presenter density, add retrieval over historical evidence, build campaign/channel intelligence, and connect content performance to leads, sales, payouts, and net revenue.

---

# Current Factory Execution Segments

| Segment                                       | Status                                           | Evidence                                                                 |
| --------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------ |
| 0 — Machine Preparation                       | Complete                                         | Runtime verified                                                         |
| 1 — OpenShorts Baseline                       | Complete                                         | Runtime verified                                                         |
| 2 — OpenRouter/Qwen Scoring                   | Functionally complete; one calibration test open | Runtime verified before final static calibration correction              |
| 3A — n8n Sub-workflow Bridge                  | Complete                                         | Runtime verified                                                         |
| 3B — Secured Funnel/Proxy                     | Complete                                         | Runtime verified                                                         |
| 3C — Existing Candidate Retrieval and Scoring | Complete                                         | Runtime verified                                                         |
| 3D — Factory Failure Handler                  | Static construction complete                     | Static verified; runtime verification pending                            |
| 3E — Callback-First Orchestrator              | In progress                                      | Polling removed; callback constructed; final three-item cleanup underway |
| Segment 3 Acceptance Test                     | Pending                                          | Runtime approval not granted                                             |
| 4A — Qwen 0–100 Calibration                   | Static correction complete                       | Runtime check pending                                                    |
| 4B — Discord Candidate Card                   | Complete                                         | Runtime verified (bot-delivered card w/ attachment + buttons)            |
| 4C — Approve/Reject/Hold/Retry                | Complete                                         | Runtime verified (Bryan clicked Hold once; signed path verified)         |
| 4D — Durable Decision Record                  | Complete                                         | Runtime verified (one Hold persisted to Supabase loom_reviews, idempotent)|
| 5 — GHL Media Storage                         | Pending                                          | Not started                                                              |
| 6 — GHL Social Planner Distribution           | Pending                                          | Not started                                                              |
| 7 — Tenant Control Plane                      | Pending                                          | Not started                                                              |
| 8 — Hardening and Cost Governor               | Pending                                          | Not started                                                              |

---

# Verified Completed Foundation

## Segment 0 — Machine Preparation

* Apple M2 Pro confirmed.
* 16 GB RAM confirmed.
* Docker confirmed.
* Docker Compose confirmed.
* Git confirmed.
* Local storage confirmed.
* OpenShorts repository established.
* Local project location established.

## Segment 1 — OpenShorts Baseline

* Dashboard and backend operated locally.
* Representative source duration: approximately 14 minutes 18 seconds.
* Four vertical clips rendered.
* Output resolution: 1080×1920.
* Playlist/Mix protection added.
* `noplaylist: true` runtime verified.
* Upload-Post billing gate bypass browser verified.
* Original source preserved.

## Segment 2 — Qwen Scoring

### Locked weights

* Hook: 20%
* Completeness: 20%
* Clarity: 15%
* Emotional intensity: 15%
* Shareability: 15%
* Context independence: 10%
* Duration: 5%

### Publishable gate

* Overall score ≥80
* Completeness ≥75
* Clarity ≥70
* Context dependency ≤45
* No repair actions
* No hard failures

Repairable is ≥60.

Below 60 is rejected.

### Completed correction

* OpenShorts score removed from Qwen-visible input.
* OpenShorts score preserved in final output.
* Explicit natural-language 0–100 integer instruction added.

### Remaining proof

One controlled post-correction model call must verify the final scale and schema.

## Segment 3A–3C

* Workflow 02 calls Workflow 01.
* Secure n8n-to-local bridge established.
* OpenShorts and proxy bound loopback-only.
* Bearer-token behavior validated.
* Real job `c22b31f9`, clip index 1, retrieved and scored.
* Overall score 76 and `repairable` decision independently recomputed and matched.
* Recorded Qwen cost: $0.00013112.
* No new OpenShorts job was created during the existing-candidate test.
* Workflows returned inactive afterward.

---

# Current Segment 3E Static State

The obsolete Workflow 03 polling system has been removed.

Removed components include:

* 60-second Wait loop
* Poll counter
* 720-poll ceiling
* 12-hour polling design
* Repeating status GET
* Poll-status router
* Any production loop back to status checking

Current target behavior:

```text
Validate source
→ create run context
→ POST /api/process exactly once (webhook_url points at local Caddy relay)
→ OpenShorts processes locally
→ OpenShorts posts terminal callback to the local relay (no signature verified)
→ Caddy relay injects a fixed private header, forwards to n8n Cloud
→ n8n Webhook node authenticates that header (native Header Auth credential)
→ correlate job (via workflow static data keyed on job_id)
→ lock first terminal event per job_id
→ enumerate unique candidates
→ Workflow 02
→ Workflow 01 scoring
→ isolate per-clip failures
→ aggregate complete batch
```

All three items below are now resolved (static verified this round). Kept as
a dated record rather than deleted, since this is exactly the kind of
before/after that evidence discipline requires preserving.

## Static controls already reported

* `POST /api/process` has automatic retry disabled.
* Only one inbound edge reaches the source-submission node.
* No downstream branch returns to source submission.
* Workflow 02 preserves:

  * Manual Trigger
  * Execute Sub-workflow Trigger
* Both Workflow 02 routes converge into the same normalized processing chain.
* Candidate key is exactly `job_id:clip_index`.
* Workflow 02 per-clip execution uses isolated error handling.
* One clip failure does not resubmit the source.
* Workflow 00 contains exactly 13 accepted failure codes.
* Source input is HTTPS-only and uses an explicit domain allowlist.
* Current URL path structurally rejects local paths.
* Local-file ingestion will be introduced as a separate local intake route rather than forcing local media through n8n Cloud.

## Segment 3E blockers — resolved this round (Sept 8, 2026)

### Callback secret — RESOLVED

`n8n Cloud's Variables/$env feature is not licensed on this account`
(confirmed: `GET /api/v1/variables` → HTTP 403, `feat:variables`), which
ruled out the originally planned `LOOM_OPENSHORTS_WEBHOOK_SECRET`/`$env`
approach entirely — that credential was never created.

**Resolution (Bryan-approved):** a $0 local Caddy relay. The existing
`loom-tunnel-proxy` container gained a second loopback-only port (`:8082`).
OpenShorts posts its terminal callback there instead of directly to n8n
Cloud; Caddy injects a fixed private header (`X-LOOM-Relay-Token`) and
forwards to n8n's real webhook path. The n8n Webhook node authenticates that
header via a native **Header Auth** credential (`LOOM Relay Auth`) — no
`$env`, no Variables, no custom HMAC Code node.

**Explicit trade-off:** this authenticates the Mac→n8n hop, not OpenShorts'
own per-payload HMAC signature. OpenShorts' `webhook_secret` field and
signature verification are no longer part of this pipeline. Flagged as a
future hardening item, not silently upgraded to "signature verified."

* Secret storage confirmed: never hard-coded, never in workflow JSON, never
  in `TOOLS.md`, not reused from another credential (a fresh, dedicated
  credential was created), never exposed through logs or reports.

### Terminal locking — RESOLVED

The first authenticated terminal event for a `job_id` claims the job (the
lock key is `job_id` alone, not `job_id:event`). A duplicate or conflicting
later terminal event is rejected before it can enter candidate processing.
Static verified via isolated unit tests of the extracted logic this round.

### Completed-result GET — RESOLVED (retained)

Compared directly against installed source: `job.completed`'s `clips[]`
(`_webhook_clip_entries` in `app.py`) contains only
`{index, title, video_url, download_url?}` — missing `start`/`end` (needed
for `clip_duration_seconds`), `viral_hook_text` (needed for `existing_hook`),
and `predicted_score` (needed for `openshorts_score`). The bounded 4-attempt
GET chain (immediate/5s/15s/45s) against `GET /api/status/{job_id}` is
retained for this reason — fires once after callback confirmation, never
loops back on itself, never becomes polling.

### Runtime-only questions — still unresolved, require an actual live test

The controlled test must still prove:

* Raw callback-body capture
* Relay-header authentication actually rejects unauthenticated requests
* Run-context persistence between production executions (requires Workflow
  03 to be active, per n8n's documented static-data behavior)
* Terminal-event deduplication
* Candidate enumeration
* Per-clip isolation
* Complete aggregation
* Exactly one source submission

None of these have been runtime-verified. Workflow 03 remains inactive.

---

# Segment 4 — Discord QC

## Runtime verification — COMPLETE (2026-09-13)

Segment 4 is complete and runtime-verified end-to-end with a real Discord
interaction. Evidence:

- **4B Candidate card** — bot-delivered to #clip-review (message
  `1548515929871360020`) with a Docker-independent review-proxy attachment
  (16.37 MB MP4, plays from Discord CDN, no localhost) and genuine
  Approve/Reject/Hold buttons (opaque `custom_id`, no secrets).
- **4C Approve/Reject/Hold** — Bryan personally clicked **Hold** exactly once
  (2026-09-13 02:52 UTC). Discord's signed interaction reached the adapter via
  the public Interactions Endpoint (`https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions`),
  was Ed25519-verified over the raw body, acknowledged within Discord's 3s
  deadline (card updated ~1s later), and resolved the correct clip
  (`6c46c3ef-…:1`), tenant (`loom-acceptance-test`), and reviewer
  (`passportpetty#688976899094872148`).
- **4D Durable decision record** — exactly one Hold decision
  (`final_status: on_hold`) persisted to Supabase `loom_reviews`; n8n's
  review-decision webhook re-persisted idempotently. First-terminal-decision-wins
  is enforced AT THE DATABASE LEVEL (`ON CONFLICT (clip_id) DO NOTHING`), so a
  repeated delivery or duplicate click cannot create or overwrite a second
  terminal decision. The card was edited to show the decision/reviewer/timestamp
  and its controls were disabled. No publication, GHL upload, retry, model call,
  or media regeneration occurred (`publication_enabled: false` throughout).
- The clearly-labeled test decision was **removed after recording evidence**;
  `loom_reviews` is empty. Bryan has NOT made a production review decision.

**Next: Segment 5A — GHL Media Storage** (GHL authentication + media upload
plane; only after the decision gate, which is now proven).

## Current Discord state

* Server: `Clippy Factory`
* LOOM Factory alerts webhook rotated by Bryan.
* Updated n8n Discord credential saved successfully.
* Public Interactions Endpoint URL registered and accepted by Discord
  (2026-09-13): `https://users-macbook-pro-1.taild000b2.ts.net/discord/interactions`.
* Adapter (`infra/discord-review/adapter.py`) supervised via `supervise.sh`.

## Candidate card fields

Every candidate must show:

* Preview or URL
* Source title (full source-video title)
* Proposed Shorts title (candidate-specific; "Not generated" when absent)
* Hook (explicit-language masked on the card; raw preserved internally)
* Proposed caption
* LOOM score
* Duration
* Tenant
* Run ID
* Caption/hook status
* Explicit-language warning (when detected)
* Publication status (disabled in MVP)

## Human actions

* Approve
* Reject
* Hold
* Retry system failure

## Durable decision record

Supabase must eventually store:

* Reviewer
* Decision
* Timestamp
* Rejection reason
* Requested system correction, when applicable
* Final status

No candidate reaches GHL or distribution without an explicit decision.

---

# Flexible Input Policy

LOOM must accept multiple reasonable inputs without making OBS mandatory:

1. Desktop/local completed video
2. Kick VOD or Kick clip URL
3. Local camera/screen recording
4. OBS program recording
5. Optional OBS multitrack/separate feeds
6. Other supported local file or approved URL

Every input is normalized locally before OpenShorts processing.

OBS multitrack is an enhancement, not a Factory-launch dependency.

When available, separate camera, microphone, source, and program feeds may improve post-production control.

---

# Ranking and Reaction Growth Engine

Stage 2 begins after the Stage 1 approval, storage, and distribution spine passes end to end.

## Topic intelligence

Initial topic interests include:

* Relationships
* Breakups
* Sports
* Animals
* Anime
* Fights and confrontations
* Music videos
* Artists
* Influencer moments and crash-outs
* Accidents and rescues
* Podcasts and interviews
* Gaming
* Major releases and cultural events
* Current events and drama

Rank opportunities using:

* Recent velocity
* Channel-relative outperformance
* Bryan fit
* Emotional strength
* Available source moments
* Saturation
* Freshness
* Recurring format evidence
* Advertiser suitability
* Data confidence

## Discovery tools

* Free YouTube search, RSS, metadata, and WebSub-style updates for routine collection
* vidIQ as an optional manual intelligence overlay
* Satura as optional competitive research
* Google Lens as a manual source/provenance aid
* LOOM’s own performance history after publishing begins

No paid vidIQ account or Satura API is required for the Factory MVP.

Satura is not a production dependency.

LOOM may reproduce observable principles—not proprietary code or undisclosed formulas—including:

* Channel-relative outliers
* Emerging-channel velocity
* Trending topics
* Upload consistency
* Viewer-response diagnostics
* Hook strength
* Payoff timing
* Emotional movement
* Visual refresh
* Semantic scene and quote search
* Thumbnail testing

## Semantic finder

The future finder should accept descriptions of:

* Scene
* Action
* Quote
* Emotion
* Topic
* Visual event

It should return:

* Source
* Timestamp
* Evidence
* Match confidence
* Reuse history

## Boundary integrity

Candidates must preserve:

* Setup
* Complete sentence
* Meaningful action
* Payoff
* Reaction
* Context independence

Required checks include:

* `setup_complete`
* `payoff_complete`
* `sentence_complete`
* `abrupt_cut`
* `recommended_start`
* `recommended_end`

## Ranking framework

```text
Immediate visual hook
→ countdown/ranking
→ escalating moments
→ strongest payoff
→ brief comment prompt
```

Source quality and complete moments outrank complicated ranking mathematics.

## Presenter modes

* `human_reaction`
* `hybrid`
* `automated`
* `source_dominant`

Bryan’s personality is the differentiator, but source material remains primary where that creates stronger retention.

Less Bryan may be more effective for some clips.

## Reaction capture

Every reaction-ready package should include:

* Topic
* Working title
* Ordered source moments
* Setup/context notes
* Suggested reaction beats
* Source links and timestamps
* Local playback package
* Optional Stream Deck/RØDECaster cues
* `ranking_package_id`

## Dynamic post-production

Supported or planned layouts:

* Source full-screen
* Bryan full-screen
* Bryan overlay
* Split screen
* Reaction punch-in
* Freeze-and-comment
* Replay-with-reaction
* Later green-screen cutout

The edit must not cover essential dialogue or action.

## Style assets

Initial reusable library target:

* 20–30 SFX
* 10 transitions
* 5 countdown animations
* 5 overlays
* 3 caption presets
* 3 ranking styles

Canonical reusable assets:

* Private Supabase Storage bucket: `style-assets`
* Mac: hash-addressed local render cache
* Final approved videos: GHL Media Storage

Cloudinary must not return.

---

# YouTube Channel Operations

After the publishing spine works:

* Test configurable upload spacing, including a 45-minute cadence.
* Publish useful Community posts when channel access exists.
* Surface comments requiring human response.
* Do not send generic spam replies.
* Connect Shorts to the strongest related video or series where supported.
* Treat collaborations as measured experiments.
* Do not treat “view jail” or any proprietary TrustScore as an official YouTube status.
* Diagnose performance through observable metrics.
* Maintain niche consistency while allowing timely one-off reactions.
* Build a backlog for rest days.
* Do not launch Channel 2 until Channel 1 proves stable.

Platform duration remains configurable.

Do not force every clip into one length.

Maintain a tested mix of:

* Concise retention-first Shorts
* 60+ second versions when current platform monetization rules justify them

---

# Paid Clipping Campaign Support

The same Factory spine should eventually support two or three external campaigns without contaminating Bryan’s own channels.

Each campaign work order should record:

* Campaign/platform
* Creator
* Niche/account identity
* Approved source inventory
* Deliverable rules
* Posting requirements
* RPM
* Total and remaining budget
* Saturation
* Deadline
* Submission proof
* Approval status
* Expected payout window
* Paid date
* Gross payout
* Production cost
* Net cash received

Campaign marketplace monitoring and alerts are deferred until the spine works.

The cash-flow tracker is not part of the current spine build.

It is a later Supabase business view that separates:

* Money earned
* Money approved
* Money expected
* Money actually received
* Expenses
* Net cash

Do not build it before the spine.

---

# Learning Architecture

## Supabase/Postgres

Stores durable structured facts and relationships.

Minimum future tables:

* `tenants`
* `sources`
* `jobs`
* `clips`
* `reviews`
* `publications`
* `performance_snapshots`
* `topic_signals`
* `experiments`
* `style_assets`

## Lineage

```text
topic_id
→ source_id
→ source_moment_id
→ ranking_package_id
→ reaction_session_id
→ reaction_moment_id
→ final_clip_id
→ publication_id
→ performance_snapshot_id
```

## RAG

RAG retrieves relevant historical outcomes into Qwen prompts.

It does not train the base model.

Do not implement vector RAG until LOOM has approximately:

* 100 published posts, or
* 30 days of useful performance evidence

Before then, use explicit rules and deterministic SQL summaries.

## MCP

MCP is an optional future access interface for agents.

MCP is not storage.

MCP is not the learning system.

MCP is not required for n8n to use Supabase.

## Performance signals

Collect:

* Views
* Viewed versus swiped
* Watch time
* Average percentage viewed
* Completion rate
* Replay rate
* Likes
* Comments
* Shares
* Saves
* Subscribers/follows
* Profile clicks
* Kick referrals
* Claims/removals
* Monetization status
* Revenue
* Leads
* Sales

Normalize by:

* Platform
* Account size
* Post age
* Topic
* Format
* Duration
* Tenant
* Posting time

Use results to improve:

* Topic selection
* Source selection
* Moment boundaries
* Ranking formats
* Presenter density
* Layouts
* Posting schedules
* Tenant/channel expansion

Significant policy or scoring changes remain subject to human approval.

---

# Deferred Scope

The following must not distract from the Factory spine:

* X/Reddit distribution through Postiz
* Cloud GPU overflow
* RunPod
* Closed-weight QC
* Advanced green-screen/background removal
* Large avatar-animation library
* Fully autonomous multi-platform trend scraping
* Paid vidIQ dependency
* Satura API integration
* Cultural/multilingual variants
* Multiple active tenants before Channel 1 proof
* Complex revision workflows
* Cash-flow dashboard
* Vector RAG before evidence exists
* Automated campaign-marketplace monitoring
* Original YouTube Mix regression rerun

---

# Defect Ledger

## FACTORY-INGEST-001

**Title:** Single-video URL expands into unintended playlist
**Severity:** High
**Status:** Resolved and runtime verified

### Original defect

A YouTube URL containing an auto-generated Radio/Mix playlist identifier caused yt-dlp to begin downloading the entire Mix instead of one requested video.

### Correction

`noplaylist: true` was added to the appropriate download/probe paths.

### Verified outcome

LOOM processed one representative source without expanding into the unintended playlist.

Playlist/Mix rejection and single-video behavior remain mandatory protections.

## FACTORY-ORCH-001

**Title:** Continuous polling creates unnecessary complexity and cost exposure
**Severity:** High
**Status:** Static correction complete; runtime verification pending

### Original defect

Workflow 03 was constructed with a 60-second polling loop and a maximum of 720 polls/12 hours.

### Correction

Continuous polling and its associated counter, waits, routes, and repeated status checks were removed.

OpenShorts’ native authenticated terminal callback replaces the polling design.

### Remaining proof

One controlled runtime test must prove callback receipt, authentication, correlation, deduplication, candidate processing, and exactly-once source submission.

## FACTORY-CALLBACK-001

**Title:** New callback secret requires secure n8n Cloud storage
**Severity:** Blocking for runtime
**Status:** Resolved (Sept 8, 2026) — static verified; runtime unverified

### Required outcome

A new independent OpenShorts webhook-signing secret must be stored and resolved without appearing in workflow JSON, tools documentation, reports, logs, or execution output.

The secret must not reuse any existing credential.

### Resolution

n8n Cloud's Variables/`$env` feature is not licensed on this account
(confirmed via `GET /api/v1/variables` → HTTP 403), so the originally
planned secret was never created there. Resolved instead with a $0 local
Caddy relay: OpenShorts posts its callback to a new loopback-only Caddy port
(`:8082` on the existing `loom-tunnel-proxy` container), which injects a
fixed private header and forwards to n8n Cloud. The n8n Webhook node
authenticates via a new, independent, native Header Auth credential
(`LOOM Relay Auth`) that reuses no other credential. The header token lives
only in that n8n credential and in a local git-ignored `.env` file (mode
600) — never printed, logged, or written into any documentation.

**Trade-off carried forward:** OpenShorts' own HMAC signature is no longer
verified anywhere in this design (only the Mac→n8n hop is authenticated).
Not yet runtime-tested — Workflow 03 remains inactive.

---

# Exact Next Action

All three narrow static cleanup items are now resolved (Sept 8, 2026):

1. Callback-secret mechanism: resolved via the $0 local Caddy relay + native
   n8n Header Auth credential (see FACTORY-CALLBACK-001).
2. First-authenticated-terminal-event lock per `job_id`: implemented and
   static verified.
3. Post-callback GET chain: retained, with the exact missing fields
   documented (`start`/`end`, `viral_hook_text`, `predicted_score`).
4. Workflows 00–03 remain inactive — runtime verified via fresh n8n API GET.
5. No workflow execution or external transmission occurred this round.

**Manual step still required before any live test:** Bryan must restart/
reload the `loom-tunnel-proxy` container so it picks up the new Caddyfile
and the new relay token env var, and separately approve activating
Workflow 03 (required for the webhook to listen and for run-context/dedup
static data to persist).

After Bryan and the supervising architecture review approve the static
state:

1. Restart the local Caddy relay container.
2. Activate Workflow 03.
3. One controlled Segment 3 acceptance test is authorized.
4. Qwen’s corrected 0–100 scale is verified during the controlled run.
5. The Discord candidate card is verified.
6. Segment 4C/4D approval and durable-decision work begins.
7. GHL authentication begins only after the approval gate works.

---

# Schedule Gates

| Stopping point                                                    | Judgment                               |
| ----------------------------------------------------------------- | -------------------------------------- |
| Callback cleanup unresolved                                       | Below target                           |
| Static callback cleanup complete but no controlled acceptance run | Progress made; proof still pending     |
| Segment 3 acceptance test plus Qwen/card verification passes      | On schedule and a successful build day |
| Discord decision gate and durable record pass                     | Ahead of schedule                      |
| GHL authentication begins after the decision gate                 | Stretch-ahead                          |

---

# Decision Status Legend

* **Locked** — explicitly decided; do not silently change.
* **Active** — currently being worked.
* **Runtime verified** — proven in actual operation.
* **Static verified** — inspected but not fully executed.
* **Bryan reported** — confirmed by Bryan.
* **Bentley reported** — reported by Bentley without independent/runtime proof.
* **Planned** — approved scope not yet built.
* **Deferred** — intentionally postponed until an entry condition is met.
* **Rejected** — considered and excluded from the architecture.

---

# Final Operating Rule

Finish the spine before opening the throttle.

LOOM’s advantage is not volume alone. It is the combination of:

* Strong source selection
* Complete moments
* Bryan’s selective human presence
* Automated finishing
* Explicit approval
* Tenant-safe publishing
* Low unit cost
* Durable performance evidence
* A feedback system that compounds

Every new component must either complete the current spine, satisfy a documented business requirement, or fix a measured defect.

No feature enters production merely because it is impressive.

## Media filename naming contract (Segment 5A, 2026-09-13)

Candidate identity is **zero-based** and must never be confused with the
one-based renderer filename ordinal.

- `clip_id = <job_id>:<clip_index>` (zero-based). `:0` = first short, `:1` = second short.
- OpenShorts renderer filenames are **one-based**: `clip_1` = `:0`, `clip_2` = `:1`.
- GHL media filenames must disambiguate explicitly, e.g.:
  `index-01__ordinal-02__render-1.0`  (zero-based index, human ordinal, render version).
- `asset_version` uses the `render-<major>.<minor>` form (e.g. `render-1.0`).
- `storage_status='fixture'` = storage-acceptance only; NOT publication-approved.
