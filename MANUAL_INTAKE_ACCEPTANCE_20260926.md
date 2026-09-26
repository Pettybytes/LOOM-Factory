# MANUAL INTAKE — LIVE ACCEPTANCE RE-RUN, 2026-09-26

Real asset (operator-supplied via Discord, Cobalt-downloaded):
`https://www.tiktok.com/@midflightclips/video/7667225763720023327` · submitter `passportpetty`
· durable media `…/media/587de683-3687-4f00-bca9-128cfdf9df00.mp4` · sha256 `8ef6886f801f…`
· 720×1280 · 12.03 s · 2,042,229 bytes · submission `d6caf284-746c-420d-ad6a-23e22ffb7c46`.

**Why this document exists:** the first traversal looked self-contradictory — a coherent
reaction angle on the card, but Moment / Why selected / Hook / Payoff rendered
"Unavailable" and scores 0. The chain was traced end to end; three defects were found
in *our* seams and fixed; the acceptance was then re-run on the SAME durable media
(retrieved from our own GHL copy — no platform reacquisition, no re-upload).

## Root cause (proven, not inferred)

| Layer | Evidence | Verdict |
|---|---|---|
| Ingress → submission | `loom_manual_submissions` row, `origin=manual_media` | worked |
| Durable media | filesafe URL + sha256 + size stored | worked |
| Supplied-media inspection | `inspection_attempts[0].method=local_supplied_media`, 0–12.03 s, signals `audio_spike`,`motion` | worked |
| Analyzer | `media_assessment.scores` (14 dimensions) + `reasons` (`LOW_NARRATIVE`, `NO_DIALOGUE`) + `usage` | worked |
| Normalization | `moment_evidence`: `reaction_angle`, `qualification.concept_evidence` ("outfit showcase"), `exact_timestamps` | worked |
| **Card render** | `Moment`/`Why selected`/`Hook`/`Payoff` read keys that are null when there is no qualifying moment → "Unavailable" | **DEFECT (fixed)** |
| **Lane intent** | `manual_brief` intent was the phrase "operator-supplied source for review" | **DEFECT (fixed)** |
| **Action naming** | manual approve labelled "Approve for Acquisition" | **DEFECT (fixed)** |

**Content verdict (unchanged, honest):** Content Intelligence found **no qualifying
moment** — model reason verbatim: *"The video shows a standard fashion/outfit showcase
with no distinct incident or conflict."* `completeness=FAIL`, content 0, selection 0,
confidence 33, no boundary. This is a genuine rejection, **not** a seam failure.

## Fixes (this seam only; no gate weakened)

`74add60` (ack/defer + service runtime) and this commit:
1. The card now renders the analyzer's real outcome: "No qualifying moment identified",
   the model's own reasons, and an **Analyzer findings** field (observed concepts +
   dimension scores), instead of presenting analysis as missing data.
2. Manual-lane intent is "operator-supplied content for human review".
3. Manual approvals are labelled **"Approve Clip"**; discovered candidates keep the
   acquisition wording. The action itself is unchanged and still creates zero jobs.
4. `media_assessment` is mapped through to the card contract.

## Acceptance results

| # | Criterion | Result |
|---|---|---|
| 1 | Ingress reaches adapter and acknowledges within the deadline | **PASS** (type-5 defer; no network before it) |
| 2 | Submission durable, `origin=manual_media`, tenant correct | **PASS** |
| 3 | Media durably stored, retrievable, byte-identical | **PASS** (sha match on retrieval) |
| 4 | Content Intelligence executes on supplied media | **PASS** |
| 5 | Analyzer understanding preserved (not lost) | **PASS** (scores + reasons + concepts durable) |
| 6 | Card renders the real outcome, not "Unavailable" | **PASS** (verified on the regenerated card) |
| 7 | Manual action labelled "Approve Clip" | **PASS** |
| 8 | Approval creates **no** acquisition job | **PASS** (0 jobs; handoff short-circuits) |
| 9 | No reacquisition on re-run | **PASS** (retrieved our durable copy only) |
| 10 | No duplicate upload on identical bytes | **PASS** (reuse guard; unit-tested; re-run uploaded nothing) |
| 11 | Candidate qualifying as a moment | **FAIL — honest content rejection** (no qualifying moment) |
| 12 | Age gate present anywhere on the decision path | **PASS — removed** (`adult_status: null`, zero age tokens on the durable row; see the canonical rule below) |

## Canonical rule (2026-09-26, owner directive)

The `adults_only` / machine age-inference gate was **never a product requirement** and has been
removed as a qualification / eligibility / routing / discovery / mining / Manual Intake /
candidate / approval / scoring / ranking / persistence / review-eligibility / acquisition gate.
It was **not** replaced by age attestation. Analyzer age metadata has no authorized consumer and
is no longer requested; legacy `adults_only` keys in stored briefs are non-authoritative.

**LOOM informs. Bryan decides.** For manually supplied content: Bryan already chose to bring it
into LOOM; LOOM analyzes it; Bryan decides what happens next.

## Resolved decision (owner directive, 2026-09-26)

`adults_only` was **not** a product requirement. It has been removed entirely, and the
alternative (operator age attestation) was explicitly declined. The canonical rule now lives in
`NEXT_SESSION_HANDOFF.md`, with regression protection in
`infra/content_intelligence/tests/test_age_gate_removed.py`. Post-removal runtime re-run on this
same durable asset: `adult_status: null`, `rejection_reason: inspected_intent_mismatch,
no_directly_observed_event`, zero age tokens — and the content-quality verdict (no qualifying
moment) is unchanged, proving the removal did not alter unrelated scoring.

## Tests

8 new card-render/naming tests; 39 intake/ingress tests; full discord-review suite **302 OK**.
