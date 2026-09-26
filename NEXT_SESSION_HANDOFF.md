# LOOM — NEXT SESSION HANDOFF

**Authoritative closeout: 2026-09-26 (session ended ~19:30 EDT).**
Supersedes the 2026-09-25 handoff and the 2026-09-26 Manual-Intake closeout.
**Read this file first.** `HANDOFF.md` defers to it; `CURRENT_STATE.md` is the execution authority
and carries the long-form history.

---

## 1. Repository state (verified, not assumed)

| Item | Value |
|---|---|
| Branch | `master` |
| Final pushed commit | **`c7eaedd0acf74f991d510d0ad54cb85f073c6c53`** (engagement-metadata increment, §10) |
| Previous code commit | `925a8343062e382b540ef20795fa2e2d8805bef9` |
| `origin/master` | identical to HEAD — `HEAD == origin/master` proven |
| Ahead / behind | `0 / 0` |
| Expected worktree | clean **except** one intentionally preserved untracked file |
| Preserved file | `infra/supabase/038_loom_editor_session_tokens.sql` — untracked, **do not add, do not touch** |
| Its SHA1 | `3da12826ef96dd05ce089b490ee490e66baf01c0` |

Re-verify with:
```
git rev-parse --abbrev-ref HEAD && git rev-parse HEAD && git rev-parse origin/master
git status --porcelain && shasum infra/supabase/038_loom_editor_session_tokens.sql
```

---

## 2. Runtime state (no secrets)

| Item | Value |
|---|---|
| Discord adapter | `infra/discord-review/adapter.py` (host process) |
| Supervision | `infra/discord-review/supervise.sh` → `run.sh` (sources the `~/.openclaw/.env` whitelist); stdout/stderr → `adapter.log` |
| Bind | `127.0.0.1:8090` |
| Health check | `curl -s http://127.0.0.1:8090/health` → `{"status":"ok", ...}` |
| Restart | `pkill -f adapter.py` — the supervisor relaunches it within ~3s. **Always restart after editing adapter/source-review code**: a stale process silently serves old code and previously made a real click look like a no-op. |
| Paid acquisition | `LOOM_ACQUISITION_LIVE=0` — manual intake must never need it |
| Manual intake channel | `LOOM_MANUAL_INTAKE_CHANNEL_ID` (`#manual-intake-📸`) |
| Review channel | `LOOM_MANUAL_REVIEW_CHANNEL_ID` (`#clip-review`) |

---

## 3. What is LIVE PROVEN (operator acceptance)

**Bryan personally drove every one of these through the real Discord interface.** This was not an
in-process test, fixture, simulated click, or Bentley-submitted transaction.

| # | Path | Result |
|---|---|---|
| 1 | Reddit — URL intake | **LIVE PASS** |
| 2 | Reddit — downloaded/file intake | **LIVE PASS** |
| 3 | Twitch — URL intake | **LIVE PASS** |
| 4 | Twitch — downloaded/file intake | **LIVE PASS** |
| 5 | Kick — URL intake | **LIVE PASS** |
| 6 | Kick — downloaded/file intake | **LIVE PASS** |
| 7 | Instagram — URL intake | **LIVE PASS** |
| 8 | TikTok — URL intake | **LIVE PASS** |

Do **not** claim platform/input-mode combinations beyond this table.

### Proven lifecycle
```
operator-supplied source → #manual-intake → intake-media → ingestion
→ durable GHL media → Content Intelligence (advisory) → durable candidate/review object
→ exactly one active review card → human decision → durable loom_source_reviews row
→ same Discord card visibly reflects the decision
→ manual media is not downloaded again after approval
```

The approved manual-media path correctly reports:
*"Media already stored at intake — no acquisition needed (nothing to download)."*
That behaviour is **intentional**.

---

## 4. ACCEPTED SEMANTICS — preserve these

**Manual intake is HUMAN-ARBITRATED. Content Intelligence scores are advisory.**

A low score, `FIRE FAIL`, missing market evidence, unsupported classification, or a
`CI: NOT RECOMMENDED` result **MUST NOT** prevent an operator-supplied asset from reaching human
review. **Bryan is the final arbiter.**

Kick proved this specifically: CI returned `filtered_media` / `video_type_unproven` /
`CI: NOT RECOMMENDED`, and the asset still remained reviewable, exposed
`Approve Clip / Reject / Hold`, was approved, persisted the decision, updated the same card, and
exists durably in GHL.

Do **not** later reintroduce automated hard filtering into manual intake.

### Fixes preserved by commit `925a834` (exact terminology)
1. **post-persist render failure can no longer make a successful decision appear to have failed** —
   the rich render is best-effort (`format_source_embed`) with a never-raising
   `minimal_decision_embed` fallback, so `persist decision` can never be hidden by `render updated card`.
2. **CI-declined manual media has an explicit honest UI state** —
   `format_manual_not_recommended_embed` renders `📥 MANUAL INTAKE · CI: NOT RECOMMENDED`, the
   `Reason` (e.g. `video_type_unproven`), and `Analysis: … unavailable` instead of coerced zeros
   masquerading as analysis.
3. **operator-supplied content remains human-reviewable when CI does not recommend it.**
4. **manual re-analysis/re-ingest is bound deterministically** — newest-first content-id lookup,
   older duplicates marked `SUPERSEDED_MANUAL_REINGEST` (evidence retained, never deleted), and the
   card id remembered in `moment_evidence.lineage.card_message_id` so a re-analysis **edits the same
   card** instead of minting another (`remember_card_message`).
5. **existing manual media does not trigger unnecessary acquisition/redownload after approval**
   (`_source_approve_handoff` short-circuits on `media_already_acquired`).

---

## 5. What NOT to reopen

Unless **new runtime evidence** contradicts today's acceptance, do not reopen:
manual intake basic architecture · Reddit downloader acceptance · Twitch downloader acceptance ·
Kick downloader acceptance · operator override semantics · decision persistence ·
same-card decision rendering · no-redownload-after-approval behaviour.

---

## 6. Known non-blocking follow-up

**GHL durable storage: LIVE PASS.** The newly tested Reddit, Twitch, Kick, Instagram and TikTok
assets are present in GHL Media Storage.

**GHL folder routing/organization: OPEN FOLLOW-UP — NON-BLOCKING.**
Historical LOOM folder organization was observed previously, but the mechanism responsible has
**not been conclusively located**. Status is **UNLOCATED / NOT YET RECONCILED** — not "does not
exist". Do not build folder routing during any closeout; do not claim the old mechanism is absent.

---

## 7. Architectural guardrail for future work

**Manual intake and automated discovery are separate trust contexts.**

* **MANUAL** — Bryan deliberately supplied the asset ⇒ *CI informs; Bryan decides.*
* **AUTOMATED DISCOVERY** — LOOM may rank/score to control queue volume and prioritize stronger
  candidates. However, do **not** automatically inherit old `filtered_media`, `video_type_unproven`,
  `FIRE FAIL`, or similar hard-rejection logic as *sourcing policy* merely because those mechanisms
  exist in CI.

When automated discovery is resumed, its filtering/ranking policy must be explicitly reviewed
against: **FIND FIRE CONTENT FIRST → RANK IT → SURFACE RISK/UNCERTAINTY → LET HUMAN POLICY CONTROL
WHAT IS ACTUALLY EXCLUDED.** Do not redesign automated discovery now; preserve the requirement.

---

## 8. Exact next build target (from the existing roadmap — not invented)

* **Current stage/segment:** Stage 2 · **Segment 5 — Multi-Source Moment Mining** (active).
  Segment 6 not started. RECUT on hold.
* **Completed gate:** Manual Intake finalization — **live accepted** across the eight paths in §3.
* **Next build target:** the roadmap order is
  **`finalize Manual Intake` → `timeline-editor capability` → already-authorized launch spine /
  Segment 6`** (see `CURRENT_STATE.md` START HERE and §12.6).
  Manual Intake is now finalized, so the next item is the **missing timeline-editor capability** —
  and it **requires Bryan's explicit authorization before any work starts**.
  *The previous editor acceptance remains locked: do not re-audit, re-test, or add Editor feature
  work, and do not resurrect the old Discord Activity editor architecture or abandoned approaches.*
* **Also still open in Segment 5 (pre-existing, not superseded by today):** the mandatory
  **FIRE-APPROVE → n8n/OpenShorts bridge verification**, the real authenticated long-form YouTube
  run, and the remaining six-platform / FIRE-path proofs. Do not invent a new segment for these.
* **Acceptance criteria already established** for the next task: read `CURRENT_STATE.md` START HERE
  plus the relevant section for that item. Do **not** spend another session auditing already-proven
  work — §3 is the proof.

---

## 9. Regression state

`infra/discord-review` — **333 OK** · `infra/content_intelligence` — **477 OK**
(discord-review = 309 baseline + 24 new engagement-metadata tests; content_intelligence re-run
at the §10 increment on commit `c7eaedd`).

These are **regression protection only** — they are not a reason to reopen accepted architecture,
and they are not a substitute for the live operator acceptance recorded in §3.

---

## 10. INCREMENT — Manual Intake engagement metadata captured (2026-09-26, later)

Commit **`c7eaedd`**, pushed. This does **not** reopen anything in §5; it fixes a defect that
sat underneath the accepted lifecycle (the card's market/engagement fields), not the lifecycle.

**Defect.** Operator-supplied sources reached `#clip-review` with Views/Likes/Comments/age =
`Unavailable` and `Market 0/100`, even though the evidence was free and obtainable through the
extractor path LOOM already runs.

**Root cause.** Manual Intake never captured platform metadata at all: `build_record()` built
`observed_metrics` from the local `ffprobe` alone, and `run_manual_intelligence()` hard-set
`record.publication_timestamp = None`. With no views/likes/comments and no publication time,
`market_evidence` was empty for every manual asset — and the card rendered that **absence** as a
measured `0/100`. Unknown was being displayed as zero.

**Fix (no new provider, no migration, no new gate, no new dependency).**
* `manual_media_intake.fetch_source_metadata()` reuses the EXISTING bounded metadata probe
  (`ytdlp_adapter.build_probe_command`: metadata only, `--skip-download`, no cookies, no auth, no
  paid provider) and maps provider fields onto the keys the existing market policy reads, per
  platform. Reddit keeps its native `score`/`num_comments` and is never given invented views.
  Never raises, never fabricates; a field the platform does not expose stays absent.
* Evidence is durable **twice**: the submission row's `media_reference.source_metadata` and the
  candidate's `moment_evidence.lineage.source_metadata`. Both JSONB, so it survives a CI decline
  before persistence — the Kick `filtered_media` asset had only `lineage` and no `market_evidence`.
* `market_evidence` now carries `evidence_available` / `evidence_coverage` /
  `unavailable_metrics`, so **unknown is distinguishable from a measured zero**. The card prints
  `Market unavailable — no market evidence observed (not a zero score)` with coverage, shows the
  available engagement rather than hiding the section when one field is missing, and still renders
  an observed `0` as `0`.
* Human arbitration unchanged: no hard filter, no FIRE input, no acquisition behaviour.
  **LOOM informs. Bryan decides.**

**What was obtainable for free, measured on the real assets** (existing path): TikTok
**771,600 views / 13,400 likes / 173 comments / 2,632 reposts / 5,513 saves**; Instagram partial
(18,914 likes, 1,149 comments — views need auth); Reddit native score 128 / 16 comments; Kick
2,429 views. Twitch has the existing Helix `/helix/clips` app-token path (not live-probed here).

**Tests.** `discord-review` **333 OK** (309 + 24 new in
`tests/test_manual_engagement_metadata.py`) · `content_intelligence` **477 OK**. No gate was
weakened and no accepted semantics from §3–§4 changed.

**Live.** Adapter restarted after the commit and verified serving `c7eaedd`: PID 71579,
`/health` → `ok`, new symbols confirmed present in the loaded bytecode.
