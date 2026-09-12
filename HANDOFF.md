# LOOM — HANDOFF

**No task is complete until this file reflects the actual current state.**
Never put API keys, tokens, passwords, cookies, secrets, complete auth
headers, or private customer data in this file.

---

## Last Updated
2026-09-08 20:38 EDT (America/Detroit)

## Model/Provider Currently Operating Bentley
anthropic/claude-sonnet-5 (via OpenClaw gateway)

## Current Objective
**Segment 3E (Full Source Ingestion) is READY FOR LIVE TEST — callback-first architecture, local relay auth.**
- Workflow 03 was rebuilt to remove ALL continuous status polling in favor of OpenShorts' native terminal webhook (`job.completed`/`job.failed`), confirmed present in the installed `app.py` (`_notify_job_webhook`, `_deliver_webhook`).
- **n8n Cloud has no Variables/`$env` feature on this account's license** (`GET /api/v1/variables` → HTTP 403, `feat:variables` not licensed). This made OpenShorts' own HMAC-SHA256 signature unverifiable via a Code node without a place to durably and secretly store the shared secret.
- **Resolved with a $0 local Caddy relay**, per Bryan's explicit approval: OpenShorts' backend now posts its terminal callback to `http://host.docker.internal:8082/relay` (new port on the existing `loom-tunnel-proxy` Caddy container, loopback-only). Caddy injects a fixed private header (`X-LOOM-Relay-Token`) and forwards to n8n Cloud's real webhook URL. The n8n Webhook node authenticates that header via a native **Header Auth** credential (`LOOM Relay Auth`, id `p7I7vakxK0a7qdew`) — no `$env`, no Variables, no raw-HMAC Code node needed.
- **Trade-off, stated explicitly:** this design authenticates the HOP from this Mac to n8n Cloud, not OpenShorts' own per-payload HMAC signature. OpenShorts' signature is no longer computed/verified anywhere in this pipeline (the `webhook_secret` field was removed from the `/api/process` submission body). Acceptable for now because the relay is the only thing that can reach this n8n webhook path with the correct header, and only this Mac can reach the relay (loopback-only bind) — flagging for a future hardening pass, not silently upgrading it to "signature verified."
- **Terminal-event lock, job_id correlation, `job_id:clip_index` candidate key, and the bounded 4-attempt GET retrieval chain (immediate/5s/15s/45s) are all preserved unchanged** from the prior round.
- All four workflows (00–03) remain inactive. No VOD submitted, no OpenRouter call, no Discord transmission this round.
- The target URL for the eventual controlled test is `https://www.youtube.com/watch?v=xtOKTsIYwH0` (Bentley-reported from existing config, not freshly re-approved by Bryan this round).

### Manual step still required before any live test
The n8n Webhook node's Header Auth credential (`LOOM Relay Auth`) was created via the API with a freshly generated, cryptographically random token. That same token now also lives in `/Users/user/Desktop/LOOM-Factory/infra/tunnel-proxy/.env` (mode 600, git-ignored) for Caddy to inject. **These two copies must match for the relay to authenticate successfully** — they were written from the same generation step this round, so they should already match, but this has not been runtime-verified (no callback has been sent yet, since Workflow 03 is inactive and no VOD has been submitted).

To actually receive a callback, Bryan must additionally:
1. Restart/reload the `loom-tunnel-proxy` container so it picks up the new Caddyfile and the new `LOOM_RELAY_TOKEN` env var (`docker compose -f infra/tunnel-proxy/docker-compose.yml up -d`).
2. Activate Workflow 03 in n8n (required for the webhook to be listening, and for workflow static data — used for run-context correlation and dedup locking — to persist across executions, per n8n's own documented behavior).

## Prior Objective (Segment 3D)
**Segment 3D (Factory Failure Handler) is COMPLETE.**
- Constructed Workflow 00 via API, implementing the strict Failure Contract validation.
- Configured native `n8n-nodes-base.discord` to format and route alerts safely using the `LOOM Discord Alerts` credential.

## Prior Objective (Segment 4A)
**Segment 4A (Remove openshorts_score from prompt) is COMPLETE.**
- Workflow 01 was modified to remove the OpenShorts predicted score from the Qwen prompt in `Build OpenRouter Request`, removing the anchor bias.
- The score was safely preserved through `Validate LOOM Score` to be returned in `Return Validated Result`.
- **Upload-Post Status:** Browser-verified.

## Prior Objective (Segment 3C)
**Segment 3C (real end-to-end ingest test) is COMPLETE.** Real OpenShorts job
data flowed through the live public Funnel URL, through Workflow 02, into
Workflow 01 as a sub-workflow call, produced a real Qwen score, and returned a
fully validated result. **Fully independently verified via n8n's execution
API** (not just the screenshot Bryan provided, though it matches exactly).

## Prior Objective (Segment 3B)
**Segment 3B (authenticated tunnel) is COMPLETE.** OpenShorts's backend is now
reachable from the public internet, but ONLY through a token-gated proxy --
verified with real requests over the real public URL, not simulated. Neither
n8n workflow was touched this round, and no video job was submitted, per
explicit instruction.

## Prior Objective (Segment 3A)
**Segment 3A (ingest wiring) is built, statically verified, NOT yet
end-to-end tested.** No OpenRouter call has occurred this round, no tunnel
exists, no real OpenShorts data was used (synthetic fixture only, explicitly
labeled). Two new/modified n8n workflows exist, both inactive, both isolated
from every other workflow in the account except each other.

## Prior Objective (Segment 2C)
**Segment 2C is COMPLETE, with a real runtime PASS.** Bryan provided a genuine
screenshot of the n8n editor (URL bar showing `workflow/Vttea1FqX4TVn4yH`,
node "Return Validated Result" open, INPUT/OUTPUT panels populated) showing a
successful end-to-end execution. This is the first real runtime evidence this
workflow has produced all session -- everything before this was static/
synthetic verification only. Moving to Segment 3 readiness (below).

**⚠️ Correction to the record (kept from the prior update, still relevant):**
partway through this segment, a document referenced "the successful stored/
pinned values" (hook_score=85, completeness_score=90, clarity_score=88,
emotional_intensity_score=75, shareability_score=82, context_dependency_score=60,
duration_score=95, overall_score=81, decision='publishable') as if they came
from a real n8n execution. **They did not** -- those were synthetic numbers
Bentley invented unprompted to hand-check `Math.round()` arithmetic. This is
notably similar-looking to, but distinct from, the real screenshot recorded
below (different values, real UI evidence this time, not a text claim).

## Current Stage / Segment / Step
- Stage: 1 — Factory
- Segment: 2B — n8n → OpenRouter (Qwen) scoring connectivity — **complete**
- Segment: 2C — Parse/Validate/Return nodes — **built, structurally verified,
  awaiting one manual runtime test**
- OpenShorts baseline (Segment 1) fixes remain committed, uncommitted-to-main,
  unmerged, and still not runtime-verified in-browser — unchanged and unrelated
  to this update; see the Segment 1 sections further down for that history.

## Work Completed (this update: Segment 2B connectivity spike + Segment 2C planning)
1. Independently verified read-only diligence before building anything:
   confirmed `qwen/qwen3.5-9b` is a real, live model on OpenRouter (fetched
   `/api/v1/models` and `/api/v1/models/qwen/qwen3.5-9b/endpoints` — served via
   SiliconFlow, fp8 quantization). Not a hallucinated slug.
2. Listed all 52 existing n8n workflows via API to confirm no name collision
   and to identify what must not be touched (Jasmine/Derm by Erica workflows).
   **Resolved a discrepancy flagged earlier in `TOOLS.md`:** the n8n key in
   Dreemzy Main Brain's copy returns 401 Unauthorized; the **Jasmine-Phase1
   copy is the live key.** Worth a correction note in that source file at some
   point (not this session's job to edit that file).
3. Located the existing `OpenRouter API Key (live)` n8n credential
   (id `6s92tsh5pB4Gkw04`) via the credentials list API. Referenced it by ID
   only in the new workflow — the key value itself was never fetched, viewed,
   or exposed.
4. Created workflow `LOOM — 01 — Score Candidate Clip` (id `Vttea1FqX4TVn4yH`)
   via n8n API. Confirmed via a follow-up GET that it was created **inactive**
   with exactly 3 nodes and zero connections to any other workflow:
   `Manual Trigger` → `Dummy Candidate Clip Data` → `Score via OpenRouter (Qwen)`.
5. Attempted to run the one authorized dummy test via the n8n public API
   (`POST /workflows/:id/run` and `/execute`) — both returned `405 Method Not
   Allowed`. This is a real n8n platform limitation (manual-trigger workflows
   can't be executed via the public REST API), not something worked around by
   changing the approved workflow structure. Stopped and asked Bryan rather
   than swap in a webhook trigger without separate approval.
6. Bryan ran the test manually in the n8n editor UI and reported: Execution ID
   9497, model `qwen/qwen3.5-9b`, cost ~$0.00037375, connectivity PASSED, but
   response not machine-ready (long reasoning trace, 2,389 completion tokens,
   final JSON embedded inside a string, no parse/validate/return nodes yet).
7. **Attempted to independently verify execution 9497 via the n8n API**
   (`GET /executions/9497`, with and without `includeData=true`, and
   `GET /executions?workflowId=...`) — all three calls returned `401
   Unauthorized`, even though the same API key successfully listed workflows,
   listed credentials, created the workflow, and fetched it back moments
   earlier in this same session. This looks like a **scope/permission gap**
   on this API key specifically for the executions endpoint (workflow
   read/write appears permitted, execution read does not) rather than a key
   validity problem. **Recording this as an unverified-by-Bentley report from
   Bryan** — the numbers below are Bryan's direct report, not independently
   confirmed against n8n execution data, and that distinction is preserved
   rather than silently upgraded to "verified."
8. Did not modify the live n8n workflow's nodes or the OpenRouter request body
   yet — the new temperature/max_tokens/reasoning/response_format settings and
   the Parse/Validate/Return nodes are **planned only** (see Segment 2C
   Planning below), pending Codex's locked scoring contract.

## Work Completed (earlier: commits)
1. Confirmed pre-commit state matched expectations (`git status --short`):
   `App.jsx`, `main.py`, `quality_probe.py` modified, `aborted-runs/` untracked.
2. Staged **only** `dashboard/src/App.jsx`, confirmed via `git diff --cached --stat`
   it was the sole staged file, committed as `a0c6217`
   ("fix(dashboard): remove Upload-Post from clip-generation gate").
   Commit message explicitly notes it is **not yet manually verified in-browser**.
3. Staged **only** `main.py` + `quality_probe.py`, confirmed via
   `git diff --cached --stat` those were the sole staged files
   (`aborted-runs/` correctly excluded, still untracked), committed as `9299e5b`
   ("fix(ingest): set noplaylist..."). Commit message explicitly notes runtime
   verification is still pending a container restart.
4. Confirmed final state: `git log --oneline -3` shows `9299e5b` → `a0c6217` →
   `a602a2a` (the pre-existing tip of `main`); `git status --short` shows only
   `aborted-runs/` untracked — working tree otherwise clean. **Nothing merged,
   nothing pushed.**

## Work Completed (earlier: FACTORY-INGEST-001 patch applied)
1. Renamed branch `loom/mvp-remove-upload-post-gate` →
   `loom/factory-mvp-baseline-fixes` (`git branch -m`) to reflect it now covers
   both fixes. Confirmed active branch and confirmed `App.jsx` was still the
   only modified file immediately before the rename.
2. Created `aborted-runs/` and moved the evidence directory
   `output/59b640dd-1671-4088-bac4-013e46e88c96/` there intact (contains only
   the 464-byte `.resume.json`, no video bytes — confirmed previously).
   Nothing deleted; `output/` now holds only `.instance` and empty `thumbnails/`.
3. Added `'noplaylist': True,` to `main.py`'s `_base_opts()` (the actual
   download path) with an inline comment referencing FACTORY-INGEST-001.
4. Added the same option to `quality_probe.py`'s `base_opts` (the pre-flight
   probe) for consistency — this one was flagged optional in the diagnosis but
   included per this instruction to keep probe and download behavior aligned.
5. Deliberately left `cloud/metering.py` untouched, per explicit instruction.
6. `python3 -m py_compile` on both `main.py` and `quality_probe.py` — both
   compiled clean, no syntax errors.
7. `git diff --stat` confirms exactly 3 files modified: `dashboard/src/App.jsx`
   (unchanged from before), `main.py` (+4/-0), `quality_probe.py` (+3/-0).
   Full diff reviewed and is exactly the intended one-option-line additions
   plus comments in each file — no unrelated changes.

## Work Completed (earlier: FACTORY-INGEST-001 diagnosis)
1. Independently verified (did not just trust the incoming report) that
   containers are actually stopped: `docker ps -a` shows `openshorts-frontend`
   exited (0), `openshorts-backend` and `openshorts-renderer` exited **(137)**.
   Exit 137 = SIGKILL, typically an OOM-kill or forced stop — **not necessarily
   the clean `docker compose stop` claimed**. Flagging this discrepancy; not
   independently resolved.
2. Confirmed the Upload-Post patch is still present and uncommitted on
   `loom/mvp-remove-upload-post-gate` (`git status --short` →
   `M dashboard/src/App.jsx`).
3. Inspected `output/` read-only: total size 8.0K. One job directory
   (`59b640dd-1671-4088-bac4-013e46e88c96`) containing only a 464-byte
   `.resume.json` — no video files. `thumbnails/` subdir is empty.
4. Inspected `uploads/` (the actual `download_youtube_video` write target per
   `app.py`'s `UPLOAD_DIR`) read-only: **0 bytes, empty** — no partial `.mp4`,
   `.part`, `.webm`, `.ytdl`, or `.m4a` files anywhere.
5. Read `output/.../.resume.json` — recovered the exact queued command:
   `main.py -u "https://www.youtube.com/watch?v=iSgUMPHQEWw&list=RDiSgUMPHQEWw&start_radio=1" --keep-original -o output/59b640dd... --format vertical`.
   The URL is a YouTube **auto-generated Radio/Mix** link (`list=RD<videoId>`),
   not a manually curated playlist — explains "different YouTube video IDs."
6. Traced the yt-dlp call path end-to-end (both entry points, both land in the
   same function — see "Exact yt-dlp Call Path" below).
7. Grepped the entire repo for `noplaylist`/`playlist` as a yt-dlp option —
   **zero matches**. Confirmed no code anywhere sets it, and no OpenShorts
   feature currently depends on playlist ingestion.
8. Diagnosed root cause and drafted the smallest safe patch (below).
   **Not applied.**

## Work Completed (earlier: continuity system + Upload-Post gate)
9. Confirmed project root and repo paths.
10. Inspected `git status` — clean, up to date with `origin/main`, before any change.
11. Located and read (read-only) the two existing Desktop tools docs:
    - `/Users/user/Desktop/dreemzy main brain/TOOLS.md`
    - `/Users/user/Desktop/Jasmine-Phase1/docs/TOOLS.md`
    Neither was modified. No secret values were copied into LOOM or exposed in chat.
12. Created `README.md`, `LOOM_SOURCE_OF_TRUTH.md`, `TOOLS.md` at
    `/Users/user/Desktop/LOOM-Factory/`.
13. Inspected `dashboard/src/App.jsx` and located the exact Upload-Post blocking logic.
14. Drafted the smallest safe patch for the Upload-Post gate. Bryan approved.
15. Created branch `loom/mvp-remove-upload-post-gate` off clean `main` (`a602a2a`).
16. Applied the one-line patch (see diff further below).
17. Confirmed Vite picked it up via HMR (`[vite] hmr update /src/App.jsx`) —
    no container restart/rebuild needed at the time.
18. Confirmed all three containers running/healthy after that change, at that time.

## Files Created
- `/Users/user/Desktop/LOOM-Factory/README.md`
- `/Users/user/Desktop/LOOM-Factory/LOOM_SOURCE_OF_TRUTH.md`
- `/Users/user/Desktop/LOOM-Factory/HANDOFF.md` (this file)
- `/Users/user/Desktop/LOOM-Factory/TOOLS.md`

## Files Modified
All on branch `loom/factory-mvp-baseline-fixes`, **committed locally, not merged to `main`, nothing pushed**:

| Commit | Files | Summary |
|---|---|---|
| `a0c6217` | `dashboard/src/App.jsx` | Upload-Post gate fix |
| `9299e5b` | `main.py`, `quality_probe.py` | FACTORY-INGEST-001 `noplaylist` safeguard |

`cloud/metering.py` deliberately **not** modified, in either commit.

## Directories Moved (not deleted)
- `output/59b640dd-1671-4088-bac4-013e46e88c96/` →
  `aborted-runs/59b640dd-1671-4088-bac4-013e46e88c96/` (evidence preserved,
  contains only `.resume.json`, no media).

## Commands Executed
```
ls -la /Users/user/Desktop/
ls -la /Users/user/Desktop/LOOM-Factory
ls -la /Users/user/Desktop/LOOM-Factory/openshorts
cd /Users/user/Desktop/LOOM-Factory/openshorts && git status
git log --oneline -5
git remote -v
find /Users/user/Desktop -maxdepth 2/3 -iname "*jasmine*" / "*main*brain*" / "tools.md"
```
Plus `read` calls (no writes) on:
- `dashboard/src/App.jsx`
- `dreemzy main brain/TOOLS.md`
- `Jasmine-Phase1/docs/TOOLS.md`

## Tests Performed
1. `git diff --stat` — confirmed exactly one file changed, net +5/-1 lines
   (1 functional line, 4 comment lines).
2. `docker ps` before and after the edit — all three containers unaffected.
3. `docker logs openshorts-frontend` — confirmed Vite HMR applied the change
   live (`hmr update /src/App.jsx`), no errors.
4. `docker inspect ... openshorts-backend` health status — `healthy`.
5. `curl http://localhost:5175/` — HTTP 200.
6. `curl http://localhost:8000/health/ready` (via backend logs) — HTTP 200.

**Not performed (needs Bryan or a browser-capable session):** clearing
`uploadPostKey_v3` from localStorage and clicking through an actual clip-generation
run in the browser UI. Static review of the new `keysMissing` logic plus the
unchanged `uploadPostKey` usage elsewhere in `App.jsx` (social-connect/calendar
buttons) gives high confidence, but an end-to-end browser click-through has not
been executed by Bentley this session.

## Test Results
All automated/CLI checks above passed. Manual browser click-through still
outstanding — see "Exact Next Action."

## Containers and Services Running
As of 2026-09-06 ~12:53 EDT (`docker ps -a`), **all three OpenShorts containers
are stopped**:
- `openshorts-frontend` — Exited (0)
- `openshorts-backend` — Exited (137)
- `openshorts-renderer` — Exited (137)

Exit 137 on two of three is a kill signal, not necessarily a graceful
`docker compose stop`. **Do not restart** until FACTORY-INGEST-001 is resolved
per instruction.

## Known Errors
- **FACTORY-INGEST-001** — see dedicated section below and in
  `LOOM_SOURCE_OF_TRUTH.md`.

## Segment 3C Status: COMPLETE — real end-to-end test PASSED

**Verification method: pulled both linked executions directly from n8n's
execution API** (`GET /executions/{id}?includeData=true`) — this endpoint,
which returned 401 earlier in the session, now works correctly (that earlier
401 is understood to have been a key-handling artifact on Bentley's end, not
a real permission gap; resolved once a reliable key-file approach was used).
Every value below is read from n8n's own stored execution data, not
transcribed from Bryan's screenshot — though it matches the screenshot exactly.

**Execution 9519** (`LOOM — 02 — Ingest OpenShorts Candidate`), `mode: manual`,
`status: success`, `20:17:26–20:17:34 UTC` (8 seconds). Linked sub-execution
**9520** (`LOOM — 01 — Score Candidate Clip`), `mode: integrated`, `status:
success`, `20:17:28–20:17:34 UTC` — confirmed fired through the
`Execute Sub-workflow Trigger` entry path (not the Manual Trigger/dummy path).

**Item-by-item verification:**

| Item | Confirmed value | How verified |
|---|---|---|
| Real job ID | `c22b31f9-da0e-46ca-807a-3877fff5ee8d` | Present in `Fetch Job Status` and `Select Candidate Clip` node output in execution 9519's stored data |
| Clip index | `1` | `Select Candidate Clip` output: `clip_index: 1`, `predicted_score: 82`, `viral_hook_text: "He's tired of the excuses."` — matches the pre-planned candidate exactly |
| Duration | `22.75` | `Normalize Candidate Data` output: `clip_duration_seconds: 22.75`. Cross-checked independently against `Fetch Clip Transcript`'s own `durationSec: 22.75` — both derived independently, both match |
| Tenant | `loom-internal-test` | Present in `Normalize Candidate Data` output and carried through to the final result — confirms the earlier tenant-naming cleanup took effect in a real run |
| Transcript normalization | `"So chat He's gonna be like it's like he's coaching you I"` | Real `Fetch Clip Transcript` response had 12 caption fragments (not a clean sentence — real transcripts are messy); `Normalize Candidate Data` concatenated them via `captions.map(c => c.text).join(' ')` exactly as designed. This is real, imperfect real-world input, not a clean synthetic string — worth noting for future prompt-quality consideration, not a defect in the normalization logic itself |
| Score | `overall_score: 76` | Read directly from `Return Validated Result`'s stored output. **Independently recomputed in Node.js** using the real per-dimension scores (`hook_score` through `duration_score`, all `82`) through the actual locked formula: `round(82*0.20 + 82*0.20 + 82*0.15 + 82*0.15 + 82*0.15 + (100-82)*0.10 + 82*0.05) = 76`. **Exact match.** |
| Decision | `"repairable"` | Read directly from stored output. **Independently recomputed**: `overall_score=76 >= 60` (repairable floor) but `context_dependency_score=82` fails the `<=45` publishable gate — correctly falls to `repairable`. `model_recommended_decision: "publishable"` (Qwen's own opinion) vs. `decision: "repairable"` (LOOM's authoritative decision) — `decision_agreement: false`, correctly computed and surfaced. **This is the decision-policy gate (Round 4, Segment 2C) working exactly as designed on a real case** where the model and the policy disagree. |
| Usage/cost | `prompt_tokens: 236`, `completion_tokens: 364`, `total_tokens: 600`, `cost: $0.00013112` | Pulled from the raw OpenRouter response embedded in execution 9520's `Score via OpenRouter (Qwen)` node data — not estimated, not from the screenshot (screenshot didn't show this). `reasoning_tokens: 0` confirms `reasoning.exclude: true` worked as intended. Real cost, real charge, matches the tiny-cost expectation set going into this test. |
| Workflow state | Both inactive | Re-fetched both workflows via API immediately after: `Vttea1FqX4TVn4yH` and `6RNa6TXeppCQ7bhh` both `active: false` |
| No new OpenShorts job | Confirmed | `ls` of `openshorts/output/` shows only the pre-existing `c22b31f9...` directory and `thumbnails/`; `find ... -newermt "2026-09-06 20:15:00"` (spanning the execution window) returned **zero results** — nothing was created or modified in that directory during the test |

**Also observed, worth flagging for future segments (not a defect, an
observation):** all 7 per-dimension scores from Qwen came back identical
(`82` for every one of `hook_score`, `completeness_score`, `clarity_score`,
`emotional_intensity_score`, `shareability_score`, `context_dependency_score`,
`duration_score`). This is plausible for a short, single-context clip but is
worth watching across more test cases — uniform scores across all dimensions
could indicate the model isn't meaningfully differentiating between them for
short inputs, which would matter once real editorial decisions ride on this.
No action taken on this observation this round; flagging for Segment 4 or a
future prompt-quality review.

## Segment 3B Status: COMPLETE — authenticated public tunnel live and verified

**Prerequisite verified before building anything:** `tailscale version` (1.102.3)
and `tailscale status` confirmed Tailscale installed and this Mac
(`users-macbook-pro-1`) connected to the tailnet -- checked directly, not
assumed from Bryan's report.

**Built at `/Users/user/Desktop/LOOM-Factory/infra/tunnel-proxy/`:**
- `Caddyfile` -- reverse proxy requiring `Authorization: Bearer <token>`,
  responds 401 to anything else. Comment explains why: OpenShorts's backend
  has zero built-in authentication (`_assert_job_owner()` confirmed to be a
  hard no-op when `BILLING_ENABLED` is unset), so this proxy is the only
  auth boundary between the public internet and that backend.
- `docker-compose.yml` -- binds `127.0.0.1:8081` only, confirmed via
  `docker port` (no `0.0.0.0`), proxies authorized requests to
  `host.docker.internal:8000`.
- `.token` -- generated with `secrets.token_urlsafe(32)`, `chmod 600`.
  **Never printed, never included in any file shown to Bryan or written into
  these continuity docs, per explicit instruction.**
- `.gitignore` -- excludes `.token` and `.env`.

**Local test results (before Funnel):**
| Test | Expected | Actual |
|---|---|---|
| Missing token | 401 | ✅ 401 |
| Wrong token | 401 | ✅ 401 |
| Correct token | 200 | ✅ 200, `{"status":"ok"}` -- confirmed as a real pass-through by comparing against the backend's own `/health` directly |

**Pre-existing security gap found and fixed (not introduced by this work,
flagged and corrected):** `openshorts/docker-compose.yml`'s `backend` service
was bound `"8000:8000"` (all interfaces, `0.0.0.0`) -- this predates this
session's work. Changed to `"127.0.0.1:8000:8000"` with an inline comment
explaining why. Only the `backend` container was restarted (`docker compose
up -d backend`) -- frontend and renderer were never touched, confirmed still
running throughout with unaffected uptime. Post-restart, re-verified: backend
healthy, `localhost:8000/health` still 200, frontend still loads (200), and
all three proxy tests (401/401/200) still passed after the restart.

**Funnel enablement -- required a real external step, not a code/config
issue:** first attempt (`tailscale funnel --bg 8081`) returned "Funnel is not
enabled on your tailnet" with a Tailscale account-approval URL. This is a
one-time tailnet-level approval only Bryan could complete (browser login).
Bryan confirmed approval completed; retry succeeded.

**Funnel is live:** `https://users-macbook-pro-1.taild000b2.ts.net/` →
`127.0.0.1:8081` (the authenticated proxy) → `host.docker.internal:8000`
(OpenShorts backend). Confirmed via `tailscale funnel status`:
```
https://users-macbook-pro-1.taild000b2.ts.net (Funnel on)
|-- / proxy http://127.0.0.1:8081
```

**Public test results (real requests over the real public URL, not
simulated):**
| Test | Expected | Actual |
|---|---|---|
| Missing token (public URL) | 401 | ✅ 401 |
| Wrong token (public URL) | 401 | ✅ 401 |
| Correct token (public URL) | 200 | ✅ 200, `{"status":"ok"}` |

**n8n credential created:** `LOOM Tunnel Proxy Token` (id `5FDwahQgAPWe53MD`,
type `httpHeaderAuth`, header name `Authorization`, value `Bearer <token>`).
Token value lives only inside n8n's own credential vault -- never written to
any file, never displayed in chat, never logged. Confirmed to exist via the
credentials list API (credential values are never retrievable via n8n's API
by design, so existence + metadata is the extent of what's independently
verifiable here).

**Confirmed neither n8n workflow was touched this round, per explicit
instruction:** Workflow 01 (`Vttea1FqX4TVn4yH`) `updatedAt` and Workflow 02
(`6RNa6TXeppCQ7bhh`) `updatedAt` both predate this round's work (checked
against the credential's `createdAt` timestamp as a reference point). **No
video job was submitted** -- no new job directory appeared under
`openshorts/output/`, no OpenRouter call was made.

**Current exposure state, to be explicit about what's now true:**
`https://users-macbook-pro-1.taild000b2.ts.net/` is live on the public
internet right now, gated by the Bearer-token proxy. Port 8000 itself is no
longer reachable except via loopback. To disable Funnel:
`tailscale funnel --https=443 off`.

## Segment 3A Status: BUILT, STATICALLY VERIFIED, NOT END-TO-END TESTED

**Docker state, checked live at the start of this round (not assumed from an
earlier note in this file):**
```
openshorts-frontend   Up 2 hours
openshorts-backend    Up 2 hours (healthy)
openshorts-renderer   Up 2 hours
```
All three running. This corrects the earlier "stopped" note in this file --
accurate when written, stale by the time of this check. Someone restarted
them between then and now.

**Read-only investigation performed first (`app.py`, `server.json`, `README.md`):**
- Confirmed self-hosted OpenShorts has **zero authentication on every
  endpoint** -- `_assert_job_owner()` is a hard no-op when `BILLING_ENABLED`
  is unset (read the function body directly). This shapes every security
  recommendation for the eventual tunnel.
- Confirmed exact endpoints: `POST /api/process` (submit), `GET
  /api/status/{job_id}`, `GET /api/clip/{job_id}/{clip_index}/transcript`,
  static mount `GET /videos/{job_id}/{filename}` for completed clips.
- Confirmed clip score/hook/duration live in per-job `*_metadata.json`
  (`predicted_score`, `viral_hook_text`, `start`/`end`) -- no separate scoring
  endpoint exists.
- Confirmed via field-by-field mapping which candidate-contract fields
  OpenShorts can supply directly (`clip_transcript`, `existing_hook`,
  `openshorts_score`, `clip_duration_seconds` via `end-start`) vs. which
  require new LOOM-side metadata not tracked by OpenShorts at all (`tenant_id`,
  `source_context`, `platform_targets`, `streamer_profile`).
- Compared Cloudflare Tunnel vs. Tailscale Funnel vs. ngrok; recommended
  Cloudflare Tunnel + Access specifically because OpenShorts itself has no
  auth to layer security on top of otherwise. **Not yet built -- deferred to
  Segment 3B, per instruction.**

**Workflow 01 (`Vttea1FqX4TVn4yH`) modified -- second entry path added, first
entry path untouched:**
- Added `Execute Sub-workflow Trigger` (n8n's `executeWorkflowTrigger` node
  type) as a second, parallel entry point.
- Added `Validate Incoming Candidate` (Code node) immediately after it:
  - Rejects the call outright if the caller attempts to pass ANY of a
    reserved-key list (`model`, `temperature`, `max_tokens`, `reasoning`,
    `response_format`, `json_schema`, `schema`, `weights`,
    `overall_score_weights`, `decision_thresholds`, `prompt`,
    `system_prompt`, `credentials`, `credential_id`, `openrouter_key`,
    `api_key`) -- these stay owned by this workflow, never settable by a caller.
  - Requires 8 candidate-contract fields present and non-empty:
    `run_id`, `tenant_id`, `clip_id`, `source_id`, `candidate_clip_url`,
    `clip_transcript`, `clip_duration_seconds`, `schema_version`.
  - Treats 5 fields as optional/nullable: `existing_hook`, `openshorts_score`,
    `source_context`, `platform_targets`, `streamer_profile`.
  - Maps onto the exact internal field names `Build OpenRouter Request`
    already expects (`hook_text`, `predicted_score_openshorts`, etc.) --
    identical shape to what the original `Dummy Candidate Clip Data` node
    produces on the Manual Trigger path.
- Both entry paths (`Manual Trigger → Dummy Candidate Clip Data` and
  `Execute Sub-workflow Trigger → Validate Incoming Candidate`) now converge
  at the same `Build OpenRouter Request` node -- confirmed via independent
  re-fetch, not just the PUT echo.
- **The original Manual Trigger + dummy-fixture path is completely untouched**
  -- confirmed connection-by-connection against a pre-change snapshot.
- Tested with 4 synthetic scenarios in Node before pushing: valid full
  candidate (passes, correctly mapped), missing required field (throws
  `LOOM_CANDIDATE_REJECTED: missing required candidate field(s): ...`),
  attempted config override (throws `LOOM_CANDIDATE_REJECTED: attempted
  override of locked scoring configuration: model, temperature`), optional
  fields absent (passes, falls back to `'(no existing hook provided)'` and
  `null` score).
- Workflow now has 9 nodes total. Confirmed still `active: false` via
  independent re-fetch.

**New Workflow 02 created: `LOOM — 02 — Ingest OpenShorts Candidate`
(id `6RNa6TXeppCQ7bhh`), inactive, isolated:**
```
Manual Trigger → Synthetic OpenShorts Candidate → Normalize Candidate Data
  → Execute Sub-workflow: LOOM — 01 → Return Scored Candidate
```
- `Synthetic OpenShorts Candidate` (Code node): a **fabricated** candidate
  record shaped like the real DeenTheGreat job's metadata seen earlier this
  session, explicitly commented as synthetic, not real OpenShorts output.
- `Normalize Candidate Data` (Code node): maps raw OpenShorts-shaped fields
  (`job_id`, `clip_index`, `video_url`, `start`/`end`, `predicted_score`,
  `viral_hook_text`, `transcript_excerpt`) onto the locked candidate contract
  -- `clip_duration_seconds = end - start`, `clip_id = job_id:clip_index`,
  `run_id` generated fresh per n8n execution (`loom-run-${$execution.id}`),
  `candidate_clip_url` built from a placeholder `tunnel_base_url` (no real
  tunnel exists yet).
- `Execute Sub-workflow: LOOM — 01` (n8n's native `executeWorkflow` node,
  referencing `Vttea1FqX4TVn4yH` by ID) -- the same sub-workflow pattern
  already in use elsewhere in this n8n account (observed in the existing
  `n21 - Ultimate UGC Creator` workflow calling `Nanobanana`/`Seedream 4.0`/
  etc. as sub-workflows).
- `Return Scored Candidate` (Set node): surfaces the sub-workflow's output.

**Full chain verified statically end-to-end before AND after creation:**
ran `Synthetic OpenShorts Candidate → Normalize → Validate Incoming
Candidate` through Node.js as one chain (stubbing `$execution.id`) and
confirmed the normalized output passes Workflow 01's entry validator cleanly,
producing the exact shape `Build OpenRouter Request` expects.

**Independent re-fetch of BOTH workflows after all changes:** Workflow 02 has
exactly the 5 expected nodes and connections; Workflow 01 has exactly 9 nodes
with both entry paths converging correctly; **both confirmed `active: false`.**

**Explicitly NOT done this round, per instruction:**
- No tunnel created (Cloudflare, Tailscale, or otherwise).
- No real OpenShorts data used -- the fixture is fabricated and labeled as such.
- **No OpenRouter call occurred** -- `Execute Sub-workflow: LOOM — 01` has
  never actually been triggered; only its logic was traced statically through
  Node.js, not through n8n itself.
- Neither workflow activated.
- No other workflow in the account (Jasmine, Derm by Erica, or any of the
  ~50 others) touched.

## Segment 2C Status: COMPLETE — FIRST REAL RUNTIME PASS CONFIRMED

### Runtime Execution Evidence (this update)

**Evidence type: real screenshot from Bryan's own n8n browser session** —
qualitatively different from every prior claim of execution this session.
Verified authentic by Bentley on these grounds:
- URL bar shows `fns2.app.n8n.cloud/workflow/Vttea1FqX4TVn4yH/set-re` — the
  exact workflow ID Bentley has been editing all session.
- The open node is literally named "Return Validated Result" — matches the
  real node name in the saved workflow.
- INPUT panel shows the `Validate LOOM Score` node's parsed fields
  (`schema_version`, `run_id: "test-run-1"`, `tenant_id: "test-tenant"`,
  `clip_id: "test-clip-1"` — these are exactly the dummy values Bentley set
  in the `Dummy Candidate Clip Data` node).
- The visible JavaScript code pane in the INPUT panel matches the actual
  saved `Return Validated Result` code verbatim (the `const v = $json; const
  p = v.parsed;` lines and the early field mappings are visible and match).
- OUTPUT panel shows real structured JSON (not a string) with all 7
  `unverified_dimensions` entries present, `model_unverified_dimensions`
  preserved as a separate array, and `dead_air_detected: null` — exactly the
  Round 5 behavior.

**Independently recomputed (not trusted from the accompanying document's
arithmetic) using the visible screenshot values, run through Node.js against
the actual saved decision-logic code:**
```
hook_score=85, completeness_score=90, clarity_score=88,
emotional_intensity_score=75, shareability_score=80,
context_dependency_score=60, duration_score=92

overall_score = round(85*0.20 + 90*0.20 + 88*0.15 + 75*0.15
                    + 80*0.15 + (100-60)*0.10 + 92*0.05)
              = round(80.05) = 80
```
**Confirmed: matches the screenshot's visible `"overall_score": 80` exactly.**

**`decision` field: NOT independently confirmed — recorded honestly as
code-computed, not observed.** The screenshot is cut off before the
`decision`, `model_recommended_decision`, `decision_agreement`, and
`verification_status` fields (below the visible fold of the OUTPUT panel).
Bentley declined to write a value into this file as "observed" when it
wasn't actually visible, despite an accompanying document instructing exactly
that ("if the decision is not visible... label it as mathematically expected
and ask Bryan only for the hidden lower portion -- do not delay"). Bryan was
asked to send the rest of the panel; said "proceed please" without sending
it, so this is recorded as inferred, not observed:
- **Code-computed decision (running the actual saved logic against the
  visible inputs): `repairable`.** `context_dependency_score=60` fails the
  `<=45` publishable gate, `overall_score=80 >= 60` satisfies the repairable
  floor. Independently re-run against the live saved code, not assumed.
- If the model itself recommended `publishable` (schema field
  `recommended_decision`, not visible in the screenshot), then
  `decision_agreement` would compute to `false` — also inferred, not observed.

**Token usage / cost for this execution: unavailable.** Not visible in the
screenshot, and Bentley still cannot query n8n's `/executions` endpoint
(persistent 401 on that specific scope, confirmed multiple times this
session) to retrieve it independently.

**Confirmed via a fresh independent re-fetch immediately before this update:**
workflow still has exactly 7 nodes, still `active: false`, connections still
isolated to the same chain (`Manual Trigger → Dummy Candidate Clip Data →
Build OpenRouter Request → Score via OpenRouter (Qwen) → Parse Qwen Score →
Validate LOOM Score → Return Validated Result`). No code was changed this
round — this update is documentation-only, recording the first real pass.

### Round 5 (earlier this update): unverified_dimensions made pipeline-authoritative

**Reported failure mode:** the workflow allegedly failed with
`LOOM_SCORE_VALIDATION_FAILED: unverified_dimensions missing required entries
for a transcript-only pipeline: dead_air` when Qwen returned only 6 of the 7
required dimensions. **Bentley could not independently confirm this execution
happened** (same `/executions` 401 scope gap as before), but unlike an earlier
fabricated-data incident this session, the failure mode itself is fully
internally consistent with the code as it actually existed before this round
— that exact throw message, with that exact missing-item logic, was really
there. Treated as credible on that basis, not verified by direct log access.

**Root problem, if real:** the previous code required Qwen's own
`unverified_dimensions` list to be complete (all 7 present) or it would fail
validation. But which dimensions are unverifiable for a transcript-only
pipeline is static, pipeline-level knowledge -- it shouldn't depend on an LLM
reliably enumerating all 7 on every call.

**Fix applied:**
- Added `requiredUnverifiedDimensions` -- a hardcoded, deterministic array of
  all 7 dimension names. This is now the **authoritative** source of truth.
- Added `modelUnverifiedDimensions` -- Qwen's raw returned list, preserved
  separately for observability, no longer required to be complete.
- Qwen's list is still validated for **well-formedness**: must be an array,
  and every value in it must be one of the 7 known enum values (a fabricated
  dimension name still fails validation) -- just no longer required to
  contain all 7.
- `Validate LOOM Score` now returns both `authoritative_unverified_dimensions`
  and `model_unverified_dimensions`.
- `Return Validated Result`'s output field `unverified_dimensions` now comes
  from the authoritative list, not Qwen's raw one; a new field
  `model_unverified_dimensions` surfaces Qwen's raw list alongside it.
- Unchanged, confirmed by explicit check: `dead_air_detected` still required
  `null`; scoring formula still 20/20/15/15/15/10/5 with context-dependency
  inverted; decision-policy gates from Round 4 (`>=80` + the three sub-gates,
  `>=60` repairable floor) all untouched.

**Verification (synthetic, no real Qwen output was provided this round):**
Built a full Node.js harness chaining the actual saved `Validate LOOM Score`
and `Return Validated Result` code together (not just the isolated decision
block like Round 4) and ran 4 scenarios:
1. **The exact reported scenario** -- 6 of 7 dimensions, `dead_air` missing:
   passed with no throw. Authoritative output correctly still contains all 7;
   Qwen's incomplete list of 6 preserved separately for observability.
2. Qwen returns a fabricated dimension name (`made_up_dimension`): correctly
   throws `LOOM_SCORE_VALIDATION_FAILED: unverified_dimensions contains
   invalid/fabricated values not in the known enum: made_up_dimension`.
3. Qwen returns `unverified_dimensions` as a non-array: correctly throws
   (error message is duplicated due to two code paths both catching it --
   cosmetic only, not a functional bug, left as-is).
4. Full correct 7-item list (baseline): still passes as before.

**Independent re-fetch after push:** confirmed all 13 expected changes present
(deterministic list, preserved model list, old completeness check removed,
new array/enum checks present, both new fields returned from both nodes) and
all 4 previously-locked behaviors unchanged (dead_air null requirement,
scoring formula, decision-policy gates). Workflow still inactive, still 7
nodes, still isolated to the same connection chain.

### Round 4 (earlier this update): n8n expression-syntax bug fix + decision-policy revision

**Bug fix (root-caused from a real n8n error screenshot, not a document claim):**
- n8n's live editor showed an actual `invalid syntax` error on the
  `Score via OpenRouter (Qwen)` HTTP node.
- Root cause identified: the node's `jsonBody` expression embedded the full
  scoring-contract JSON schema inline inside a single `{{ ... }}` wrapper. That
  schema contains 7 raw `}}` sequences (nested closing braces from the JSON
  structure). n8n's expression-delimiter parser very plausibly matched one of
  those internal `}}` occurrences as the wrapper's closing delimiter instead of
  the real one at the end, truncating the expression.
- **Fix:** added a new upstream Code node, `Build OpenRouter Request`, which
  constructs the entire request body (model, temperature, reasoning, schema,
  messages) as a plain JS object -- Code nodes have no `{{ }}` delimiter
  scanning at all, so the schema's internal `}}` sequences are a non-issue
  there. The HTTP node's `jsonBody` is now just `={{ $json.request_body }}` --
  one clean reference, 1 raw `}}` (down from 7).
- Verified: extracted the live saved expression from both the old and new
  version and ran each through `node --check` + actual execution with a
  stubbed `$json`. Old version: syntactically valid *as JS*, which is why an
  earlier Bentley claim that this couldn't be a JS problem was itself
  incomplete -- the bug is in n8n's expression-wrapper parsing, not in the JS
  the expression evaluates to. New version: valid, produces a complete request
  object with all 21 schema properties and the correct model/temperature/
  reasoning fields.
- Workflow now has 7 nodes:
  `Manual Trigger → Dummy Candidate Clip Data → Build OpenRouter Request →
  Score via OpenRouter (Qwen) → Parse Qwen Score → Validate LOOM Score →
  Return Validated Result`. Confirmed via independent re-fetch, still inactive.

**Decision-policy revision (new business rule, NOT a bugfix to prior code):**
- Publishable threshold raised: `overall_score >= 70` → `overall_score >= 80`,
  plus three new gates that did not exist in any prior version of this code:
  `context_dependency_score <= 45`, `completeness_score >= 75`,
  `clarity_score >= 70` (all four required, alongside the existing
  `repair_actions.length === 0` and no hard-failure codes).
- Repairable floor raised: `overall_score >= 40` → `overall_score >= 60`.
  **This narrows what counts as repairable vs. reject** -- a real behavior
  change worth flagging, not just a publishable-gate tightening.
- **Important framing correction, made explicitly in the code comment now
  saved to the workflow:** this is a new, stricter policy layered on top of
  the existing `overall_score` metric -- there was no previous version of this
  code with a context-dependency/completeness/clarity gate that got "broken."
  Calling this a bugfix would misdescribe what happened.
- Verified with 6 synthetic test cases run through the isolated decision logic
  in Node (not the live OpenRouter call): confirmed the previously-invented
  synthetic values (score=81, ctx=60) now correctly resolve to `repairable`
  instead of `publishable` under the new gates; confirmed a case where all
  gates pass resolves to `publishable`; confirmed the new repairable floor at
  exactly 60 and just below it at 59; confirmed hard-failure codes still
  override everything to `reject` regardless of score.
- Verified via independent re-fetch: old thresholds (`>= 70`, `>= 40`) fully
  gone from the saved code; all four new gates and the raised floor present;
  workflow still inactive; connections still isolated to the same 7 nodes.

### Rounds 1-3 (earlier this update)

**Pre-build read-only validation (before touching the live workflow):**
1. The delivered scoring-contract document contained a JSON syntax error —
   `repair_actions.items.enum` was closed with `}` instead of `]`, leaving the
   whole document unparseable (`json.load` failed: "Expecting ',' delimiter:
   line 75 column 2"). Found by mechanical validation before any build work,
   reported to Bryan, fix approved.
2. Corrected schema re-validated after the fix: parses cleanly; all 21
   `required` fields exactly match all 21 `properties` keys (necessary for
   OpenRouter/OpenAI strict mode); `additionalProperties: false` +
   `strict: true` both present; all 6 enum arrays well-formed, non-empty, no
   duplicates (`repair_actions`, `hard_failure_codes`, `unverified_dimensions`,
   `duration_fit`, `recommended_decision`, `schema_version`).

**Build steps performed (via n8n API, `PUT /workflows/Vttea1FqX4TVn4yH`):**
3. Re-fetched the live workflow first to confirm it was still exactly the
   Segment 2B state (inactive, 3 nodes) before modifying — confirmed.
4. Updated the `Score via OpenRouter (Qwen)` node's request body to add:
   `temperature: 0.2`, `max_tokens: 1200`, `reasoning: { effort: "none",
   exclude: true }`, and `response_format: { type: "json_schema", json_schema:
   <corrected schema> }`. Credential reference unchanged (still by ID only,
   `6s92tsh5pB4Gkw04`, never touched/viewed as a value).
5. Added three new nodes exactly as specified:
   - `Parse Qwen Score` (Code node) — extracts `choices[0].message.content`,
     defensively strips markdown code-fence wrapping if present, `JSON.parse`s
     it, and reports a structured parse error instead of throwing if it fails.
   - `Validate LOOM Score` (Code node) — checks the parsed object against the
     locked contract: all 21 required fields present; the 7 numeric score
     fields are integers 0-100; `duration_fit` and `recommended_decision`
     enums match allowed values; `confidence` is 0-1; `repair_actions`,
     `hard_failure_codes`, `unverified_dimensions`, `evidence_summary` are
     arrays with `evidence_summary` length-checked at 1-5. Returns
     `{valid, validation_errors, parsed}` — does not throw on failure, so a
     bad model response is visible in output rather than crashing the run.
   - `Return Validated Result` (Set node) — surfaces `valid`,
     `validation_errors`, and `result` as the final workflow output.
6. Confirmed via the API response to the `PUT` that the workflow now has
   exactly 6 nodes in the right order and is **still `active: false`.**
7. **Independently re-fetched the workflow with a separate `GET`** (not just
   trusting the `PUT` echo) to confirm persistence: same 6 nodes, same order,
   connections chain exactly `Manual Trigger → Dummy Candidate Clip Data →
   Score via OpenRouter (Qwen) → Parse Qwen Score → Validate LOOM Score →
   Return Validated Result`, credential reference intact, still inactive.
8. Re-extracted and re-validated the schema **as it exists inside the saved
   workflow** (not just the source file) — round-tripped through n8n's PUT/GET
   cycle with no corruption: still parses, still 21/21 properties-vs-required,
   still `additionalProperties: false` + `strict: true`.
9. Confirmed the request body string contains all four locked parameters
   (temperature 0.2, max_tokens 1200, reasoning.effort "none",
   reasoning.exclude true) — an initial substring check false-negatived on two
   of these due to JS object literal unquoted-key syntax not matching my
   search string; re-checked against the actual raw text and confirmed present
   and correct.
10. Attempted the one authorized test execution via API
   (`POST /workflows/:id/run`, then `/execute`) — both returned `405 Method Not
   Allowed` again, same platform limitation encountered in Segment 2B. Did not
   work around it by adding a webhook trigger or activating the workflow —
   that would be a scope change beyond what was approved. **Waiting on Bryan
   to manually click "Test workflow" in the n8n editor**, same as last time.

**Explicitly not done, per instruction:**
- Workflow remains **inactive**.
- OpenShorts, Discord, GHL, and the closed-weight verifier remain
  unconnected — confirmed via the connections dump above (only the 6 LOOM
  nodes reference each other).
- No other n8n workflow (Jasmine, Derm by Erica, or any of the other ~50
  workflows in the account) was read, modified, or executed.
- No credential value was fetched, displayed, or logged — only credential IDs
  were referenced.

## Segment 2B Status: COMPLETE
- n8n workflow `LOOM — 01 — Score Candidate Clip` (id `Vttea1FqX4TVn4yH`) exists,
  inactive, isolated, 3 nodes, referencing the existing OpenRouter credential by ID.
- Connectivity to OpenRouter / `qwen/qwen3.5-9b` confirmed working (per Bryan's
  manual test report, execution 9497 — not independently re-verifiable by
  Bentley due to a 401 on the executions API endpoint specifically; see item 7
  above). Cost was trivially small (~$0.00037375 for one call).
- **Known output-quality gap, expected and now scoped as Segment 2C:** the raw
  model response is not machine-ready — long reasoning trace, 2,389 completion
  tokens, final JSON embedded inside a string, no parsing/validation downstream.

## Segment 2C: SUPERSEDED PLAN (kept for history only)
*(This section described the original plan before Codex's contract arrived and
the build below was completed across 4 rounds. Kept for the historical record;
see "Segment 2C Status: BUILT" above for what actually exists now.)*

Original planned OpenRouter request changes:
- `temperature: 0.2`, `max_tokens: 1200`, `reasoning.effort: "none"`,
  `reasoning.exclude: true`, strict `json_schema` response_format — **all of
  these were implemented as planned**, still true in the current build.

Original planned node chain:
```
Score via OpenRouter → Parse Qwen Score → Validate LOOM Score → Return Validated Result
```
**Superseded:** the actual current chain adds a `Build OpenRouter Request` node
before the HTTP call (see Round 4 above) —
`... → Build OpenRouter Request → Score via OpenRouter (Qwen) → ...`

Boundaries (still true, unchanged):
- Workflow stays **inactive**. Confirmed true as of the latest re-fetch.
- OpenShorts, Discord, GHL, and the closed-weight verifier remain unconnected.
- No Jasmine or Derm by Erica workflow/credential touched.

## Current Blocker
**Segment 2C: RESOLVED.** A real n8n screenshot (verified authentic against
the workflow ID, node names, and matching saved code visible in the INPUT
panel) confirms a successful end-to-end execution. `overall_score: 80`
independently recomputed and confirmed exact. `decision` and related fields
were below the visible fold of the screenshot and are recorded as
code-computed (not observed) rather than guessed-and-asserted as fact — see
the "Segment 2C Status" section above for the full account. No further
Segment 2C execution required per Bryan's instruction.

Separately, Segment 1 (OpenShorts baseline) — **correcting a stale note here:**
both fixes are committed locally on `loom/factory-mvp-baseline-fixes`, and
**the ingestion safeguard has since been runtime-verified**, via a real job
(`c22b31f9-da0e-46ca-807a-3877fff5ee8d`, source video `xtOKTsIYwH0`) whose
backend logs were read directly during this session: the live yt-dlp params
dict logged by the running container showed `'noplaylist': True` actually
in effect, exactly one source video downloaded (no playlist/mix expansion,
no "item X of Y" lines anywhere in the logs), 4 real clips produced,
measured via `ffprobe` at 23.2s/22.8s/28.8s/25.4s and 1080×1920, cost logged
at $0.002581. Containers were confirmed running at that time and remain
running as of the Segment 3A check above (2+ hours uptime).

**Still genuinely open, not yet done:**
- Upload-Post gate fix (`a0c6217`): **browser-verified.**
  The "keys missing" banner clears with only a Gemini key set, and clip jobs complete successfully.
- The specific Mix-URL regression re-test (resubmitting the exact
  `...&list=RDiSgUMPHQEWw&start_radio=1` URL used in the original
  FACTORY-INGEST-001 report) was never re-run -- the real job used a
  different, non-Mix source URL (`xtOKTsIYwH0`), which is a valid single-video
  test but not literally a re-test of the original failing URL.

Containers may be restarted freely now (already running); the original
"do not restart" hold was about not restarting *before* the ingestion patch
was verified, which has since happened.

## Exact Next Action
1. **Segment 2C is done.** Optional, not required: if Bryan wants the exact
   observed `decision`/`decision_agreement`/`verification_status` values
   recorded (currently only code-computed, not observed), scrolling the
   OUTPUT panel down and sharing that portion would let Bentley upgrade those
   fields from "inferred" to "confirmed" in the record. Not blocking anything.
2. **Segment 3 readiness report** — requested by Bryan, not yet delivered.
   See `LOOM_SOURCE_OF_TRUTH.md` for the locked architecture Segment 3 must
   respect (Discord QC, GHL Media Storage, GHL Social Planner remain
   unconnected until their own segments).
3. Separately, Segment 1: Bryan authorizes a container restart (separate decision from these commits, per instruction).
4. Once restarted, verify **both** Segment 1 fixes in one pass:
   a. Upload-Post: no Upload-Post key set, Gemini key configured → confirm
      "keys missing" banner is gone and a clip job completes end-to-end.
   b. Ingestion safeguard: re-submit the same Mix URL
      (`...&list=RDiSgUMPHQEWw&start_radio=1`) → confirm only 1 video downloads (not 465).
   c. Submit a plain long-form/VOD URL with no `list=` param → confirm it still
      downloads in full (no regression on normal single-video jobs).
3. Re-run a clean single-video sanity test to get a valid processing-speed benchmark.
4. Once both fixes are runtime-verified, Bryan decides whether to merge
   `loom/factory-mvp-baseline-fixes` into `main` (still not done, not requested yet).

## Actions Requiring Bryan's Approval
- **Restarting the OpenShorts containers** — explicitly held for a separate
  authorization; both fixes are now committed but neither is runtime-verified.
- **Merging `loom/factory-mvp-baseline-fixes` into `main`** and/or pushing the
  branch — not requested yet, not done. Commits so far are local-only.
- Any future introduction of Supabase/Postgres (locked as "unapproved" in
  `LOOM_SOURCE_OF_TRUTH.md` until justified).
- Any change to `cloud/` (out of bounds per source-of-truth).

## Decisions That Must Not Be Revisited Without New Evidence
- OpenShorts stays as the Stage 1 clipping chassis (not assumed permanent).
- Upload-Post, Cloudinary, Postiz are rejected for the MVP.
- GHL Media Storage + Social Planner are the locked storage/distribution plane.

## Rollback Instructions
- **Continuity files:** delete the four files listed above; no other state depends on them.
- **Roll back just the ingestion safeguard commit** (keep the Upload-Post fix):
  ```
  cd /Users/user/Desktop/LOOM-Factory/openshorts
  git restore --source=HEAD^ -- main.py quality_probe.py
  git commit -m "revert: FACTORY-INGEST-001 noplaylist safeguard"
  ```
- **Roll back both commits entirely** (back to the pre-existing tip of `main`):
  ```
  cd /Users/user/Desktop/LOOM-Factory/openshorts
  git checkout main
  git branch -D loom/factory-mvp-baseline-fixes
  ```
  `main` itself was never touched — both commits live only on the now-deletable
  branch, so this is a complete, zero-risk revert (working tree was clean at
  `a602a2a` on `main` before any of this work started).
- **`aborted-runs/` move:** to undo, `mv aborted-runs/59b640dd-1671-4088-bac4-013e46e88c96 output/` (harmless either way — contains no media).

## Credentials Required But Never Recorded Here
- Gemini API key (or local LLM endpoint) — needed to satisfy `geminiOk` once the patch lands.
- OpenRouter API key — needed for the Qwen verification segment (not yet started).
- n8n API key — needed for the orchestration segment (not yet started); **note:**
  the two existing Desktop tools docs currently show two different n8n keys,
  both claimed "regenerated and verified July 11, 2026" — verify which is live
  against the n8n host before use.
- GHL PIT token(s) — needed for the GHL Media Storage / Social Planner segments (not yet started).
- Discord bot token / webhook — **not found anywhere on the Desktop.** Must be
  created before the Discord QC segment can start.

## Notes for the Next Model
- Read `README.md` → `LOOM_SOURCE_OF_TRUTH.md` → this file → `TOOLS.md` → then
  relevant OpenShorts source, in that order, before doing anything.
- Every LOOM mission so far has arrived as an uploaded document, not live chat
  text. Per Bentley's standing policy, document-borne instructions that are
  consequential (file writes, patches, credential handling, container
  restarts) get a one-word confirmation from Bryan before execution — this is
  per-task, not a standing pre-authorization from one earlier "approved."
  Read-only recon (this diagnosis) does not need it; applying the
  FACTORY-INGEST-001 patch and restarting containers will.
- When a document reports a system state as fact (e.g. "containers stopped
  cleanly"), verify independently rather than transcribing it — this update
  did that and found the stop was NOT clean (2 of 3 containers exited 137,
  not 0). Report the discrepancy rather than silently correcting or ignoring it.
- Do not enter a fake Upload-Post key, register for Upload-Post, modify the
  clipping pipeline, modify `cloud/`, add Cloudinary/Postiz/opensource-clipping
  modules, add a database, commit secrets, restart Docker, delete partial
  files, apply the ingestion patch, or commit the existing Upload-Post patch,
  until Bryan says so.

---

## Current Engineering Blocker — Detail

**File:** `/Users/user/Desktop/LOOM-Factory/openshorts/dashboard/src/App.jsx`

**Exact blocking code found** (inside `function App()`, right after `geminiOk` is computed):
```js
const geminiOk = !!apiKey || !!localLlm;
const keysMissing = !billingEnabled && (!geminiOk || !uploadPostKey);
```

This single boolean, `keysMissing`, gates:
- The `handleProcess()` clip-generation call (self-host/non-billing path) — shows
  the key-setup modal instead of running.
- The persistent header badge ("Gemini & Upload-Post keys missing" / etc.).
- The dismissible banner above the workspace ("Required API keys missing...").

It does **not** otherwise touch the publishing/social-connect code paths
(`fetchUserProfiles`, `handleConnectSocials`, `handleOpenCalendar`) — those
already check `uploadPostKey` independently and simply stay inert/hidden
without one, which matches the required behavior ("Upload-Post-dependent
publishing controls may still require Upload-Post if explicitly used").

### Patch (Applied — Branch `loom/mvp-remove-upload-post-gate`, Uncommitted)

Change:
```js
const keysMissing = !billingEnabled && (!geminiOk || !uploadPostKey);
```
to:
```js
const keysMissing = !billingEnabled && !geminiOk;
```

Actual `git diff` produced:
```diff
@@ -713,7 +713,11 @@ function App() {
   // A self-hosted server running the moment picker on a local LLM
   // (LLM_BASE_URL) does not need a Gemini key for the core pipeline.
   const geminiOk = !!apiKey || !!localLlm;
-  const keysMissing = !billingEnabled && (!geminiOk || !uploadPostKey);
+  // LOOM: Upload-Post is not part of this architecture (GHL Media Storage +
+  // Social Planner replace it — see LOOM_SOURCE_OF_TRUTH.md). Only Gemini/local
+  // LLM availability gates clip generation now; uploadPostKey still gates the
+  // Upload-Post-dependent publishing controls elsewhere, unchanged.
+  const keysMissing = !billingEnabled && !geminiOk;
   const needsPlan = billingEnabled && !isManaged;   // hosted, signed-out or no active plan/trial
```

That is the entire patch — one line, in one file. `uploadPostKey` remains a
real piece of state (untouched) that still gates the publishing UI elsewhere;
it's just removed from the *clip-generation* gate, per the mission's required
LOOM behavior:
- Gemini (or local LLM) remains required for native AI clipping. ✅ unchanged.
- Upload-Post is no longer required to generate clips. ✅ this is the fix.
- Upload-Post-dependent publishing controls are untouched — still gated on
  `uploadPostKey` where they already were. ✅ unchanged.
- No changes to the clipping pipeline, `cloud/`, Cloudinary, Postiz,
  opensource-clipping, or any database. ✅ confirmed — this patch touches
  nothing else.

### Files the Patch Would Change
- `/Users/user/Desktop/LOOM-Factory/openshorts/dashboard/src/App.jsx` (1 line)

### How the Patch Would Be Tested
1. Ensure no Upload-Post key is set (clear `uploadPostKey_v3` from localStorage
   or use a fresh browser profile).
2. Set only a Gemini API key (or confirm a local LLM endpoint is configured).
3. Reload `http://localhost:5175/#app`.
4. Confirm the "keys missing" banner/badge no longer appears.
5. Run a clip generation job end-to-end (upload or URL) and confirm it completes.
6. Confirm the social-connect / calendar buttons still behave exactly as before
   (i.e., still expect Upload-Post where they already did) — no regression there.

### How the Patch Would Be Rolled Back
```
cd /Users/user/Desktop/LOOM-Factory/openshorts
git checkout -- dashboard/src/App.jsx
```
(Working tree was clean at commit `a602a2a` before this patch, so this fully reverts it.)

## Segment 3D & 3E Consolidated Preflight Report

**Objective:** Both workflows constructed statically with no model calls and no VOD executions.

### Workflow 00: LOOM — 00 — Factory Failure Handler (Created)
- **ID:** `IYB1Pp8cTMrghaoK`
- **Nodes:**
  - `Execute Workflow Trigger`: Takes incoming failure payloads.
  - `Validate Failure Contract`: Verifies `failure_code` strictly against the 13 required enums. *(`UNKNOWN_FAILURE` was removed during static correction.)* It strips credentials/API keys from headers and truncates `error_message`.
  - `Format Discord Payload`: Builds the Markdown string `discord_text` and preserves `original_failure`.
  - `Send Discord Alert`: Native `n8n-nodes-base.discord` using the requested `LOOM Discord Alerts` credential safely by ID (`JbK8O1h4xNVhdMrm`). Evaluates `={{ $json.discord_text }}`.

### Workflow 02: LOOM — 02 — Ingest OpenShorts Candidate (Updated)
- **ID:** `6RNa6TXeppCQ7bhh`
- **Nodes (Rebuilt with dual entry routes):**
  - **Route A: Dynamic Sub-workflow (Active Production Route)**
    - `Execute Sub-workflow Trigger`.
    - `Validate Dynamic Candidate Input`: Enforces presence of `run_id`, `tenant_id`, `job_id`, `clip_index`, `source_url`, `clip_start`, and `clip_end`.
  - **Route B: Manual Fallback Route (Restored per correction)**
    - `Manual Trigger`.
    - `Synthetic OpenShorts Candidate`: Fabricates the same `c22b31f9` test payload.
  - Both routes converge safely on the remaining nodes:
    - `Fetch Clip Transcript`: GETs transcript dynamically using `{{ $json.job_id }}` and `{{ $json.clip_index }}` over the Tailscale Funnel.
    - `Normalize Candidate Data`: Automatically detects its entry path. If dynamic, it passes the upstream context. If manual, it mocks a safe run identifier. It merges the transcript text cleanly.
    - `Execute Sub-workflow: LOOM — 01`: Scores candidate.
    - `Return Scored Candidate`: Sets flattened output.

### Workflow 03: LOOM — 03 — Factory Job Orchestrator (Created)
- **ID:** `x1ZYzsLc6Jaow0Wm`
- **Nodes:**
  - `Manual Trigger` + `Source Input`: Defines baseline `source_url` (`https://www.youtube.com/watch?v=xtOKTsIYwH0`).
  - `Validate Source`: Checks HTTPS and rejects domains outside the explicit allowlist. Prevents Playlists/Mixes from passing the MVP gate by throwing `SOURCE_VALIDATION_FAILED` if `list=` is present.
  - `Create Run Context`: Generates `loom-run-<UUID>` and poll counter.
  - `Submit OpenShorts Job`: POST `/api/process` over Tailscale Funnel. **(Static correction: Automatic retries explicitly disabled on this POST request.)**
  - `Check Submission`: Verifies `job_id`.
  - `Wait 60 Seconds` + `Increment Poll Count`: Delays between polls.
  - `Poll Job Status`: GET `/api/status/{job_id}`. **(Static correction: Explicitly configured to exactly 3 retries (4 maxTries) with a 5000ms wait between tries and exponential backoff, approximating the 5, 15, 45 second schedule).**
  - `Route Status`: Switch logic (`queued`, `completed`, `failed`, `timeout`, `unknown`). It explicitly bounds the timeout to 720 polls (12 hours).
  - `Validate Completed Job`: Asserts array length and deduplicates the `result.clips` indices (implements MVP idempotency against array repopulation, ensuring `candidateKey` is exactly `p.job_id + ':' + index`).
  - `Split Completed Clips`: Iterates through the output array dynamically.
  - `Attach Job and Tenant Context`: Pairs each clip with the upstream `run_id`, `source_url`, and sets `clip_start`/`clip_end` for WF02.
  - `Execute Workflow 02`: Sub-calls `6RNa6TXeppCQ7bhh` in `each` mode. **(Static correction: "Continue On Fail" explicitly enabled so per-clip errors do not crash the batch.)**
  - `Aggregate Batch Results`: Sorts valid outputs from sub-workflow failure exceptions, mapping them into `scored_candidates` and `failed_candidates`.
  - `Factory Failure Handler`: Connects the fail route in `Switch Routes` to WF00 (`IYB1Pp8cTMrghaoK`), passing exactly `run_id`, `job_id`, `failure_code`, `http_status`, and `error_message`.

## Exact Next Action (bottom-of-file summary)
1. Segment 3D and 3E are fully staged and statically constructed.
2. Please verify the corrected preflight architecture above. Once approved, activate **Workflow 03 (x1ZYzsLc6Jaow0Wm)** and run it via the UI to trigger the first live end-to-end VOD ingestion. The baseline test will use `https://www.youtube.com/watch?v=xtOKTsIYwH0`. 
3. Review results or report back to the agent with the execution outcome to finalize the segment.
