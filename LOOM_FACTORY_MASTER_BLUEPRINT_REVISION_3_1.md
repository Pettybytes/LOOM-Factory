# LOOM Factory — Master Blueprint and Cross-Model Handoff

**Revision:** 3.1 — continuity audit patch  
**Date:** September 8, 2026  
**Owner:** Bryan  
**Engineering executor:** Bentley, operating locally on Bryan's Mac  
**Document role:** Current operational Source of Truth for Bryan, ChatGPT, Gemini, Claude, Bentley, and the visual roadmap artifact.

## 1. Instructions to every AI reading this document

1. Treat this revision as authoritative where it conflicts with an older LOOM roadmap.
2. Do not infer that something works because it was designed, constructed, or reported.
3. Preserve the evidence labels exactly:
   - **Runtime verified:** observed in a real execution or browser/runtime test.
   - **Bryan reported:** Bryan personally observed or confirmed it.
   - **Bentley reported:** Bentley reports it was built or changed, but independent/runtime proof is incomplete.
   - **Static verified:** configuration or code was inspected, but the complete live path has not run.
   - **Planned:** accepted scope, not built.
   - **Deferred:** deliberately excluded until its entry condition is met.
4. Do not ask Bryan to export workflow JSON that Bentley can retrieve locally. JSON inspection is appropriate only when an executor is about to inspect or change the workflows.
5. Do not activate workflows, publish media, submit a paid model request, or run a VOD without Bryan's approval.
6. Do not redesign the architecture unless runtime evidence exposes a defect.
7. Cost control is a product requirement, not an optimization to add later.

## 2. Executive system map

LOOM is no longer only a long-form clipper. It is a modular content factory with three production lanes sharing one approval, storage, publishing, and learning spine.

### Lane A — Stream/VOD clipping

```text
Kick VOD, desktop video, local recording, or optional OBS recording
→ normalize locally
→ OpenShorts candidate extraction
→ Qwen scoring
→ post-production
→ Discord approval
→ GHL storage and scheduled publishing
```

### Lane B — Ranking plus Bryan reaction

```text
Viral topic discovery
→ multi-source moment mining
→ reaction-ready ranking package
→ Bryan reacts live or records locally
→ ingest reaction recording
→ synchronize source and reaction
→ Remotion/FFmpeg finishing
→ Discord approval
→ GHL storage and scheduled publishing
```

### Lane C — Automated or source-dominant ranking

```text
Viral topic discovery
→ multi-source moment mining
→ ranking composition
→ limited narration/callouts when useful
→ Discord approval
→ GHL storage and scheduled publishing
```

The ranking package in Lane B is **reaction material**, not automatically the final published asset. The reaction/commentary master becomes the source for the final edit.

## 3. Locked architecture and boundaries

### Core stack

| Responsibility | Locked component | Boundary |
|---|---|---|
| Orchestration | n8n Cloud | Coordinates jobs; does not transport large local media |
| Clip chassis | Self-hosted OpenShorts | Local processing; do not replace during MVP |
| Completion signal | OpenShorts native callback | No continuous production polling |
| Scoring/planning | OpenRouter + `qwen/qwen3.5-9b` | Structured requests; sampled finalist media only when needed |
| Rendering | OpenShorts + FFmpeg + Remotion | Extend the current renderer; no second rendering chassis |
| Human QC | Discord | Explicit approve/reject/hold/retry gate |
| Structured memory | Supabase/Postgres | Jobs, clips, decisions, performance, experiments, topic signals, and asset metadata |
| Reusable style assets | Private Supabase Storage + local hash cache | SFX, overlays, animations, caption/ranking presets |
| Final media | GHL Media Storage | Canonical approved/published media location |
| Distribution | GHL Social Planner | Scheduling and publishing plane |

### Current technical asset register

| Asset | Current identifier/state |
|---|---|
| OpenShorts branch | `loom/factory-mvp-baseline-fixes` |
| OpenShorts backend | Loopback port 8000 |
| Bearer-token proxy | Loopback port 8081 |
| Secure bridge | Tailscale Funnel + Caddy; 401/401/200 behavior runtime verified |
| Workflow 00 — Failure Handler | `IYB1Pp8cTMrghaoK` |
| Workflow 01 — Score Candidate Clip | `Vttea1FqX4TVn4yH` |
| Workflow 02 — Ingest OpenShorts Candidate | `6RNa6TXeppCQ7bhh` |
| Workflow 03 — Factory Job Orchestrator | `x1ZYzsLc6Jaow0Wm` |
| n8n tunnel credential | `LOOM Tunnel Proxy Token` |
| Discord credential | `LOOM Discord Alerts` |

Secret values never belong in this blueprint. The regenerated September 8, 2026 n8n API key is documented as non-expiring in the designated local tools record and must never be echoed into reports or workflow JSON.

### Locked exclusions and deferrals

- Cloudinary remains removed.
- Postiz remains deferred until revenue and may return only for X/Reddit.
- RunPod/cloud GPU remains deferred until local thermal or throughput evidence demands overflow.
- `opensource-clipping` remains a possible donor only after a measured defect.
- Closed-weight QC remains deferred.
- No continuous polling loop.
- No large file uploads through n8n Cloud or the public Funnel.
- No complex bespoke revision treadmill: approve, reject, hold, or retry system failure.
- No additional active tenant before Channel 1 proves stable production and bounded cost.
- Jasmine/Derm by Erica workflows remain untouched.

## 4. Current location

**Current stage:** Stage 1 — Factory Spine  
**Current segment:** Segment 6B — GHL Social Planner Distribution (ACCEPTED)  
**Exact position:** Segment 6B accepted on combined evidence (automated harness: Create Post → unlisted YouTube → reconciliation → durable receipt; plus Workflow 05 exec `10296`: tenant-driven existing-receipt lookup → `already_distributed`, zero GHL calls, no duplicate). Next Stage 1 priority: the Discord-centered OpenShorts editor (bounded inspection first). Workflow 05's Create Post/reconciliation branches are statically implemented and receive their first workflow-native runtime proof with the first genuine approved asset.

### Progress snapshot

| Segment | Status | Evidence |
|---|---|---|
| 0 — Machine Preparation | Complete | Runtime verified |
| 1 — OpenShorts Baseline | Complete | Runtime verified |
| 2 — OpenRouter/Qwen Scoring | Complete | Runtime verified |
| 3 — n8n Orchestration (ingest) | Complete | Runtime verified; closed — not reopened |
| 4 — Discord QC | Complete | Runtime verified end-to-end (signed Hold) |
| 5 — GHL Media Storage | Complete | Runtime verified (storage-acceptance fixture) |
| 6A — Tenant Distribution Mapping | Complete | Runtime verified (Dreemzy YouTube) |
| 6B — Distribution | Accepted | Combined evidence: harness + exec 10296 `already_distributed` |
| 6C — Scheduling rules | Moved to Stage 2 | Stage 1 keeps min publish-mode/time validation (G12) |
| 6D — Distribution receipt | Pending | Schema live; reconciliation static-verified |
| 7–8 | Pending | Not yet built |
| 9–16 (+ tenant provisioning) | Planned | Ranking/Reaction Engine |
| 17–33 | Planned/deferred by entry gates | Evidence-led improvement and scale |

## 5. Stage 1 — Factory Spine

**Mission:** Prove that one source can become scored, reviewed, stored, scheduled, and measured media with bounded cost and no manual editing requirement.

### Segment 0 — Machine Preparation — COMPLETE / RUNTIME VERIFIED

- Apple M2 Pro, 16 GB RAM, disk capacity, Docker, Compose, and Git confirmed.
- Local LOOM project and OpenShorts containers established.

**Victory:** The local Factory floor runs.

### Segment 1 — OpenShorts Baseline — COMPLETE / RUNTIME VERIFIED

- Local dashboard/backend operated successfully.
- Representative 14:18 source produced four 1080×1920 clips.
- `noplaylist: true` and playlist/Mix protection verified.
- Upload-Post billing gate bypass verified in browser.

**Victory:** OpenShorts produces usable vertical candidates from a real source.

### Segment 2 — OpenRouter and Qwen Scoring — COMPLETE WITH ONE OPEN RUNTIME CHECK

#### 2A — Connection and isolation — COMPLETE

- Existing OpenRouter credential reused without exposing the key.
- LOOM workflows logically separated from Derm by Erica.

#### 2B — Structured score contract — COMPLETE

- Model: `qwen/qwen3.5-9b`.
- Temperature 0.2; maximum output 1,200 tokens; reasoning excluded.
- Required structured output and deterministic validation established.

Locked score weights:

- Hook: 20%
- Completeness: 20%
- Clarity: 15%
- Emotional intensity: 15%
- Shareability: 15%
- Context independence: 10%
- Duration: 5%

Locked publishable gate:

- Overall score ≥80
- Completeness ≥75
- Clarity ≥70
- Context dependency ≤45
- No repair actions
- No hard failures

Repairable is ≥60; otherwise reject. These values change only through an approved benchmark—not silent model judgment.

#### 2C — Independent scoring correction — STATIC COMPLETE

- OpenShorts score removed from Qwen-visible input to reduce anchoring.
- OpenShorts score preserved in the final record.
- Every numeric score explicitly constrained to integer 0–100.

#### 2D — Calibration test — OPEN

- One controlled live model call must prove the final 0–100 schema and scale.

**Victory:** Candidate scoring is consistent, structured, inexpensive, and independent.

### Segment 3 — n8n Callback-First Orchestration — IN PROGRESS

#### 3A — Sub-workflow bridge — COMPLETE / RUNTIME VERIFIED

- Workflow 02 successfully calls Workflow 01.

#### 3B — Secured local bridge — COMPLETE / RUNTIME VERIFIED

- Tailscale Funnel + Caddy bearer proxy established.
- Missing, wrong, and correct credentials returned 401/401/200.

#### 3C — Existing candidate retrieval/scoring — COMPLETE / RUNTIME VERIFIED

- A real existing OpenShorts candidate was retrieved and scored.

#### 3D — Failure handler — BENTLEY REPORTED; VERIFICATION PENDING

- Workflow 00 must enforce approved failure codes, safe payload truncation, credential-backed Discord alerts, bounded GET transport retries, and per-clip failure isolation.
- No failure path may return to `POST /api/process`.

#### 3E — Full-job orchestrator — CORRECTION REQUIRED

- Workflow 03 was constructed around obsolete continuous polling.
- Replace polling with OpenShorts' installed native terminal callback.
- Submit `POST /api/process` exactly once.
- Authenticate callback using the installed HMAC signature contract.
- Correlate `run_id` and `job_id`.
- Reject replay/duplicate callbacks.
- Deduplicate clips by `job_id:clip_index`.
- Preserve Workflow 02 manual and sub-workflow entry routes.
- Keep a bounded manual status check only for diagnosis.

#### Segment 3 acceptance test — PENDING

One approved test must prove: exactly one submission, no continuous polling, authenticated callback, all candidates discovered, independent Qwen scoring, isolated clip failures, complete batch aggregation, safe failure alerting, and inactive workflows afterward.

**Victory:** One trigger runs one entire OpenShorts job without polling or duplicate spend.

### Segment 4 — Discord QC and Durable Decisions — IN PROGRESS / DEPENDS ON SEGMENT 3

#### 4A — Scoring calibration — OPEN

- Complete Segment 2D during the controlled Segment 3 run.

#### 4B — Candidate card — BRYAN REPORTED COMPLETE; VERIFY

Required fields: preview/URL, source, hook, caption, LOOM score, duration, tenant, run ID.

#### 4C — Human actions — PENDING

- Approve
- Reject
- Hold
- Retry system failure

#### 4D — Supabase decision record — PENDING

- Record reviewer, decision, timestamp, rejection reason, requested system correction, and final status.
- No candidate advances without an explicit decision.

**Victory:** Distribution cannot occur without a durable human decision.

### Segment 5 — GHL Canonical Media Storage — PENDING

#### 5A — Minimum-scope authentication

Confirm the current GHL access model, required media scopes, target location, and vault-backed credential.

#### 5B — Tenant folder convention

`LOOM/{tenant_id}/{Sources|Candidates|Approved|Published|Rejected}`

#### 5C — Approved-media upload

Discord approval → n8n → GHL Media Storage → canonical GHL URL.

#### 5D — Validation

Verify tenant, folder, filename, duration, hash, URL, and duplicate protection.

**Victory:** Every approved clip has exactly one canonical GHL record.

### Segment 6 — GHL Social Planner Distribution — PENDING

#### 6A — Profile connections

Connect YouTube Shorts, TikTok, Instagram Reels, and Facebook Reels per tenant.

#### 6B — Platform test matrix

Validate format, title/caption, selected account, schedule, public result, and returned post ID for each enabled platform.

The matrix must also validate platform-specific duration and monetization rules. Do not force every clip to one duration. Maintain a tested mix of concise retention-first Shorts and, where the current platform program requires it, eligible 60+ second outputs.

#### 6C — Scheduling rules — MOVED TO STAGE 2 (2026-09-13)

Full scheduling rules — tenant timezone/approved windows, configurable spacing (including the 45-minute test cadence), daily account maximums, duplicate prevention, platform-specific metadata, and failure notification — are deferred to Stage 2. Stage 1 preserves only the minimum explicit publish-mode/time validation: a schedule date, when present, must be a valid ISO-8601 timestamp, and publish mode must be an explicit supported value. This is enforced by the distribution workflow's preflight gate (G12) only.

#### 6D — Distribution receipt

Record platform, account, scheduled/published times, post ID, public URL, and success/failure evidence.

**Victory:** One approved GHL asset reaches every enabled test destination with receipts.

### Segment 7 — Tenant Control Plane — PENDING

- Start with Bryan's primary tenant.
- Store tenant ID, GHL location, brand identity, profiles, content pillars, prompts, templates, approval destination, schedule, quota, retention policy, and cost ceiling.
- Tenant configuration lives in Supabase; credentials remain in vaults.
- Prove isolation before activating tenant two.

#### Paid clipping campaign work orders

The same tenant-safe spine must support two or three external clipping campaigns without contaminating Bryan's own channels. Each campaign work order records:

- Campaign/platform and creator
- Accepted source inventory
- Required niche/account identity
- Deliverable and posting rules
- RPM, total budget, remaining budget, and saturation notes
- Submission deadline and proof requirements
- Payout status, expected payment window, actual payment, and dispute evidence
- Per-campaign cost and net profit

Campaign opportunity alerts and marketplace monitoring are a later input adapter. They do not block the Factory spine.

**Victory:** A second tenant activates through configuration, without code changes or cross-tenant leakage.

### Segment 8 — Hardening and Cost Governor — PENDING

- Idempotency, replay protection, bounded retries, timeouts, and rate-limit handling
- Source duration/file-size ceilings
- Per-job and per-tenant spend ledgers
- Cache reuse by URL/hash
- TTS and frame-analysis reuse
- Temporary-source expiration after 24–72 hours
- Audit logs, health checks, run summaries, and emergency stop
- Local heavy-render concurrency initially fixed at one
- Do not stream and heavily render concurrently until thermal testing clears it
- No always-on GPU
- Initial all-in system-expense ceiling: $250 per month unless Bryan explicitly changes it
- Spend alerts at 50%, 75%, 90%, and 100% of the monthly ceiling
- Stop new discretionary AI/render work at the ceiling while preserving records and already-approved obligations

**Stage 1 victory:** LOOM reliably transforms one source into reviewed, stored, scheduled, and measured clips with bounded cost.

## 6. Stage 2 — Ranking and Reaction Growth Engine

**Mission:** Turn proven viral interests into a daily reaction queue and multiple publishable ranking/reaction Shorts without replacing the Stage 1 spine.

### Automated Discord Tenant Provisioning — PLANNED (Stage 2, added 2026-09-13)

Multi-tenant Discord QC without manual per-tenant wiring:

- One LOOM Discord bot/application.
- Automatically create tenant categories and review channels.
- Store channel IDs in tenant configuration.
- Route review cards dynamically by `tenant_id`.
- One shared interaction endpoint (the existing `/discord/interactions` funnel) — no per-tenant webhook.
- Avoid one manually configured webhook per tenant/channel.
- Keep the existing shared `#clip-review` channel for Stage 1.

**Victory:** A new tenant's Discord review lane is provisioned by configuration, not by hand-creating channels and webhooks.

### Segment 9 — Topic Intelligence — PLANNED

- Monitor recent velocity, channel outliers, recurrence, freshness, saturation, emotional strength, Bryan fit, and advertiser suitability.
- Initial interests: relationships, sports, animals, anime, confrontations, breakups, music/artists, influencer moments, accidents/rescues, podcasts/interviews, gaming, current events, and drama.
- Use free YouTube search/RSS/metadata for routine collection.
- Use free/manual vidIQ as an intelligence overlay, not a dependency.
- Use Google Lens manually for provenance or source discovery on finalists, not as an automated trend API.
- Output a daily Discord opportunity/reaction board.
- Represent Satura-inspired research concepts through observable inputs—not proprietary formulas: channel-relative outliers, emerging-channel velocity, recurring format patterns, upload consistency, viewer-response signals, and data confidence.

**Victory:** LOOM produces a ranked daily list of promising subjects with evidence and source leads.

### Segment 10 — Broad Source Discovery and Provenance — PLANNED

- Search accessible indexes for strong moments matching the ranking concept.
- Capture source URL, owner/channel, timestamp, publication date, hash, and reuse history.
- Keep a source/risk ledger.
- Do not treat short duration, narration, or commentary as automatic immunity from claims or monetization review.

**Victory:** Every candidate moment is traceable and reusable without rediscovering it.

### Segment 11 — Multi-Source Moment Mining — PLANNED

- Download/import candidates locally.
- Use transcript and scene analysis first.
- Sample only finalist frames for multimodal analysis.
- Remove duplicates/near-duplicates through local perceptual hashing.
- Preserve complete setup, meaningful action/dialogue, payoff, and reaction.
- Auto-extend boundaries when a sentence or payoff is cut off.
- Support semantic finder requests that describe a scene, action, quote, emotion, or topic in plain language; return source, timecode, evidence, and match confidence for human review.

Required checks: `setup_complete`, `payoff_complete`, `sentence_complete`, `abrupt_cut`, `recommended_start`, and `recommended_end`.

**Victory:** Sourced moments are genuinely strong and do not amputate the reason people wanted to watch.

### Segment 12 — Lean Ranking Composer — PLANNED

Framework: immediate visual hook → countdown/ranking → escalating moments → strongest payoff → brief comment prompt.

- 1080×1920 deterministic composition
- Ranking labels and captions
- Three to five reusable transition/SFX families
- Template, source, and provenance manifests
- Quality of sourced moments and boundary integrity outrank sophisticated ranking math

**Victory:** LOOM creates a coherent reaction-ready or publish-ready ranking package.

### Segment 13 — Reaction-Ready Package and Session Board — PLANNED

Each package supplies Bryan with:

- Topic and working title
- Ordered source moments
- Context/setup notes
- Suggested reaction beats or questions
- Source links and timing manifest
- Local playback package
- Optional Stream Deck/RØDECaster cues
- `ranking_package_id`

The package can be played during a Kick stream or a separate recording session.

**Victory:** Bryan can sit down and react without spending the session hunting for content.

### Segment 14 — Flexible Reaction Ingestion — PLANNED

Accepted inputs:

1. Desktop/local completed video — preferred when available for simplicity and quality.
2. Kick VOD or Kick clip URL — convenient post-stream route.
3. Local screen/camera recording — valid without OBS.
4. OBS program recording — optional.
5. OBS multitrack/separate feeds — advanced option, not an MVP dependency.
6. Other approved local file or supported URL.

Every input is validated, imported locally, hashed, associated with the source/ranking manifest, and normalized through FFmpeg before analysis. Large local files never traverse n8n Cloud.

**Victory:** The Factory processes Bryan's reaction regardless of which reasonable recording path he used.

### Segment 15 — Reaction Synchronization and Dynamic Post-Production — PLANNED

- OpenShorts finds Bryan's best reaction/commentary moments.
- Qwen checks whether each reaction adds context, humor, emotion, or personality.
- Match reaction timestamps to the prepared source moments.
- Remotion/FFmpeg constructs the final timeline.
- Layouts may move among source-full, Bryan-full, Bryan overlay, split screen, reaction punch-in, freeze/comment, replay/reaction, and later green-screen cutout.
- Never obscure essential dialogue or action.
- Source-dominant edits intentionally use less Bryan when that improves retention.
- Apply post-production SFX, animations, overlays, and caption presets only where they improve the beat.

Presenter modes: `human_reaction`, `hybrid`, `automated`, and `source_dominant`.

**Victory:** A ranking package plus Bryan's recording becomes a clean, human-connected final Short.

### Segment 16 — Benchmark, Backlog, and Channel 1 Launch — PLANNED

- Benchmark 10 finished ranking/reaction videos before bulk release.
- Measure successful-job rate, source quality, boundary accuracy, render time, Mac temperature, cost, approval rate, duplicate rate, claims/removals, and monetization status.
- Establish a quality-controlled backlog for rest days.
- Launch Channel 1 at a controlled cadence; scale output only when the spine remains stable.
- Mix ranking/reaction series with timely one-off reactions so the page does not appear mechanically identical.
- Do not launch Channel 2 until Channel 1 has bounded costs, stable production, two repeatable formats, and no unresolved platform issue.

#### YouTube channel operations

- Schedule uploads with configurable spacing; test the observed 45-minute cadence rather than treating it as a universal law.
- Publish useful daily Community posts when the channel has access and the practice remains sustainable.
- Surface comments requiring a timely human response; never deploy generic spam replies.
- Link each Short to the strongest relevant video/series where YouTube supports the relationship.
- Treat collaborations with another controlled or aligned channel as a measured growth experiment, not an automatic view-jail workaround.
- Diagnose low distribution through observable metrics—do not treat “view jail” or a proprietary TrustScore as an official YouTube state.
- Keep channel setup, niche consistency, originality/transformative contribution, account health, and monetization readiness in the launch checklist.

**Stage 2 victory:** LOOM converts promising topics into a repeatable stream-to-Shorts engine and safely publishes a measured Channel 1 backlog.

## 7. Stage 3 — Evidence-Led Editor

**Entry gate:** At least 100 candidates or enough production evidence to identify recurring defects.

### Segment 17 — Defect Ledger

Measure publishability, boundary failures, dead air, hook failures, crop/speaker failures, caption failures, render failures, and manual intervention time.

### Segment 18 — Boundary and Trim Improvements

Upgrade only when measured cutoff/dead-air defects justify it.

### Segment 19 — Hook and Packaging Engine V2

Test opening visual, first line, headline, curiosity, specificity, emotional tension, title, and thumbnail without misleading viewers. Benchmark OpenShorts' existing thumbnail capability and three LOOM template families against real channel-page/search click behavior before adding another thumbnail service.

### Segment 20 — Speaker, Layout, Caption, and Style Intelligence

Improve active-speaker treatment, camera/layout switching, caption timing, emphasis, safe margins, and brand styling based on measured defects.

### Segment 21 — Fast System Repair Actions

Allow bounded system-level correction/rerender actions. Do not introduce unlimited bespoke client revisions.

**Stage 3 victory:** LOOM fixes the defects evidence proves are costing retention, approvals, or labor.

## 8. Stage 4 — Cultural and Brand Editor

**Entry gate:** The core formats have earned repeatable performance.

### Segment 22 — Cultural Profiles

Define geography, language, age/community, platform norms, humor, vocabulary, sensitivities, and forbidden clichés.

### Segment 23 — Cultural Adaptation

Adapt hooks, captions, titles, examples, slang level, emotional framing, and calls to action without changing the source meaning.

### Segment 24 — Multilingual Output

Support English, Colombian Spanish, and bilingual variants; later add optional dubbing when justified.

### Segment 25 — Tenant Brand Voices

Control tone, energy, vocabulary, values, prohibited claims, CTA style, and platform differences.

### Segment 26 — Cultural QC

Flag awkward translation, forced slang, context errors, cultural mismatch, offense risk, brand mismatch, and loss of meaning.

**Stage 4 victory:** One strong moment can become multiple culturally native assets without becoming fake or generic.

## 9. Stage 5 — Learning, Distribution, and Business Intelligence

**Foundation timing:** Create the Supabase ledger before the first production publishing run. Begin vector RAG only after approximately 100 published posts or 30 days of outcomes.

### Segment 27 — Operational and Lineage Ledger

Minimum tables: `tenants`, `sources`, `jobs`, `clips`, `reviews`, `publications`, `performance_snapshots`, `topic_signals`, `experiments`, and `style_assets`.

Lineage chain:

`topic_id → source_id → source_moment_id → ranking_package_id → reaction_session_id → reaction_moment_id → final_clip_id → publication_id → performance_snapshot_id`

### Segment 28 — Performance Collection and Normalization

Collect views, viewed-versus-swiped, watch time, completion, replay, engagement, follows/subscribers, profile/Kick referrals, claims, monetization, revenue, leads, and sales. Normalize by platform, account size, post age, topic, format, duration, tenant, and posting time.

Also preserve campaign receivables separately from earned revenue: submission date, approval date, expected payout window, paid date, gross payout, platform fee, production cost, and net cash received. Delayed payouts must not be mistaken for available operating cash.

### Segment 29 — Source and Topic Intelligence Loop

Learn which topics, source channels, moment types, emotions, and freshness windows repeatedly earn attention.

### Segment 30 — Boundary, Editing, and Presenter Intelligence Loop

Learn which boundaries, hooks, templates, captions, layouts, SFX, presenter density, and human/TTS modes improve outcomes.

### Segment 31 — Retrieval-Augmented Decisions

- Supabase/Postgres stores facts.
- SQL summaries and explicit rules operate first.
- Later pgvector/RAG retrieves relevant history into Qwen; it does not train the base model.
- MCP may later expose the ledger to agents; MCP is an interface, not storage or learning itself.
- Significant policy, scoring, or publishing changes require human approval.

### Segment 32 — Campaign and Channel Intelligence

Group winners into recurring series, campaigns, audience journeys, offer paths, and channel expansion recommendations.

### Segment 33 — Business Intelligence and Reinvestment

Connect clip → attention → profile/Kick visit → lead → appointment → purchase → revenue. Reinvest only from measured returns into throughput, editors/VAs, additional channels, or overflow compute.

**Stage 5 victory:** Every publication teaches LOOM what to source, preserve, edit, present, schedule, and scale—and ties that learning to money rather than vanity alone.

## 10. Model and component policy

| Function | MVP choice | Upgrade rule |
|---|---|---|
| Search planning, scripting, scoring | `qwen/qwen3.5-9b` | Keep until a controlled benchmark proves a replacement improves output enough to justify cost |
| Transcription | OpenShorts/faster-whisper | Do not duplicate |
| Scene detection | OpenShorts/PySceneDetect | Replace only after recurring boundary defects are measured |
| Visual finalist analysis | Existing multimodal Qwen on sampled frames | Add another visual model only if it wins a bounded benchmark |
| Duplicate detection | Local perceptual hashing | No paid call |
| Narration | Bryan first; ElevenLabs v3 when valuable | Cache generated audio and cap characters |
| Composition | OpenShorts + FFmpeg + Remotion | Extend existing compositions |
| Style assets | Supabase Storage + local cache | Cloudinary remains removed |
| Approved media/publishing | GHL | Keep canonical during MVP |

No GitHub repository enters production merely because it is popular. It must fix a measured defect, avoid duplicating an installed capability, pass license/security review, fit the Mac/runtime budget, and win a relevant benchmark.

## 11. Cost envelope

- Local-first processing; heavy-render concurrency one.
- Callback-first orchestration eliminates production polling loops.
- Qwen structured text/scoring remains the default low-cost model path.
- Use transcript/scene filters before any visual model.
- Analyze sampled finalist frames, not every frame of every source.
- Cache sources, transcripts, hashes, TTS, and style assets.
- Human commentary avoids TTS cost where it also adds stronger identity.
- Ranking module benchmark targets:
  - Human-commentary lane: under $0.07 incremental cost per finished Short.
  - TTS-heavy lane: under $0.12 incremental cost per finished Short.
  - At 900 ranking Shorts/month, target the ranking module itself below roughly $60/month when human commentary dominates.
- These are engineering targets, not guaranteed invoices. Record actual spend before volume expansion.

## 12. Reusable style library

- Canonical private bucket: Supabase Storage `style-assets`.
- Mac: hash-addressed local render cache.
- Metadata: asset ID, tenant, type, emotion, intensity, duration, source/license note, hash, path, active status, and usage count.
- Starter target: 20–30 SFX, 10 transitions, 5 countdown animations, 5 overlays, 3 caption presets, and 3 ranking styles.
- RØDECaster 2 and Stream Deck cues can improve live sessions, but the post-production library remains available when cleaner timing is needed.
- Final approved/published video files remain in GHL.

## 13. Exact next execution

### Bentley mission 1 — narrow static preflight only

Inspect Workflows 00–03 and only the installed OpenShorts callback/signature fields needed to answer:

1. Does the installed version expose the native terminal callback, and what is its exact contract?
2. What is the smallest Workflow 03 change that removes continuous polling?
3. Does Workflow 02 preserve manual and sub-workflow entry routes?
4. Can any loop/error path submit `POST /api/process` more than once?
5. Are callback authentication, replay protection, `run_id`, `job_id`, and `job_id:clip_index` dedupe present?
6. Are Workflows 00–03 inactive?

No VOD, paid model call, broad repository audit, or workflow mutation in this mission.

### After Bryan approves the correction

1. Rotate previously exposed secrets and update vault references without pasting values.
2. Apply only the callback/exactly-once/dual-entry correction.
3. Static-review Workflows 00–03; keep them inactive.
4. Run one controlled Segment 3 end-to-end source.
5. Verify the Qwen 0–100 calibration and Discord candidate card.
6. Build/test 4C/4D and the Supabase decision record.
7. Begin GHL authentication only after the approval gate passes.

### Schedule scoreboard

| Stopping point | Judgment |
|---|---|
| Preflight only | Below today's target |
| Callback correction applied but no clean controlled run | Progress made; proof still below target |
| Segment 3 acceptance plus 4A/4B runtime verification | On schedule; successful day |
| 4C/4D decision gate and durable record pass | Ahead of schedule |
| Segment 5A begins after 4C/4D pass | Stretch-ahead |

## 14. Visual tracker requirements for Claude

Update the existing LOOM website artifact rather than redesigning its visual language.

- Header: **34 segments · 5 stages**.
- Stage tabs: **Factory Spine**, **Ranking + Reaction**, **Evidence Editor**, **Cultural Editor**, **Learning Engine**.
- Preserve the dark industrial design, amber active state, teal complete state, gray locked state, accordion cards, Decision Ledger, current-position panel, progress counters, and local single-file operation.
- Display evidence separately from status; for example, `COMPLETE` plus `RUNTIME VERIFIED`, or `CONSTRUCTED` plus `BENTLEY REPORTED`.
- Current position must point to **Stage 1 → Segment 3 → 3E Callback Correction**.
- Do not display Segment 4 as fully locked: mark it **IN PROGRESS / BLOCKED BY SEGMENT 3**, because the server exists and 4B is Bryan-reported.
- Add the three production lanes near the top.
- Segment 14 must show flexible ingestion; OBS cannot appear as mandatory.
- Add dependency/entry-gate text to Stages 2–5.
- Make subsegments individually checkable while preserving their evidence labels.
- Add editable notes and last-verified date fields.
- Add separate counters for runtime-verified, reported/static, pending, and deferred work.
- Do not calculate completion by treating reported construction as proof.

## 15. Final operating principle

Finish the spine before opening the throttle. LOOM's advantage is not merely volume: it is the combination of strong source selection, complete moments, Bryan's selective human presence, automated finishing, explicit review, low unit cost, and a feedback ledger that compounds. The system earns the right to scale one verified gate at a time.
