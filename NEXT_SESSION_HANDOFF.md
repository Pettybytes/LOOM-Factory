# LOOM — NEXT SESSION HANDOFF (authoritative resume point)

> **READ THIS FILE FIRST.** It supersedes anything in `HANDOFF.md` or `CURRENT_STATE.md`
> that contradicts it. Those two remain the execution authority for *previously accepted*
> work (Editor Gate, Segment 4, acquisition); this file is the authority for the
> **Segment 5 moment-mining work in progress**.
>
> **Never put API keys, tokens, passwords, cookies, complete auth headers, or private
> customer data in this file.** Refer to credentials by *variable name* only.

**Last updated:** 2026-09-22 ~03:30 EDT (America/Detroit) — session closeout
**Repo:** `/Users/user/Desktop/LOOM-Factory`
**Branch state at closeout:** `master` == `origin/master` == **`d61df9e`**, working tree clean
**Only untracked file:** `infra/supabase/038_loom_editor_session_tokens.sql` — untracked
**on purpose** (new migration, not applied/tracked; do NOT `git add -A`).

**Suites at HEAD (unittest discover, per-suite exit status captured):**
`infra/content_intelligence/tests` **396 OK** · `infra/discovery/tests` **120 OK** ·
`infra/discord-review/tests` **238 OK** · `infra/acquisition/tests` **578 OK**.

---

## 0. TL;DR for the incoming model

Segment 5 goal: **scrape highly-viral short-form moments across six platforms, run
them through the mining pipeline, and deliver ONE legitimate Source Review card per
platform to Discord with working buttons.**

Five commits landed this session (`c92265d`, `78f91d6`, `ae44a71`, `dfed7d0`, `d61df9e`)
repairing the defects Astra's diagnosis identified (§1/§2). The pipeline now runs a real
candidate end-to-end: the 8 s ViralHog Short `ZaIDxlEuKpc` ("Impossibly Funny Golf
Shot") FIRE-passed with real media evidence and persisted to `loom_source_candidates`
(fire_passed=true, completeness PASS, media_assessment ok, boundaries [0, 8]). A clearly
labeled **LIVE REPAIR TEST card** is posted in #source-review with working buttons.

**Bryan's closeout directions (obey these):**
1. **NOT niche acceptance:** the required YouTube lane is **funny dog moments**; the
   accepted Short was golf content. Diagnose tomorrow why discovery/query/tenant-fit
   enforcement allowed it (§3.1).
2. **Card did not display the 0–8 s window** — authoritative persisted boundaries must
   render on every Source Review card (§3.2).
3. **Approve click: ACKed but durable result MISSING.** Bryan reported the Approve
   click; a Discord interaction arrived 03:27:01 EDT and got HTTP 200, but NO
   `loom_source_reviews` row and NO `loom_acquisition_jobs` row exist (checked three
   times read-only), the card was NOT updated, and the adapter log shows NO worker
   output or traceback. **The acquisition chain did NOT pass — do not claim it did.**
   Diagnose the silent worker failure tomorrow (§3.3) before re-running the click.
4. Preserve the successful proof (§2.1): native Short well formed, received real
   media analysis, qualified, persisted, rendered with working controls.
5. No platform expansion, no spending, no new searches/model calls/scraping/cards/
   migrations/implementation after the closeout instruction.

---

## 1. What Astra's diagnosis identified (the issue list this session repaired)

- Bounded preview deleted before analysis (available=True, file gone).
- Hook/activity assessments ran BEFORE signal aliases attached (activity always 0.0).
- Native Shorts labelled `source_as_moment` still went through moment mining and
  blind boundary repair (3→7→11→15 s on a 120 s source; preview cap is actually 12 s).
- No real video/audio ever reached the Gemini provider (text-only assess).
- FIRE/completeness results lived only in memory; Discord read pre-FIRE selection
  rows → blank titles, no FIRE enforcement.
- Market scored from title/platform metrics before content analysis, contradicting
  the advertised evidence policy; FIRE (M75/C80/S85/Conf85) was unreachable with
  honest first-observation data.
- Source Review Approve saved a decision but did not hand off to acquisition.
- Duplicate clicks rendered the attempted decision, not the stored winner.
- Open Source button existed in the formatter but was never attached.
- Discovery normalization dropped TikTok `playCount` / Instagram `videoPlayCount`;
  only 3 of 6 platforms had registered connectors.

## 2. What was repaired (all committed + pushed, suites green)

1. `bounded_preview`: artifacts retained until `cleanup()`; cleanup only removes
   `loom-inspect-*` dirs it created (foreign-path safe).
2. `moment_inspection`: `normalize_signal_kinds` canonicalizes `visual_motion/
   scene_cut` → `motion/scene_change` AT COLLECTION; captions scoped to the
   inspected interval; `populate_from_inspection` crash fixes (hook.value None,
   missing emotion key).
3. `moment_quality`: `repair_boundaries` extends to the NEXT REAL boundary (no
   blind +4 s); `completeness_gate` accepts `media_evidence` (payoff/context
   understanding) and inspected signal windows; `active_at_end` requires STRICT
   overrun of the clip end.
4. `pipeline`: native Shorts get `BoundaryPlan [0, duration]`
   (`source_as_moment` + `complete_asset` provenance), no repair; signal windows
   folded before assessments; media-aware model pass over the retained bounded
   artifact (`media_assessment_status`); authoritative persistence AFTER
   FIRE + ranking; market re-assessed after content evidence (`market_score_final`
   feeds `fire_gate`); evidence-informed selection persisted.
5. `providers`: Gemini `assess_media()` (inline_data), corrected Flash-Lite pricing
   (0.30/2.50 $/M tokens), env-file handle closed.
6. `store_supabase`: `upsert_candidate` + `upsert_selection_fire` →
   `loom_source_candidates` (migration `040_loom_source_candidates.sql`, APPLIED
   via Supabase Management API; adds FIRE columns to selections too).
7. `source_review`: queue reads the authoritative table
   (`fire_passed=is.true & duplicate_state=neq.DUPLICATE_SUPPRESSED`), legacy
   fallback only while the table is absent; `boundary_plan` rides the candidate map.
8. `adapter`: Approve hands off to the pending acquisition lane
   (`approved_lane.plan_pending_job` → durable `create_job`; gate 1, ZERO spend);
   duplicate clicks render the stored decision; `_post_source_card` passes the
   Open Source link button; `/health` exposes a `source_review` readiness block
   including live `candidates_table` state.
9. `discovery/normalization`: platform-aware volume keys (playCount/videoPlayCount);
   `moment_evidence` passthrough; topic content-first dim honors a grounded
   `content_worthiness_score`.
10. Regression tests for every fix (`test_repairs_regression.py`,
    `test_source_review.py` additions, `test_moment_quality.py` reconciliation).

### 2.1 Successful proof to preserve (do not re-run, do not discard)

`ZaIDxlEuKpc` "Impossibly Funny Golf Shot" (ViralHog, 8 s, 24,653 views, 318 likes,
7 comments, published 2026-09-17): real metadata → real bounded preview (yt-dlp
`--download-sections`) → real signals (audio_spike/visual_motion/scene_cut over the
full clip) → real Gemini media assessment (`media_assess: ok`) → market-final ≥75,
content recalibrated 100, selection 100, confidence 100, completeness PASS,
tenant PASS → FIRE `()` → persisted `loom_source_candidates` (fire_passed=true,
DISTINCT) → rendered card with working buttons. Total model cost ~$0.0025. This
proves the repaired EVIDENCE chain; it does NOT prove niche acceptance (wrong
lane — §3.1).

## 3. UNRESOLVED ACCEPTANCE DEFECTS (tomorrow's work)

### 3.1 Wrong niche: golf content on the funny-dog-moments lane (NOT niche acceptance)

- The candidate passed tenant fit via the generic "funny" term, which is NOT
  lane-specific. Diagnose: (a) why discovery query / seed vocabulary /
  tenant-fit universe does not enforce the **per-lane niche** (funny dog moments)
  rather than broad "funny"; (b) whether `STRONG_MOMENT_TERMS` head-word indexing
  is too coarse and must be lane-qualified; (c) whether the source brief
  topics/keywords need a lane contract. **Do not count the golf card as niche
  acceptance.**

### 3.2 Card did not display the 0–8 s window

- The embed renders `suggested_timestamps` from `boundary_plan.start_anchors`,
  which is `[]` for native Shorts — the authoritative `recommended_clip_start/
  recommended_clip_end` (0.0 / 8.0) is NOT rendered. Fix: every Source Review
  card must show the persisted boundary window (e.g. "Clip window 00:00–00:08")
  sourced from `loom_source_candidates.boundary_plan`. Add a regression test:
  a candidate with `boundary_plan [0,8]` renders the window on the card.

### 3.3 Approve click: ACKed, durable result MISSING (silent worker failure)

**Verified facts (read-only, ~03:28–03:30 EDT):**
- Bryan reported clicking Approve on the LIVE REPAIR TEST card.
- A Discord interaction POST arrived at the adapter 03:27:01 EDT and was answered
  HTTP 200 (the type-6 ACK).
- `loom_source_reviews` has NO row for `ZaIDxlEuKpc` (decision absent).
- `loom_acquisition_jobs` has NO row for `ZaIDxlEuKpc` (handoff absent).
- The card in #source-review is UNCHANGED (still "SOURCE REVIEW · HOLD
  UNASSIGNED"; no "Decision recorded" and no error patch).
- Adapter log shows NO `[source-review]` worker output and NO traceback after the
  ACK (the only tracebacks in the log are older "Address already in use" startup
  lines).
- Leading hypothesis: `_source_button_worker` computes
  `reviewer = self._reviewer_name(interaction)` OUTSIDE the try block, so any
  exception there kills the thread silently (no print, no patch, no rows).
  Verify tomorrow with a captured interaction fixture (which fields the worker
  receives), then move that line inside the try (or make the worker's first
  line a guarded default) and add a regression test asserting a durable row OR
  a visible failure message on EVERY decision path.
- **Do not claim the acquisition chain passed until a fresh click produces the
  verified rows (review + pending job).**

## 4. What's next (after the defects above)

1. Fix §3.3 (approve worker) — it blocks everything else. Re-click Approve on the
   existing test card, verify review row + pending gate-1 job (window 0–8 s,
   `LOOM_ACQUISITION_LIVE=0` → no spend possible).
2. Fix §3.1 (per-lane niche enforcement) BEFORE accepting ANY platform card as
   niche acceptance.
3. Fix §3.2 (boundary window on every card).
4. Optional: Acquire (gate 2, authorization flow only while the live switch is off).
5. Repeat the chain per platform: Twitch/Reddit (registered connectors),
   TikTok/Instagram/Kick (ad-hoc; only accept genuinely qualifying fresh
   inventory — the restricted Kick channel had none; never manufacture a pass).

## 5. Deployment state (unchanged at closeout)

- Adapter running under `infra/discord-review/supervise.sh` (production service,
  left RUNNING). `/health` carries the `source_review` block with
  `candidates_table: true` (migration 040 live).
- Migration 040 applied (Supabase Management API `database/query`, HTTP 201).
- `LOOM_ACQUISITION_LIVE=0` — no paid acquisition possible; nothing paid is
  running. No LOOM test processes were left running (all proof runs completed;
  the adapter + supervisor are the only LOOM processes and are production).

## 6. Gotchas / trouble the next session should know

- Suites are **unittest** (`python3 -m unittest discover -s infra/<pkg>/tests`);
  no pytest on the host. Capture each suite's own exit status when gating commits.
- `LOOM_GEMINI_KEY` lives in `infra/tunnel-proxy/.env` (name only here). Run the
  pipeline from the repo root so the relative env path resolves.
- Shell/chat masking rewrites `"apikey": <secret-var>` inside heredocs to `***`
  (SyntaxError). Write one-off Python to a file with the write tool instead of a
  heredoc when it touches headers.
- Supabase CLI token is in the macOS keychain (service "Supabase CLI", account
  "supabase", go-keyring-base64 wrapped). Management API `database/query` can run
  DDL with it — used once for migration 040; do not reuse without explicit
  authorization.
- `038_loom_editor_session_tokens.sql` stays untracked by design.
- The `loom_source_candidates` row for `ZaIDxlEuKpc` is the durable proof row;
  replaying the pipeline with the same fingerprint updates it (idempotent).
- Editor Gate, Segment 4, and cross-platform acquisition remain ACCEPTED and
  locked — do not re-audit or revert (see CURRENT_STATE.md).
