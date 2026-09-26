# LOOM — NEXT SESSION HANDOFF

**Authoritative closeout: 2026-09-26, America/Detroit (session ended ~16:58 EDT).**
Supersedes the 2026-09-25 handoff. Documentation/continuity only — **no feature work was
performed in this closeout, and none should be assumed pending.**

## 0. START HERE — the next block is **MANUAL INTAKE FINALIZATION**

Two connected issues remain. This is the only authorized next block.

### A. Manual candidate pre-persistence filtering (canonical seam, not per-platform patches)

Real Manual Intake assets reached Content Intelligence and produced candidates, but some
candidates did not survive to durable persistence. Exact application messages shown to the
operator:

- `CI produced a candidate but its durable row was not found`
- `Content Intelligence produced a candidate but the durable row is absent — the candidate was filtered before persistence`

Seen on more than one real platform/asset. Treat as **ONE canonical manual-origin
persistence seam**, not separate Instagram/Kick fixes.

**Required semantic correction**

| Lane | Behaviour |
|---|---|
| Automated discovery | May keep filtering weak/non-qualifying candidates with its existing authorized logic. **Do not change.** |
| Manual Intake | Bryan deliberately supplied the source/media, so the intelligence result must stay **durable and reviewable** even when CI concludes: not recommended · no qualifying moment · low score · incomplete · intent mismatch. |

Manual origin changes **persistence/review semantics only**. It MUST NOT increase a score,
manufacture a qualifying moment, weaken Content Intelligence, or make poor content look good.

**Canonical rule: LOOM informs. Bryan decides.**

### B. URL-only Manual Intake (second ingress mode)

Cobalt works well for the TikTok/Instagram supplied-media workflow but does not reliably serve
Bryan's **Kick / Twitch / Reddit** workflow. Manual Intake therefore needs TWO modes:

| Mode | Flow |
|---|---|
| **A — Supplied media** (substantially exists) | file + source URL → durable media → CI → durable manual candidate → human review |
| **B — Source URL / LOOM resolution** (missing) | source URL → identify platform → **reuse EXISTING resolver/acquisition** → retrieve media → durable media → **SAME** CI → durable manual candidate → human review |

Both converge at `durable media available`, then `durable media → CI → candidate → review`.
**Do not** build separate downstream pipelines, and **do not** write new downloaders before
mapping what already exists (§7).

---

## 1. Repository / runtime state (verified, not assumed)

| Item | Value |
|---|---|
| Branch | `master` |
| HEAD | `0a99e4f4913118821d1e7aa4386cbd57141d0dc9` |
| `origin/master` | identical to HEAD |
| Worktree | clean except `?? infra/supabase/038_loom_editor_session_tokens.sql` |
| Preserved 038 | untracked, unmodified, SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0` — **do not touch** |
| Adapter | pid 98801, `127.0.0.1:8090`, health `ok`, managed by `infra/discord-review/supervise.sh` (`run.sh` sources the `.env` whitelist) |
| Acquisition | `LOOM_ACQUISITION_LIVE=0`; health reports `live_runner: disabled`. **No paid acquisition ran this session.** |
| Live channel bindings | `LOOM_MANUAL_INTAKE_CHANNEL_ID=1553487809422295061` (#manual-intake-📸) · `LOOM_MANUAL_REVIEW_CHANNEL_ID=1546266875225440397` (#clip-review) — verified present in the running service's env |

## 2. Commit history (verified with stats)

| Commit | What it established |
|---|---|
| `f519512` | 2026-09-25 closeout (rollback + six-platform new-lane run) — prior baseline |
| `8aca68b` | **Manual Intelligence Intake V1.2**: supplied-media ingress, GHL durable store, submission rows with `origin=manual_media`, CI invocation on supplied media, migration 041, prove-script repair, 9 files / +1188 |
| `5b9ec8c` | Routing correction: ingress bound by channel **ID**; manual candidates → `#clip-review`, never `#source-review` (+ regression guard) |
| `74add60` | Discord **ACK/defer** fix (type 5 for app commands, no network pre-ACK), `run.sh` env whitelist, service-runtime import bootstrap, retry-safe media reuse |
| `ec226a3` | Card renders the analyzer's real outcome (not "Unavailable"); `media_assessment` mapped through; manual approvals labelled **Approve Clip**; manual-lane intent corrected |
| `0a99e4f` | **Age gate removed entirely** (current baseline) |

## 3. What is RUNTIME PROVEN (with evidence)

- Discord **`#manual-intake` exists** and ingress is **bound by channel ID**.
- **Supplied-media ingress works**; **no network work occurs before the ACK**; app-command
  **ACK/defer defect fixed** (type 5); service-runtime **import bootstrap** and
  **env/channel-binding** defects fixed.
- **GHL/filesafe durable media upload AND retrieval** work (real byte-identical retrieval).
- **Submission persistence** works: `origin=manual_media` with durable `media_reference`.
- **Content Intelligence consumes supplied media** (local ffmpeg inspection:
  `inspection_attempts.method=local_supplied_media`).
- **Operator boundaries remain authoritative when supplied**; machine analysis runs when they
  are absent (`boundary_provenance` = `manual_intake` | `machine`).
- **Supplied media bypasses unnecessary acquisition** — approve handoff returns
  *"Media already stored at intake — no acquisition needed"* and plans **zero** jobs.
- **Retry/dedup:** identical bytes reuse the existing durable copy (`durable media reused
  (same bytes)` in the log).
- **Analyzer findings survive to inspection**, and the **review-card rendering defect is fixed**
  (states the real outcome + model reasons instead of "Unavailable").
- **Age/adult gate completely removed**; no replacement attestation added.

## 4. Product decisions (FINAL unless Bryan changes them)

- **No LOOM age/adult qualification gate** for source mining or Manual Intake. Do **not**
  reintroduce `adults_only`, adult attestation, `Confirm 18+`, machine age veto, ambiguous-age
  rejection, or age-based suppression. Analyzer age inference is **no longer requested**.
  Regression guard: `infra/content_intelligence/tests/test_age_gate_removed.py`.
- **Manual vs automated semantics** (§9) — manual candidates stay reviewable.
- **Operator UX** (§10) — the operator supplies only: media (or URL), optional tenant, optional
  start/end. LOOM infers topic/hook/payoff/moment type/angle.
- Authorized criteria remain: `intent_match`, `required_concepts`, `creator_identities`,
  `nudity`/`explicit_sex`, directly-observed event, timed payoff, market/selection logic.

## 5. Real durable acceptance assets (reuse — do NOT ask Bryan to re-upload)

| Asset | Submission id | Durable media (filesafe) | sha256 | Dims/dur | Bytes | Fingerprint | Candidate today |
|---|---|---|---|---|---|---|---|
| **TikTok** `@midflightclips/video/7667225763720023327` | `d6caf284-746c-420d-ad6a-23e22ffb7c46` | `…/media/587de683-3687-4f00-bca9-128cfdf9df00.mp4` (file_id `6ab820a03dbd5f2bbbefc6b7`) | `8ef6886f801f30a2…` | 720×1280 / 12.03 s | 2,042,229 | `0829b8d1e458b8f1…` | **PERSISTED** (status `scored`, content 0, completeness FAIL, `inspected_intent_mismatch,no_directly_observed_event`, no boundary) · jobs **0** |
| **Instagram** `instagram.com/reels/DdvnP_rqcQ0` | `41008b38-acdd-4c6b-949f-200433742826` | `…/media/d6f62ce6-e666-4461-aee7-e0ad679f77ce.mp4` (file_id `6ab82a95974a9da6eee45686`) | `5d8ed3c5895f629f…` | 720×1280 / 30.09 s | 9,249,951 | `8b5686959b981817…` | **PERSISTED** under a **different** fingerprint `13c3bca765b2ab2d…` (status `scored`, **content 68**, completeness **PASS**, boundary present, `MARKET_GATE_FAILED,LOW_CONTENT,LOW_SELECTION,LOW_CONFIDENCE`) · jobs **0** |
| **Kick** `kick.com/deenthegreat/clips/clip_01M33XASMP29XTQ1QK0EV7TXDC` | `613843c0-6af2-4f1a-9f1b-82b319372582` | `…/media/7a59b60c-0db0-4b5b-9d41-fbef947a8763.mp4` (file_id `6ab82eef1f3be2be1bd532c0`) | `e3844b9996b898de…` | 1920×1080 / 33.01 s | 19,419,451 | `c21aff113f5d7978…` | **NO candidate row.** Log: `done fingerprint=c21aff113f5d ci_ok=False` |

All three are submitter `passportpetty`, `origin=manual_media`, platform `accepted_pending_platform_support`.
Also on record (proof runs): Twitch `2768903e…` (boundary `manual_intake`, window 1–4 s) and
TikTok `d34a6022…`.

## 6. The persistence defect, stated precisely

It is **not** "Content Intelligence didn't run" — CI ran (evidence: Instagram candidate has
content 68 + a boundary; TikTok/Instagram both have `media_assessment`). The seam is:

```
CI candidate produced → qualification/filter → candidate does not survive persistence
→ Manual Intake consumer looks for the durable candidate → durable row absent
```

Two distinct causes are already identified and must not be conflated:

1. **Persistence gate** — `infra/content_intelligence/pipeline.py:472`: a candidate is written
   **only when `status == "scored"`**; a store failure rewrites it to `status="error"` with
   `error="persist_candidate: <Exc>"`. Any manual candidate that is filtered before that point
   produces no durable row at all.
2. **Identity/lookup mismatch (fixed in `ec226a3`, but note the residual)** — the pipeline
   assigns its **own** candidate fingerprint per materialized moment, so the submission
   fingerprint is not the candidate key; lookups now resolve by `platform_content_id`.
   **Residual defect:** `manual_intake._extract_content_id` for **kick** matches only
   `/clip/` (singular), so Bryan's `/clips/<id>` URL produced an **empty**
   `platform_content_id` — which alone can produce "durable row absent" even when a row exists.

**Required discipline (use it):** `EXISTS ≠ CONNECTED ≠ EXECUTED ≠ PERSISTED ≠ CONSUMED`, plus
`written ≠ consumed` · `returned ≠ persisted` · `persisted ≠ rendered` · `rendered ≠ actionable`
· `actionable ≠ durable`.

**Trace this exact chain:** manual ingress → submission → durable media → CI → analyzer result →
normalization → qualification → filtering → persistence decision → DB write → candidate lookup →
Discord render.

## 7. URL-only intake: map what exists first (do NOT write new downloaders)

The container must **derive the platform from the URL** (no per-platform Discord command
explosion) and reuse existing LOOM resolution/acquisition. Prior work *claims* capability
around: YouTube exact URL · Reddit · Twitch clips · Twitch bounded VOD windows · Kick clips ·
TikTok provider route · Instagram provider route. **Treat that list as CLAIMED, NOT VERIFIED —
inspect current repo/runtime truth before relying on it.**

Known trap: **Kick clip acquisition ≠ Kick ranged-VOD acquisition.** Do not conflate them.

Desired operator model: TikTok/Instagram → prefer Cobalt → supplied media (avoids provider
cost); Reddit/Twitch/Kick → supply the URL and let LOOM resolve; YouTube → either mode.

## 8. Cost discipline

Supplied media must not trigger unnecessary paid acquisition. Prefer an existing local/free
authorized resolver; never silently use a paid provider when a free authorized route exists;
preserve provider/cost provenance. **Do not** add CapCut or any new downloader subscription for
this requirement. The goal is not "zero rows" — it is: no redundant acquisition decision, no
duplicate media, no unnecessary provider spend, no accidental automated pipeline behaviour.

## 9. Manual vs automated semantics (must survive)

- **Automated discovery:** LOOM finds volume and may filter weak candidates with authorized
  qualification/ranking before Bryan sees them. **Do not break this.**
- **Manual Intake:** Bryan intentionally gives LOOM the content. LOOM analyses honestly but must
  not make the item vanish merely because CI doesn't recommend it. Desired state:
  **`UNDERSTOOD + NOT RECOMMENDED`** — Bryan still decides.
- Manual persistence must not contaminate automated discovery semantics.

## 10. Discord operator experience (desired end state)

The operator must **not** be required to supply topic, why-selected, hook, payoff, moment type,
reaction angle, or category — Content Intelligence infers them. Optional inputs: tenant,
start timestamp, end timestamp. No timestamps → LOOM attempts machine boundary detection.
Timestamps supplied → operator boundaries preserved with manual provenance while machine
analysis remains evidence. Manual Intake must stay **fast**.

## 11. GHL / durable media — honest state

Durable **upload and retrieval are proven**. Folder targeting is **not**: the `POST /medias/folder`
call created `Intake Reviews` under `LOOM`, but the upload endpoint did not reliably place media
inside it, so media lands **unfiled yet durable and retrievable**. Record this as a **bounded
UX/storage issue**; it is **not** a Manual Intake blocker. Do not claim folder targeting works
unless it is runtime proven.

## 12. Editor state — do not lose this

`CURRENT_STATE.md` carries a standing lock: *"Editor acceptance is complete and locked. Do not
re-audit, re-test, or add Editor feature work."* **That lock is preserved, not removed.**
Distinction: the *previous* editor acceptance remains locked; Bryan intends to authorize a
**new/missing timeline-editor capability** *after* Manual Intake is finalized. Do not resurrect
Discord Activity editor architecture, old component-limit discussions, or abandoned approaches;
the editor already moved to an external LOOM editor link. The future work is the missing
**timeline** editing functionality. **Do not start or reopen it without explicit authorization.**

## 13. Launch discipline (September 30 boundary)

Order: **finalize Manual Intake → timeline editor capability → already-authorized launch
spine / Segment 6 work.** Do not insert speculative infrastructure between these items, and do
not let this handoff drift into an unlimited build.

## 14. Locked / proven summary

Locked: Editor Gate + Segment 4 + cross-platform acquisition (do not re-audit); automated
discovery qualification behaviour; the preserved migration 038; age-gate removal.
Proven this session: see §3.

## 15. Known open defects (all genuinely unresolved)

1. **Manual candidate pre-persistence filtering** (§6) — the canonical seam to fix.
2. **URL-only Manual Intake missing** for the Kick/Twitch/Reddit operator workflow (§7).
3. **Kick `/clips/` content-id extraction** returns empty → candidate cannot be linked (§6.2).
4. **GHL folder targeting** not honoured by the upload endpoint (§11) — bounded, non-blocking.

## 16. NEXT EXECUTION BLOCK

Do **not** begin by coding. In order:

1. read `CURRENT_STATE.md` → 2. read this handoff → 3. verify HEAD/worktree →
4. inspect the targeted Manual Intake persistence path → 5. confirm the real Instagram/Kick
durable assets are still available (§5) → 6. trace the pre-persistence filter →
7. fix the canonical manual-origin persistence seam → 8. regression-prove automated discovery is
unchanged → 9. map existing platform resolvers → 10. implement URL-only intake by **composing**
them → 11. runtime-prove representative Reddit/Twitch/Kick URL intake → 12. verify TikTok/
Instagram supplied-media behaviour is intact → 13. prove at least one real **`Approve Clip`**
decision becomes durable → 14. close Manual Intake only when evidence supports it.

**Summary: Fix canonical Manual Intake persistence seam → compose existing resolvers into
URL-only Manual Intake → runtime acceptance → close Manual Intake.**

Bryan will supply the detailed implementation prompt next session. **Do not invent additional
scope before receiving it.**

## 17. DO-NOT-REDO list

Do not: rebuild Manual Intake from scratch · re-audit the age gate · add adult attestation ·
reintroduce age inference · redo the Discord ACK/defer work · resurrect Cloudflare tunnel work ·
assume the latest Discord failure is Tailscale/ingress related without evidence · rebuild GHL
durable media upload · write new platform downloaders before checking existing resolvers · force
TikTok/Instagram through paid acquisition when supplied media exists · reopen automated discovery
architecture unnecessarily · weaken CI thresholds to make acceptance pass · make manual
candidates look artificially good · resurrect Discord Activity editor work · touch
`038_loom_editor_session_tokens.sql` · start the timeline editor before Bryan authorizes it.

## 18. Test / evidence state

`infra/content_intelligence` suite **477 OK** · `infra/discord-review` suite **302 OK** ·
age-gate regression **6 OK**. Runtime artifacts from this session:
`/tmp/loom-v12/` (`v12-proof-report.json`, `reenrich-report.json`, `ghl-contract-report.json`).
Repo proof: `MANUAL_INTAKE_ACCEPTANCE_20260926.md`, `SEGMENT5_NEW_LANES_20260925.md` (historical).
