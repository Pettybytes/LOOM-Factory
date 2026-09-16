# LOOM — Current State (handoff)

> **This file is the execution authority.** If `HANDOFF.md` disagrees, this file wins.

---

## STOP STATE — 2026-09-15 ~23:20 EDT

**Stage 2 — Discord-centered OpenShorts Editor: CLOSED. Runtime acceptance PASSED.**

The editor build is complete and accepted. The last blocker — the editor silently no-op'ing on Auto Edit because the OpenShorts job workspace had been evicted — is resolved by a **job-rehydration adapter** that reconstructs a live OpenShorts workspace from LOOM's durable clean source, then drives the *native* OpenShorts endpoints (no new render implementation). A real, unmocked end-to-end run passed.

**No `render-1.3` exists.** The accepted run produced exactly one immutable version (`render-1.1`) and exactly one review card.

---

## 🔒 Locked architecture (editor path)

```text
durable clean source (loom_media.clean_source_url, filesafe CDN)
  → session-bound temporary OpenShorts job (output/loom-<session>/, reconstructed)
  → native OpenShorts endpoints (/api/clip/rerender, /api/hook, /api/subtitle, …)
  → immutable LOOM version (register_edited_asset RPC, render-<major>.<minor+1>)
  → exactly one Discord final_publication review card
```

- The OpenShorts editor is **NOT stateless**: it needs a live `output/<job_id>/`
  workspace (metadata JSON + clip files) and an in-memory `jobs[id]` entry.
- LOOM's adapter (`infra/discord-review/adapter.py`) rehydrates that workspace on
  demand from the durable clean source, so editing survives OpenShorts job eviction.
- The client (Discord Activity / editor) never chooses the temp job id; it is
  derived server-side from the edit session and translated transparently in
  `_handle_editor_api`.

---

## ✅ Real E2E proof (unmocked, driven through the actual adapter HTTP endpoints)

Fresh non-production clip `loom-e2e-proof-job:0` with its original OpenShorts job
**deleted**, run end-to-end:

| Step | Result |
|---|---|
| Launch-token exchange → editor session | ✅ 200 |
| `/discord/editor/bootstrap` (durable) | ✅ clean_source_url present, duration 6.0 |
| `/api/status` → rehydration | ✅ "Job recovered from disk" |
| `/api/clip/{job}/0/edl` | ✅ real EDL (segments/canonical_range) |
| `/api/clip/rerender` (trim 1.0→5.0) | ✅ real recut |
| `/api/hook` | ✅ real hook burn |
| `/api/subtitle` (karaoke) | ✅ real caption burn |
| `/discord/editor/complete` | ✅ registered `render-1.1` + one card |
| Replay `complete` | ✅ idempotent — no duplicate card/version |
| Edited media download | ✅ `/videos/` HTTP 206 |

- **Card message id: `1549614172055281724`** (final_publication, `render-1.1`,
  title "E2E Edited Proof", hook "NEW HOOK PROOF").
- Proof script: `infra/supabase/prove_e2e_rehydrate.py` (reproducible).

---

## ♻️ Temporary workspace lifecycle

`rehydrate_edit_job()` creates `output/loom-<edit_session_id[:12]>/` per session.

- **Retain** while the edit session is `active` or retryable.
- **Delete** after successful completion plus a safe grace period.
- **Clean** abandoned/expired workspaces with bounded retention (the OpenShorts
  backend's `OUTPUT_MAX_GB` retention is the outer bound; a dedicated sweep is
  deferred — documented policy, not yet a scheduled task).
- **Never delete** another tenant's or another session's workspace — the temp id
  is derived from the session id, so workspaces are structurally isolated.
- Idempotent: re-hydration reuses an existing workspace (checks for the metadata
  JSON + clean file) rather than re-downloading.

---

## 1. Repository state

### LOOM-Factory — `master`
- **HEAD:** `4e97070` · clean · pushed to `origin/master`
  (`origin https://github.com/Pettybytes/LOOM-Factory.git`).
- Commit chain (this editor build): `029f873` → `d7e5697` → `4492068` →
  `042090e` → `4020f57` → `4e97070`.
- Editor work lives in `infra/discord-review/adapter.py` + `infra/supabase/`.

### OpenShorts (nested) — `loom/factory-mvp-baseline-fixes`
- **HEAD:** `7f47de5` (committed locally; upstream push historically 403 — no
  Bryan-owned fork).
- **Pre-existing dirty/untracked (do NOT clean/reset/overwrite):**
  - Modified: `dashboard/index.html`, `docker-compose.yml`, `security_utils.py`
  - Untracked: `aborted-runs/`, `security_utils.py.bak-20260908212436`

### Recovery artifacts (`openshorts-recovery/`) — preserved
- `0002-discord-editor.patch` — md5 `b0867c9ec0e20d6550929ee8bce7a4b7` (unchanged).
- `0001-fix-caption-contract.patch` — md5 `c631f000b6b5e904dbffab942d9fba9e`.
- Reference files (`captionRealign.js`, `captionRealign.test.mjs`,
  `EditorErrorBoundary.jsx`, `EDITOR_README.md`) unchanged.
- The editor's frontend files (ManualTrimEditor.jsx, HookModal.jsx, TitleOverlay.tsx,
  RemotionPreview.jsx, ShortVideo.tsx, types.ts) are captured in the patch.

### Automated test harness (NOT in either repo)
- `/Users/user/.openclaw/workspace/loom-editor-test/editor-browser-test.cjs` +
  `fixtures/tiny.mp4` — Playwright, mocked, **9/9 pass**.

---

## 2. Runtime + infrastructure (unchanged)

**Funnel hostname:** `https://users-macbook-pro-1.taild000b2.ts.net` → Caddy `:8081`

| Component | Process/container | Bind | Health |
|-----------|-------------------|------|--------|
| LOOM adapter | `adapter.py` (host, supervised) | `127.0.0.1:8090` | `/health` 200 |
| Caddy funnel | `loom-tunnel-proxy` | `127.0.0.1:8081–8082` | Up |
| OpenShorts backend | `openshorts-backend` | `127.0.0.1:8000` (loopback) | Up (root 404 normal) |
| OpenShorts renderer | `openshorts-renderer` | `0.0.0.0:3100` | Up |
| OpenShorts frontend (Vite) | `openshorts-frontend` | `0.0.0.0:5175→5173` | Up |

**Adapter supervision/logs:** `infra/discord-review/supervise.sh` (restart on exit)
→ `supervisor.log`; launcher `run.sh` (sources `~/.openclaw/.env`); stdout/stderr →
`adapter.log`.

**Caddy `:8081` routes:** `/edit /edit/*` (CSP frame-ancestors Discord) ·
`/discord/interactions*` → 8090 (Ed25519) · `/discord/editor/*` → 8090 ·
`/discord/card*` → 8090 (Bearer tunnel token) · `/api/*` → 8090 · default → Vite 5175.
**`:8082`:** `/relay*` → n8n callback relay.

**Stage 2 critical path:** external HTTPS `/edit` (the Discord deep-link / Activity
entry). **Discord Activity (iframe) embedding is DEFERRED** — the CSP
`frame-ancestors` scoping is in the Caddyfile but embedding is not a gating path.

---

## 3. Discord IDs (unchanged)

- **App:** "LOOM Clip Review" · App ID `1548188833386995742`
- **Guild:** "Clippy Factory" · Guild ID `1546266463663423518`
- **Review channel:** resolved at runtime by name `clip-review`.
- **Reviewer (Bryan):** `688976899094872148`

---

## 4. Migrations — 010–017 (all applied + live-verified)

| # | Change |
|---|--------|
| 010 | `tenant_distribution_profiles.publisher_user_id` |
| 011 | `loom_media.content_type` |
| 012 | `loom_edit_sessions` + `loom_edit_corrections` + lineage |
| 013 | `register_edited_asset` RPC (service_role only) |
| 014 | `loom_edit_sessions.session_token_hash` |
| 015 | editor launch context |
| 016 | `loom_edit_sessions.draft` (jsonb) |
| 017 | `loom_media.clean_source_url/hash/size/filename` (durable clean input) |

Test clip `loom-e2e-proof-job:0` now carries `render-1.0` (clean source + burned
canonical) and `render-1.1` (the accepted edited version). No `render-1.3`.

---

## 5. Credential inventory (names + locations only — never values)

**`~/.openclaw/.env`:** `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
`LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
`LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`, `LOOM_N8N_API_KEY`,
`LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY`, `LOOM_GHL_TOKEN`,
`LOOM_GHL_LOCATION_ID` (all present). `LOOM_EDITOR_CORS_ORIGINS`,
`LOOM_EDITOR_BASE_URL`, `LOOM_EDITOR_ORIGIN` (optional, MISSING — defaults exist).

**`infra/tunnel-proxy/.env`:** `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
`LOOM_GEMINI_KEY` (present).

> ⚠️ Never record or print secret values. Reference the files only.

---

## 6. Deferred / NOT in scope

- **Discord Activity (iframe) embedding** — deferred. External HTTPS `/edit` is
  the Stage 2 critical path.
- **Temporary workspace sweep** — lifecycle policy documented above; explicit
  scheduled cleanup deferred (bounded by OpenShorts retention today).
- **Missing preview font** `fonts/NotoSerif-Bold.ttf` — cosmetic (preview only;
  server burn uses its own fonts). Deferred.
- No further editor polish/features.

## 7. Next segment

**Automated Source Discovery V1.** Editor work is closed.
