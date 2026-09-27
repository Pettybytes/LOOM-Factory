# LOOM — NEXT SESSION HANDOFF

**Authoritative closeout: 2026-09-26 (session ended ~19:30 EDT).**
Supersedes the 2026-09-25 handoff and the 2026-09-26 Manual-Intake closeout.
**Read this file first.** `HANDOFF.md` defers to it; `CURRENT_STATE.md` is the execution authority
and carries the long-form history.

---

# START HERE — TIMELINE EDITOR

**Read this section only, then open the handful of files it names. Do not search the repo.**

### 1. Current HEAD

Code HEAD **`253d503b000925f8561a0c6ea340395266c3e8c9`** (`fix(media): route LOOM uploads into the
existing GHL LOOM folder, verified`), plus this documentation commit. The documentation commit that
introduced this section is the live HEAD — **verify, never assume**:

```
git rev-parse HEAD && git rev-parse origin/master && git status --porcelain
git log --oneline -6
```

### 2. Stage / segment

Stage 2 · **Segment 5 — Multi-Source Moment Mining** (active). Segment 6 not started. RECUT on hold.
`Segment 5 is NOT closed`: the real authenticated long-form YouTube run, the FIRE-APPROVE →
n8n/OpenShorts bridge verification, and remaining six-platform / FIRE-path proofs are still open.
**Segment 5 remains OPEN.**

### 3. LIVE ACCEPTED — do NOT reopen (see §11 closed list)

Manual Intake operator-driven, real Discord, all **nine** paths: Reddit URL + file, Twitch URL +
file, Kick URL + file, Instagram URL, TikTok URL, **YouTube URL**. Engagement metadata capture is
live accepted (YouTube: 2,010,656 views / 13,411 likes / 645 comments / Market 100/100 / coverage
3/5). **GHL FOLDER ROUTING — LIVE PASS** (see §13a). Full lifecycle, decisions and terminology: §3,
§4, §10, §11, §13.

### 4. Exact next task

Build the **missing timeline-editor capability** — the only item between Manual Intake finalization
and the already-authorized launch spine / Segment 6. **This is a NEW capability; do not resurrect
the old Discord Activity editor or any abandoned approach** (see §5C below and §7).

### 5. The gap (authoritative, from `EDITOR_IMPLEMENTATION_PLAN.md`)

**OpenShorts has NO manual timeline editor today.** Its "editor" is LLM-driven
(`VideoEditor` → Gemini → FFmpeg filter/EDL). A lightweight **manual correction interface must be
ADDED**, reusing the existing transcript / scenes / EDL / Remotion-preview / reframe / subtitle /
rerender infrastructure. The plan above is **APPROVED (2026-09-13)** and is the schema + exact-files
plan — start there, do not re-plan it.

### 6. Files to inspect FIRST (small, explicit set)

| Purpose | File |
|---|---|
| Approved editor plan (schema + files) | `EDITOR_IMPLEMENTATION_PLAN.md` |
| Editor gateway, sessions, render/return seams | `infra/discord-review/adapter.py` |
| Review card + decisions (Discord seam) | `infra/discord-review/source_review.py` |
| Durable manual lineage | `infra/discord-review/manual_intake_intelligence.py` |
| Durable media + folder upload | `infra/discord-review/manual_media_intake.py` |
| Edit-session schema | `infra/supabase/012_loom_edit_sessions.sql`, `013_register_edited_asset.sql`, `014_loom_editor_session_token.sql`, `015_loom_editor_launch_context.sql`, `016_loom_editor_draft.sql`, `037_loom_edited_asset_storage_status.sql` |
| OpenShorts backend + render | `openshorts/app.py`, `openshorts/clip_selection.py`, `openshorts/dashboard/` |

### 7. Exact symbols / routes likely involved (`infra/discord-review/adapter.py`)

* **Editor gateway:** `_handle_editor_api(method)`, `_authorize_editor_path(...)` (strict endpoint
  allow-list + job/clip ownership), `_forward_to_backend(...)`, `_backend_request(...)` — the backend
  is the **loopback OpenShorts server `http://127.0.0.1:8000`**, and the adapter translates the
  durable job identity to the rehydrated `temp_job_id` (clip index collapses to `0`).
* **Allow-listed editor endpoints:** `GET /api/status/{job}`, `GET /api/source/{job}`,
  `GET /api/clip/{job}/{idx}/{transcript|edl|scenes}`, `GET /api/render/{id}`,
  `POST /api/{subtitle|clip/rerender|clip/reframe|hook|edit|header}`.
* **Session / launch:** `/discord/editor/bootstrap` (durable bootstrap — resolves the review asset
  from LOOM media/version records, **not** transient `/api/status`), `/discord/editor/exchange`,
  `/discord/editor/oauth`, `/discord/editor/pending`, `/discord/editor/draft`;
  `mint_session_token_diag`, `resolve_editor_session_token_diag`, `register_editor_token`,
  `consume_editor_token`, `resolve_edit_session`, `fetch_session_by_id`,
  `EDITOR_TOKEN_TABLE = "loom_editor_session_tokens"`.
* **Edit completion / render:** `_handle_editor_complete`, `register_edited_asset_rpc(payload)`,
  `register_clip_media(...)`.
* **Review card + decisions:** `_post_source_card(...)`, `_handle_interaction()`, and in
  `source_review.py`: `format_source_embed`, `format_source_buttons`, `make_source_custom_id`,
  `parse_source_custom_id`, `persist_decision`, `decided_embed`.

### 8. Editor source-resolution contract (MANDATORY)

Resolve media through LOOM's **durable identity/lineage**. Never locate media by browsing GHL,
searching folders, guessing or fuzzy-matching filenames, scraping Discord attachments, or
re-discovering the platform URL when durable media already exists.

Conceptual chain: `Discord review card → candidate/review identity → tenant_id → durable
source/media reference → exact GHL asset / durable media URL → selected source window → editor
session → edit instructions → render → review`.

Where those identities live TODAY:

| Identity | Where it lives |
|---|---|
| Candidate / review identity | `loom_source_candidates.duplicate_fingerprint` (+ `tenant_id`, `profile_id`, `platform`, `platform_content_id`) |
| Review card ↔ candidate | `loom_source_candidates.moment_evidence.lineage.card_message_id` |
| Human decision | `loom_source_reviews` (keyed by `duplicate_fingerprint`, first terminal decision wins) |
| Durable manual media | `loom_manual_submissions.media_reference` (`url`, `file_id`, `folder_id`, `sha256`, `size_bytes`, `placement_*`) and `moment_evidence.lineage.media_reference` |
| Source window | `loom_source_candidates.boundary_plan` (+ `moment_evidence.lineage.boundary_provenance`) |
| Clip/burned + clean pre-hook source | `loom_media` (`canonical_url`, `clean_source_url`, `ghl_media_id`, `ghl_folder_id`, `previous_asset_version`) |

Shortest existing path: **candidate row → `moment_evidence.lineage.media_reference` (GHL filesafe
URL) → that asset**. For rendered clips: `loom_media.canonical_url` + `clean_source_url`.

### 9. Discord integration seam

`#clip-review` cards are built by `source_review.format_source_embed` with buttons from
`format_source_buttons`; custom_ids are `make_source_custom_id(decision, fingerprint)` =
`<code>_<sha256>` and parsed by `parse_source_custom_id`. Clicks arrive at
`adapter.Handler._handle_interaction` and are dispatched by that parsed custom_id; decisions persist
via `source_review.persist_decision`, and the same card is then edited in place. **An `Edit` /
`Open Editor` action should be ADDED as another button on this existing machinery — reuse it, do not
replace it.**

### 10. Existing editor seam / render seam / return-to-review seam

* **Existing editor seam:** there is no manual timeline editor (see §5). What exists and is reusable:
  edit sessions (`loom_edit_sessions`), single-use hashed session tokens, the durable bootstrap, the
  `/api/*` allow-list gateway to OpenShorts 127.0.0.1:8000, and `loom_editor_draft`.
* **Render seam:** OpenShorts is the ONLY rendering engine — reuse it, never build a second one.
  Render/export is reached through the gateway (`POST /api/edit`, `/api/clip/rerender`,
  `/api/subtitle`, `/api/clip/reframe`, `/api/hook`, `/api/header`; `GET /api/render/{id}` for
  status), and `register_edited_asset_rpc` atomically allocates the next `asset_version`
  (`render-<major>.<minor>`, `previous_asset_version` = lineage) and marks the session completed.
* **Return-to-review seam:** `register_clip_media(...)` persists the durable artifacts to
  `loom_media`, and `send_review_card(payload)` + `check_existing_card(tenant_id, clip_id,
  asset_version)` + `submission_fingerprint(body)` keep **exactly one active review object per
  asset**. A new version is `final_review_pending` — never approved, never promoted, never
  distributed. Reuse the same one-active-review principle; never mint a duplicate card.

### 11. Known constraints (hard)

* **Editor acceptance is LOCKED** — do not re-audit, re-test, or add Editor feature work, and do not
  resurrect the Discord Activity editor architecture or abandoned approaches.
* **Preserved migration `038_loom_editor_session_tokens.sql`**: untracked, **do not add, do not
  commit, do not modify, do not execute** (SHA1 `3da12826ef96dd05ce089b490ee490e66baf01c0`).
* **Do NOT restructure GHL folders.** The LOOM folder now also contains pre-existing subfolders
  `Intake Reviews` and `dreemzy`; leave them alone.
* **The editor must not depend on browsing GHL folders.** Folder targeting is a proven storage
  primitive; the eventual durable organization must stay **tenant-aware**
  (`LOOM → tenant_id → lifecycle assets`) but the exact hierarchy is **NOT locked**.
* No Manual Intake acceptance re-runs, no paid acquisition, no RECUT, no Segment 6, and no
  age/adult gate (`infra/content_intelligence/tests/test_age_gate_removed.py` is the guard).

### 12. What requires Bryan's live acceptance

Any editor launch from a real Discord card, and any new-asset acceptance, must be Bryan driving the
real Discord surface. Tests prove wiring, never acceptance.

### 13. What NOT to investigate

See the closed list in §14. If a fact is closed there, do not spend tokens re-deriving it.

---

## 1. Repository state (verified, not assumed)

| Item | Value |
|---|---|
| Branch | `master` |
| Final pushed commit | **`c7eaedd0acf74f991d510d0ad54cb85f073c6c53`** (engagement-metadata increment, §10) → later `253d503` (GHL folder routing, §13); see `git log` |
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

---

## 11. LIVE ACCEPTED — engagement-metadata capture through the real Discord flow (2026-09-26)

Bryan personally submitted a real YouTube URL through the live Manual Intake workflow **after** the
§10 patch and adapter restart. **This is live operator acceptance, not a unit test.**

| Item | Observed on the live `#clip-review` card |
|---|---|
| Source | `https://www.youtube.com/watch?v=eFHfe7AArlE` |
| Views/plays | **2,010,656** |
| Likes | **13,411** |
| Comments | **645** |
| Native demand | 2,010,656 views |
| Age | ~74.34 h |
| Lifetime avg VPH | ~27,047.7 |
| Engagement | ~0.006991 |
| Market score | **100 / 100** |
| Evidence coverage | **3/5** |
| Route | `established` |
| Durable media | appeared in GHL Media Storage as `youtube__eFHfe7AArlE.mp4` |

Bryan then clicked **Approve Clip** on that same card, which visibly persisted:
decision `Approve for Acquisition`, reviewer `passportpetty`, timestamp recorded.

### Scope of this acceptance (read carefully)

It covers the **engagement-metadata / Manual Intake path**: real platform evidence reached the
card, durable media was stored, and human arbitration persisted on the same card.

The reported figures are internally consistent with the §10 contract: engagement `~0.006991`
= (13,411 + 645) / 2,010,656, and coverage `3/5` reflects YouTube exposing no share/save
counters — those two stay honestly **Unavailable** rather than becoming 0.

### Explicitly NOT part of this acceptance

The same asset separately showed `Moment unavailable`, `Content 0`, `Selection 0`, `FIRE FAIL`.
That is a **separate CI / moment-analysis concern**. It is not a failure of this acceptance and
must not be used to reopen the accepted Manual Intake architecture (§3–§5).

### Platform metadata capabilities are unchanged

The §10 table still governs: TikTok full · Instagram / Reddit / Kick partial-native ·
**YouTube = views / likes / comments only** · Twitch via the existing Helix app-token path.
No new platform capability is claimed by this acceptance.

---

## 12. GHL folder routing — archaeology verdict + discovered folder, BLOCKED on one detail

Investigation (read-only except as disclosed): **GHL upload/storage remains LIVE PASS**; only
folder *placement* is at issue.

### The first question answered: did LOOM ever know the folder ID or have a working mechanism?

**No.** The `LOOM` folder id appears in **no commit** (`git grep 6aa61446… $(git rev-list --all)`
→ empty), `folderId` appears **nowhere in history** (`git log --all -S folderId` → empty), and no
commit ever implemented folder targeting. Supporting evidence:

* `adapter.ghl_upload_file()` posts multipart with only `file`, `name`, `hosted` to
  `POST /medias/upload-file` — **no folder field at all**.
* `manual_media_intake.store_durable_media(folder_id=…)` **records** the id but never passes it
  to the upload (its own docstring calls targeting "only attempted when an id is supplied").
* `LOOM_GHL_INTAKE_FOLDER_ID` is whitelisted in `run.sh` and read into `MANUAL_MEDIA_FOLDER_ID`,
  but is **unset in every env file** (`.env`, `.env.bak-loom-ingress-20260921181937`,
  `.env.bak-v12-routing` — zero folder-named keys).
* `loom_media.ghl_folder_id` exists (migration 002) but is **never written by any code**.
* Blueprint §5B specifies `LOOM/{tenant_id}/{...}`, but Segment 5 is still marked **PENDING** —
  the convention was designed, never built.
* No other GHL upload implementation exists (n8n workflows, chrome-extension, browser_flow,
  local-helper): none.

This is a finding about **this repository**, not a claim about anything Bryan may have created
manually in the GHL UI (consistent with §6's caution). The folder is real; LOOM never targeted it.

### The folder IS discoverable through the existing integration

Using the SAME token already in use, and the production `User-Agent` (without it Cloudflare
returns `1010 Access denied`):

```
GET /medias/files?locationId=<loc>&type=folder      -> 200
```

It returns **exactly one** folder named `LOOM`, unambiguous:

| Field | Value |
|---|---|
| `_id` | **`6aa61446df2d015553434919`** |
| name | `LOOM` |
| `parentId` | `null` (top level) |
| createdAt | 2026-09-13T03:11:02.826Z |
| appFolder / isAutoGenerated | `false` / `false` |

Other folders present are unrelated (`Derm 7 `, `Brand Boards`, `Relocated Item`) — no second `LOOM`.

### Root placement is confirmed from the API itself

Every uploaded file (all **64**) returns `parentId: ""` — i.e. **My Media root**, including
`youtube__eFHfe7AArlE.mp4` and the manual-intake TikTok/Kick/Reddit/Twitch assets.

### The blocker (why no code change shipped)

The **upload parameter name for folder targeting could not be established** from the repo, the git
history, GHL's docs (JS-rendered, no extractable content), or read-only API probes. A probe with a
deliberately invalid folder id returned **HTTP 201 and placed the file at root** — so an
unrecognised or wrong folder value fails **silently**. Shipping a guessed parameter would therefore
reproduce this exact bug while looking fixed. Per the standing rule, **do not guess**.

Disclosed: one such probe accidentally persisted a 0-byte `probe.mp4`; it was **deleted**
(`DELETE /medias/{id}?altId=<loc>&altType=location` → `{"deleted":true}`) and the library is back
to **64 files**. No other asset was created, moved, or altered.

### Status — SUPERSEDED BY §13 (resolved in the same session)

**BLOCKED — GHL media-upload folder parameter unverified.** Exact need is one of: (a) authorize a
single controlled self-cleaning probe upload against `6aa61446df2d015553434919`, (b) supply the
field name(s) `POST /medias/upload-file` accepts, or (c) ship the plumbing with post-upload
placement verification so a wrong value fails loudly, then prove it with Bryan's live Discord test.

Regardless of route, final routing acceptance is Bryan's own Discord action — never a test result.

---

## 13. GHL FOLDER ROUTING — CONTRACT PROVEN + CODE READY (2026-09-26)

Bryan authorized **Option 1** (a tightly bounded, self-cleaning live probe). Status now:

**`CODE READY — LIVE ACCEPTANCE REQUIRED`** — not LIVE PASS until Bryan's own Discord submit.

### 1. Proven GHL folder-targeting request contract

The targeting field is the **multipart form field `parentId`**, whose value is the target folder id:

```
POST https://services.leadconnectorhq.com/medias/upload-file
Authorization: Bearer <token>   Version: v3
Content-Type: multipart/form-data

file=<bytes>  name=<filename>  hosted=false  parentId=6aa61446df2d015553434919
```

`folderId` is **NOT honoured** — GHL answers **HTTP 201**, silently ignores it, and stores the asset
in My Media root. That silent-ignore behaviour is why HTTP success can never be treated as placement
proof.

### 2. Probe evidence

Every candidate used the same 1,709-byte disposable `loom-probe-*.mp4`; success was judged ONLY by
the stored record's own `parentId`:

| Probe (multipart fields) | HTTP | resulting `parentId` | verdict |
|---|---|---|---|
| `folderId` only | 201 | `""` (root) | ❌ not the contract |
| `parentId` only | 201 | `6aa61446df2d015553434919` | ✅ **contract** |
| `parentId` only (repeat) | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `folderId` + `parentId` | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `folderId` + `parentId` (repeat) | 201 | `6aa61446df2d015553434919` | ✅ contract |
| `?parentId=` query | 201 | `""` (root) | ❌ |
| `?folderId=` query | 201 | `""` (root) | ❌ |

All four probe records were located via
`GET /medias/files?locationId=<loc>&type=file&parentId=<folder>` and **deleted**
(`DELETE /medias/{id}?altId=<loc>&altType=location` → `{"deleted":true}`).

**Final state: LOOM folder contents 0 · root listing 64 · `count` 64 · zero probe residue.**

One earlier probe (before authorization) accidentally persisted a 0-byte `probe.mp4`; it was also
deleted and is covered by the same final counts.

**Note:** the LOOM folder was EMPTY before this work — every one of the 64 assets was at root, and
the folder's own contents contained only probe files. The folder existed (created 2026-09-13) but
LOOM had never populated it.

### 3. Root cause

LOOM never targeted a folder: `ghl_upload_file()` sent only `file`/`name`/`hosted`, and
`store_durable_media(folder_id=…)` recorded the id without passing it. With the env var also unset,
every upload landed in My Media root by default.

### 4. Files changed

* `infra/discord-review/adapter.py` — folder-aware upload + placement verification
  (`_ghl_list_media_files`, `ghl_verify_placement`, `ghl_upload_file(..., parent_id=None)`,
  `GHL_MEDIA_PARENT_ID`).
* `infra/discord-review/manual_media_intake.py` — passes the folder through and propagates
  placement evidence into the returned durable reference.
* `infra/discord-review/tests/test_ghl_folder_routing.py` — new, 11 focused tests.

### 5. Configuration

`LOOM_GHL_INTAKE_FOLDER_ID=6aa61446df2d015553434919` — the **existing** pattern (already whitelisted
in `run.sh`), now actually consumed. It is read once into `MANUAL_MEDIA_FOLDER_ID` /
`GHL_MEDIA_PARENT_ID` and is the **default target for every LOOM upload**, not just Manual Intake.
The folder id is not a secret; no value is hardcoded in application code. With the variable unset the
request is byte-identical to the previous behaviour (root).

### 6. Post-upload verification (fail-loud)

HTTP success is never treated as placement evidence. After every upload the durable record is read
back and its `parentId` compared to the target:

* match → `placement_verified: true`, `placement_reason: in_target_folder`;
* mismatch → loud `[media] ROUTING FAILURE …`, `placement_verified: false`, with
  `placement_expected/actual/reason` carried into the durable media reference for diagnosis.

The asset is still kept (the durable-media lifecycle is unchanged) but root placement is **never**
reported as folder success. Verification can never fail an upload that already succeeded.

### 7. Tests

Focused: **11 OK** (`test_ghl_folder_routing.py`) covering the proven contract, matching placement,
HTTP-201-but-root → fail loud, missing configuration, and no regression to the upload shape.
Regressions: `discord-review` **344 OK** (333 + 11) · `content_intelligence` **477 OK**.

### 8-12. Repo / runtime state

Committed and pushed; `HEAD == origin/master`; worktree clean except the preserved
`infra/supabase/038_loom_editor_session_tokens.sql` (untouched, SHA1
`3da12826ef96dd05ce089b490ee490e66baf01c0`). Runtime **restarted** (folder config is read at process
start): PID **86696**, `/health` → `ok`, and the folder variable is present in the running process
environment.

### Live acceptance — Bryan's exact action

1. In Discord, post a source in `#manual-intake-📸` and run the `intake-media` command
   (the normal Manual Intake flow — one new source only).
2. Let it complete to the `#clip-review` card.
3. Confirm BOTH:
   * the asset appears **inside the existing `LOOM` folder** in GHL Media Storage (not My Media root), and
   * API evidence: `GET /medias/files?locationId=<loc>&type=file&parentId=6aa61446df2d015553434919`
     lists it, i.e. its `parentId` equals `6aa61446df2d015553434919`.

If the card or logs show `ROUTING FAILURE`, the placement guard worked — report it rather than
retrying blindly.

---

## 13a. GHL FOLDER ROUTING — LIVE PASS (2026-09-26, operator-driven)

Bryan submitted a fresh TikTok source through the real Discord Manual Intake flow and confirmed the
asset appears inside `GHL Media Storage → LOOM`. Verified independently from read-only evidence —
not from the visual confirmation alone:

| Check | Evidence |
|---|---|
| Asset exists | `tiktok_comountainmarine_7671018311492193567.mp4`, GHL `_id 6ab860781f3be2be1bd92a0d`, 4,653,539 B, created 2026-09-27T00:16:56Z |
| Inside the LOOM folder | stored `parentId = 6aa61446df2d015553434919` |
| Durable record matches | `GET /medias/files?…&type=file&parentId=6aa61446…` lists it |
| No routing failure | `ROUTING FAILURE` count in `adapter.log` = **0**; instead `folder placement verified … parentId=6aa61446…` |
| Durable placement evidence | `loom_manual_submissions.media_reference`: `folder_id`, `placement_verified: true`, `placement_expected = placement_actual = 6aa61446…`, `placement_reason: in_target_folder` |
| No duplicate acquisition/redownload | `moment_evidence.lineage.media_already_acquired: true`; **0** acquisition jobs created; reuse guard hit once |
| Root untouched | root still **64** files; LOOM folder holds exactly the one accepted asset |

Bonus confirmation: this same asset also live-proves the §10 engagement capture —
`source_metadata.metrics = {views: 3,500,000, likes: 209,100, comments: 866, shares: 34,300,
saves: 30,782}`, candidate `market_score = 100`, `market_evidence.views = 3,500,000`,
`evidence_available: true`, `coverage: 1.0`, real publication timestamp retained.

**Status: `GHL FOLDER ROUTING — LIVE PASS`.** Do not re-run a live intake to "confirm" this.

---

## 14. CLOSED MILESTONES + DO-NOT-REOPEN LIST

Unless **new runtime evidence directly contradicts** them, these are CLOSED and must not be
rediscovered, re-tested, or reopened:

**Manual Intake** — operator-driven live-proven for Reddit (URL + file), Twitch (URL + file),
Kick (URL + file), Instagram (URL), TikTok (URL), **YouTube (URL)**. Lifecycle accepted end to end:
operator source → `#manual-intake` → `intake-media` → durable GHL media → CI advisory analysis →
durable candidate → exactly one active review card → human decision → durable `loom_source_reviews`
row → same card visibly updated → no unnecessary redownload.

**Engagement metadata** — captured where platforms expose it; publication timestamp retained when
available; **unknown ≠ measured zero**; evidence coverage reported honestly; market evidence survives
durable persistence; YouTube exposes no share/save counters and they are **not fabricated**.

**GHL folder routing** — `POST /medias/upload-file` targets a folder with the multipart field
**`parentId`**; **`folderId` does NOT work** (HTTP 201, silently ignored, stored at root) and neither
does a query-string folder parameter; **HTTP 201 alone is NOT placement proof**; LOOM reads the
durable record back and compares `parentId`; wrong/root placement **fails loudly**; existing root
assets are untouched; the LOOM folder id is `6aa61446df2d015553434919`; folder listing is
`GET /medias/files?locationId=<loc>&type=folder` and folder contents are listed with
`…&type=file&parentId=<folder>`.

**Also closed:** CI advisory semantics for Manual Intake (a low score, `FIRE FAIL`,
`filtered_media`, `video_type_unproven`, `no qualifying moment`, missing market evidence, or
unsupported classification must **never** prevent an operator-supplied asset reaching human review —
LOOM informs, Bryan decides); the age/adult gate removal; no-redownload-after-approval;
one-active-review-card behaviour; automated-discovery ranking/filtering policy (unchanged, and CI
rejections must **not** silently become sourcing policy); the old Cloudinary architecture; and that
**GHL is canonical durable storage**.

### Tenant architecture requirement (record, do not build now)

Bryan observed the existing GHL `LOOM` folder already contains subfolders `Intake Reviews` and
`dreemzy`. **Do not restructure, rename, move, delete, or create tenant folders during this work.**

GHL folder targeting is now a **proven storage primitive**. The eventual durable organization must
stay **tenant-aware**, conceptually `LOOM → tenant_id → lifecycle assets`. The exact hierarchy is
**NOT yet locked**. Critically: **the editor must not depend on browsing GHL folders** — it resolves
media through durable identity/lineage (§8 above).

### Future edit-event extensibility (requirement, do not build now)

Editor V1 is **not** Premiere/CapCut. No SFX, transitions, overlays, or commentary audio are
authorized yet. But the session/timeline representation must stay extensible for future **typed edit
events** (`trim`, `split`, `remove`, `reorder`, `sfx`, `transition`, `overlay`, `commentary`,
captions, hook text).

**Honest state of today's representation:** `loom_edit_corrections` has first-class columns for
`trim_start`/`trim_end` plus `caption_changes` (jsonb) and `crop_reframe_changes` (jsonb), and a
free-form `edl_metadata` (jsonb) — so additional op types can ride in `edl_metadata` and/or additive
columns **without replacing the editor**. The plan's requirement is therefore satisfiable, but only
if V1 extends that representation instead of hard-coding it around trim alone. This is a design
constraint on the next task, not a claim that the capability already exists.
