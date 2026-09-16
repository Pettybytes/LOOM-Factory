# LOOM — HANDOFF

> **Primary execution authority:** `CURRENT_STATE.md`. If this file and
> `CURRENT_STATE.md` ever disagree, `CURRENT_STATE.md` wins.
>
> Never put API keys, tokens, passwords, cookies, secrets, complete auth
> headers, or private customer data in this file.

---

## Last Updated
2026-09-15 23:20 EDT (America/Detroit)

## Stage 2 — Discord-centered OpenShorts Editor: CLOSED (runtime acceptance passed)

The editor build is complete and accepted. The final blocker — Auto Edit
silently no-op'ing because the OpenShorts job workspace had been evicted — is
resolved by a **job-rehydration adapter** that reconstructs a live OpenShorts
workspace from LOOM's durable clean source, then drives the **native** OpenShorts
endpoints. A real, unmocked end-to-end run passed.

**Commit:** LOOM-Factory `master` `4e97070` (pushed). **Card message id:**
`1549614172055281724` (final_publication, `render-1.1`).

## Locked architecture

```text
durable clean source (loom_media.clean_source_url, filesafe CDN)
  → session-bound temporary OpenShorts job (output/loom-<session>/, reconstructed)
  → native OpenShorts endpoints (/api/clip/rerender, /api/hook, /api/subtitle, …)
  → immutable LOOM version (register_edited_asset RPC)
  → exactly one Discord final_publication review card
```

## Real E2E proof (unmocked)

Fresh clip `loom-e2e-proof-job:0` with its original job deleted, driven through
the actual adapter HTTP endpoints: rehydrate ("Job recovered from disk") →
`/api/clip/rerender` (trim 1.0→5.0) → `/api/hook` → `/api/subtitle` (karaoke) →
`/discord/editor/complete` (one `render-1.1` + one card) → replay idempotent (no
duplicate) → edited media served (`/videos/` HTTP 206). Script:
`infra/supabase/prove_e2e_rehydrate.py`.

## Temporary workspace lifecycle

`output/loom-<edit_session_id[:12]>/` per session. **Retain** while the session
is active or retryable; **delete** after successful completion plus a safe grace
period; **clean** abandoned/expired workspaces with bounded retention (OpenShorts
`OUTPUT_MAX_GB` is the outer bound today; a dedicated sweep is deferred); **never
delete** another tenant/session workspace (temp id is derived from the session id,
so isolation is structural). Re-hydration is idempotent (reuses an existing
workspace).

## Repository state

- **LOOM-Factory** `master` · HEAD `4e97070` · clean · pushed to origin.
  Editor code in `infra/discord-review/adapter.py` + `infra/supabase/`.
- **OpenShorts (nested)** `loom/factory-mvp-baseline-fixes` · HEAD `7f47de5` ·
  pre-existing dirty/untracked (do NOT touch): `dashboard/index.html`,
  `docker-compose.yml`, `security_utils.py` (modified); `aborted-runs/`,
  `security_utils.py.bak-20260908212436` (untracked).
- **Recovery artifacts** (`openshorts-recovery/`) preserved:
  `0002-discord-editor.patch` md5 `b0867c9ec0e20d6550929ee8bce7a4b7`;
  `0001-fix-caption-contract.patch` md5 `c631f000b6b5e904dbffab942d9fba9e`.

## Deferred / not in scope

- **Discord Activity (iframe) embedding** — deferred. External HTTPS `/edit` is
  the Stage 2 critical path.
- Temporary workspace sweep (explicit scheduled cleanup) — policy documented,
  enforcement deferred.
- Missing preview font `fonts/NotoSerif-Bold.ttf` — cosmetic, deferred.
- No further editor polish/features.

## Credentials (names only — values in secure source locations)

- `~/.openclaw/.env`: `LOOM_DISCORD_APP_ID`, `LOOM_DISCORD_PUBLIC_KEY`,
  `LOOM_DISCORD_BOT_TOKEN`, `LOOM_DISCORD_CLIENT_SECRET`,
  `LOOM_DISCORD_OAUTH_REDIRECT_URI`, `LOOM_EDITOR_PUBLIC_URL`, `LOOM_N8N_API_KEY`,
  `LOOM_SUPABASE_URL`, `LOOM_SUPABASE_SERVICE_ROLE_KEY`, `LOOM_GHL_TOKEN`,
  `LOOM_GHL_LOCATION_ID`.
- `infra/tunnel-proxy/.env`: `LOOM_TUNNEL_TOKEN`, `LOOM_RELAY_TOKEN`,
  `LOOM_GEMINI_KEY`.

## Next segment

**Automated Source Discovery V1.** Editor work is closed.
