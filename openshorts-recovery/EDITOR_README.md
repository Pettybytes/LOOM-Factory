# OpenShorts Discord Editor — Recovery Artifact

**Purpose:** Preserve the LOOM Discord-centered editor changes durably inside the
private `Pettybytes/LOOM-Factory` repository, since the upstream
`mutonby/openshorts` remote rejected pushes with 403 (no Bryan-owned fork yet).

**This is NOT a replacement for a proper Bryan-owned OpenShorts remote.** It only
makes the editor recoverable now.

## Identity

- **Upstream repository:** `https://github.com/mutonby/openshorts.git`
- **Local branch:** `loom/factory-mvp-baseline-fixes`
- **Patch:** `0002-discord-editor.patch`
- **Affected files (dashboard only):**
  - `dashboard/index.html` — `Referrer-Policy: no-referrer` meta (launch-token defense)
  - `dashboard/src/main.jsx` — `/edit` path route → `ManualTrimEditor`
  - `dashboard/src/components/ManualTrimEditor.jsx` — NEW manual trim editor

## What the editor does

- Reads `job_id`, `clip_index`, `token` from the `/edit` deep-link query string.
- **One-time token exchange:** POSTs the launch token to the LOOM adapter's
  `/discord/editor/exchange`; the adapter consumes the single-use token (hash-only)
  and returns the editor session identity. The raw token never leaves the exchange.
- Strips the token from the URL via `history.replaceState` on mount.
- Loads the editable recipe (`/api/clip/{job}/{idx}/edl`), transcript, scenes, and
  preview, then exposes trim (start/end), transcript/scene seeking, an optional
  written instruction, and a rerender button (`POST /api/clip/rerender`, FAST path).

## Security (launch token)

- Consumed immediately (single-use); only SHA-256 hash stored (`loom_edit_sessions`).
- Raw token removed from the URL (`history.replaceState`) and never sent to
  analytics/logs/errors.
- `Referrer-Policy: no-referrer` set globally so the referring URL (which briefly
  carries the token) never leaks via Referer.

## Reapplication instructions

From the OpenShorts clone at `/Users/user/Desktop/LOOM-Factory/openshorts`
(branch `loom/factory-mvp-baseline-fixes`):

```bash
cd /Users/user/Desktop/LOOM-Factory/openshorts
git apply /Users/user/Desktop/LOOM-Factory/openshorts-recovery/0002-discord-editor.patch
```

Then rebuild the dashboard (Vite) and restart as needed.

## Exclusions (do NOT include)

- OpenShorts `/cloud`, generated media (`output/`), secrets/`.env`.
- `docker-compose.yml` and `security_utils.py` — pre-existing uncommitted changes
  from prior LOOM sessions, NOT part of the editor.
- No backport to the adapter — the adapter lives in `infra/discord-review/` (this repo).

## Remaining editor work (not in this patch)

- **Version registration** (Step 4): new `asset_version` + correction log +
  `final_review_pending` — lives in LOOM (Supabase/n8n), not OpenShorts.
- **`final_publication` re-review card** (Step 5) — LOOM adapter/n8n.
- **Discord Activity embed** (Step 6): scoped `frame-ancestors`, Embedded App SDK.
- **Instruction-driven LLM edit**: the current `/api/edit` is autonomous Gemini;
  wiring a written instruction through it is a follow-up (the editor captures the
  instruction for the correction log in the meantime).
