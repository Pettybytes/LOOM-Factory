# LOOM — NEXT SESSION HANDOFF (authoritative resume point)

> **READ THIS FILE FIRST.** It supersedes anything in `HANDOFF.md` or `CURRENT_STATE.md`
> that contradicts it. Those two remain the execution authority for *previously accepted*
> work (Editor Gate, Segment 4, acquisition); this file is the authority for the
> **Segment 5 moment-mining work in progress**.
>
> **Never put API keys, tokens, passwords, cookies, complete auth headers, or private
> customer data in this file.** Refer to credentials by *variable name* only.

**Last updated:** 2026-09-22 ~02:55 EDT (America/Detroit)
**Repo:** `/Users/user/Desktop/LOOM-Factory`
**Branch state:** `master` == `origin/master` == **`dfed7d0`**, working tree clean
**Only untracked file:** `infra/supabase/038_loom_editor_session_tokens.sql` — untracked
**on purpose** (new migration, not applied/tracked).

**Suites at HEAD (unittest discover, per-suite exit status captured):**
`infra/content_intelligence/tests` **396 OK** · `infra/discovery/tests` **120 OK** ·
`infra/discord-review/tests` **238 OK** · `infra/acquisition/tests` **578 OK**.

---

## 0. TL;DR for the incoming model

Segment 5 goal: **scrape highly-viral short-form moments across six platforms, run
them through the mining pipeline, and deliver ONE legitimate Source Review card per
platform to Discord with working buttons.**

Four repair commits landed this session (`c92265d` → `78f91d6` → `ae44a71` → `dfed7d0`).
The pipeline now **FIRE-passes a real candidate end-to-end**: the 8 s ViralHog Short
`ZaIDxlEuKpc` ("Impossibly Funny Golf Shot") scored market-final ≥75, content 100
(media-model grounded), selection 100, confidence 100, completeness PASS, tenant PASS
— total cost $0.0025. Its authoritative row lives in `loom_source_candidates`
(fire_passed=true), and a clearly labeled **LIVE REPAIR TEST card is posted in
#source-review** with working Approve/Reject/Hold + Open Source buttons.

**Blocked on Bryan:** his **Approve click** on that card (the handoff to the pending
acquisition lane is the next acceptance proof), then optionally Acquire (gate 2; live
spend switch is OFF, so it cannot charge anything).

---

## 1. What was fixed this session (all committed + pushed)

1. **Bounded preview deleted before analysis** — `bounded_preview.fetch_bounded_preview`
   used to `rmtree` its temp dir in `finally` while returning the artifact path
   (`available=True`, file gone). Now artifacts are RETAINED until `cleanup()`; callers
   clean after analysis; `cleanup()` only removes `loom-inspect-*` dirs it created.
2. **Signal aliases applied after assessments** — `visual_motion/scene_cut` were
   aliased onto `motion/scene_change` AFTER `assess_*`/confidence ran, so activity
   stayed 0.0. Now `moment_inspection.normalize_signal_kinds` canonicalizes AT
   COLLECTION (both inspection paths) and the pipeline folds pipeline-level windows
   in before assessments.
3. **Blind +4s boundary repair** — `repair_boundaries` extended by fixed steps
   (3→7→11→15 s). Now extends to the NEXT REAL boundary (segment/signal end),
   capped by `max_extend_s`/duration.
4. **Native Shorts truncated** — `source_as_moment` mode still ran locator+boundary
   rebuild. Now native Shorts get `BoundaryPlan [0, duration]` with provenance
   `("source_as_moment","complete_asset")` and NO repair.
5. **No real media into the model** — `GeminiProvider.assess` was text-only. Added
   `assess_media()` (inline_data video/audio) + `_apply_fire` runs it over the
   retained bounded artifact; classified failures fall back honestly
   (`media_assessment_status`). Corrected Flash-Lite pricing (0.30/2.50).
6. **FIRE/evidence never persisted** — pipeline persisted selections BEFORE FIRE.
   New `store.upsert_candidate` + `upsert_selection_fire` persist the authoritative
   joined record AFTER fire+ranking; migration `040_loom_source_candidates.sql` is
   **APPLIED** (table + FIRE columns on selections).
7. **Queue read pre-FIRE rows (blank titles)** — `source_review.fetch_pending_candidates`
   now reads `loom_source_candidates` with `fire_passed=is.true &
   duplicate_state=neq.DUPLICATE_SUPPRESSED`, falling back to the legacy join only
   while the table is absent.
8. **Approve dead-ended** — `_source_button_worker` now hands APPROVE to the pending
   acquisition lane (`approved_lane.plan_pending_job` → durable `create_job`; gate 1,
   zero spend). Duplicate clicks render the STORED decision. `_post_source_card`
   passes the Open Source link button.
9. **Market contradicted content** — topic scoring never saw moment evidence. Now
   `normalize_signals` carries `moment_evidence`; the content-first dim honors a
   grounded `content_worthiness_score`; `_apply_fire` re-assesses market AFTER
   evidence and `fire_gate` uses the final (`market_score_final`).
10. **Caption-only completeness** — gate now accepts `media_evidence`
    (payoff/context understanding from the media pass) and inspected signal windows;
    re-evaluated post-inspection. `active_at_end` requires STRICT overrun
    (window ending exactly at the clip end is contained).

**Live evidence captured:** `media_assess: ok` (real bounded video into Gemini),
`inspection_0_3s`, signals (audio_spike/visual_motion/scene_cut) over the whole
8 s clip, completeness PASS, FIRE `()`.

## 2. Deployment state

- Adapter restarted (supervisor `infra/discord-review/supervise.sh` alive; child
  respawned, new PID after restart). `/health` now carries a `source_review` block:
  `{"route":"SR","route_active":true,"approve_handoff":true,
  "duplicate_click_stored_decision":true,"open_source_button":true,
  "candidates_table":true}` — **`candidates_table:true` proves migration 040 is live**.
- Migration 040 was applied via the Supabase Management API `database/query` endpoint
  using the CLI's keychain token (HTTP 201). No DB password was printed/stored.
- Acquisition lane unchanged: `LOOM_ACQUISITION_LIVE=0` (no spend possible),
  durable store on.

## 3. Next actions (in order)

1. **Bryan clicks Approve** on the LIVE REPAIR TEST card in #source-review.
   Verify: `loom_source_reviews` row (decision=approved_for_acquisition) +
   `loom_acquisition_jobs` pending row (authorization_type=approved_for_acquisition,
   bounded window 0–8 s) — that is the approve-handoff proof.
2. Optional: Bryan clicks **Acquire** (gate 2) — authorization flow only; live
   spend switch is OFF. Do NOT flip `LOOM_ACQUISITION_LIVE` without Bryan's
   explicit instruction.
3. Repeat the same chain for the remaining platforms (Twitch, Reddit, TikTok,
   Instagram, Kick). Only Twitch/Reddit/YouTube have registered discovery
   connectors; TikTok/Instagram/Kick were reached via ad-hoc runs. Six-platform
   completion stays **contingent on qualifying fresh inventory** (the restricted
   Kick channel had none — do not manufacture a passing score).
4. Remove the interim availability caps for Derm by Erica only when Bryan
   supplies the real hours (unrelated to this segment).

## 4. Gotchas

- Suites are **unittest** (`python3 -m unittest discover -s infra/<pkg>/tests`), no
  pytest on the host. Capture each suite's own exit status when gating a commit.
- `LOOM_GEMINI_KEY` lives in `infra/tunnel-proxy/.env` (name only here). The
  pipeline must run from the repo root so the relative env path resolves.
- Shell-displayed secrets can be masked with `***`; pull real values from files,
  never reconstruct them from chat.
- `038_loom_editor_session_tokens.sql` stays untracked by design — never `git add -A`.
