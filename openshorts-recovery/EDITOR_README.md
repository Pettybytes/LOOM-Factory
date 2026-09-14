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
  - `dashboard/src/components/ManualTrimEditor.jsx` — the manual editor
  - `dashboard/src/lib/discordActivity.js` — Discord Activity SDK (preserved, not critical path)
  - `dashboard/src/lib/api.js` — editor session token + auth diagnostics
  - `dashboard/src/lib/captionRealign.js` (+ test) — untracked in the nested repo; preserved here as **reference files** `captionRealign.js` / `captionRealign.test.mjs`

## Editor critical path (current)

- Discord "Edit" → adapter ACKs deferred (type 5) → ephemeral **Open Editor** link button to `{LOOM_EDITOR_PUBLIC_URL}/edit?token=<single-use>`.
- `/edit` reads **token only** (no `job_id`/`clip_index` in the URL; those resolve server-side at exchange), strips it via `history.replaceState`, POSTs to `/discord/editor/exchange`, and mints a 4-hour session token for authenticated `/api/*` calls.

## Editor boundary (complete)

preview · trim · title · hook · separate social caption + spoken subtitles ·
crop/reframe (auto/full/track) · save→render→register→card progress · secure
download · full-field persistent draft · recoverable errors · idempotent retry ·
exactly-one revised re-review card · "Return to Discord Review" link.

## Security (launch token)

- Consumed immediately (single-use); only SHA-256 hash stored (`loom_edit_sessions`).
- Raw token removed from the URL (`history.replaceState`) and never sent to
  analytics/logs/errors.
- `Referrer-Policy: no-referrer` set globally so the referring URL (which briefly
  carries the token) never leaks via Referer.

## Reapplication instructions

1. In the nested `openshorts/` checkout (branch `loom/factory-mvp-baseline-fixes`):
   `git apply 0002-discord-editor.patch`
2. Copy `captionRealign.js` → `dashboard/src/lib/captionRealign.js` and
   `captionRealign.test.mjs` → `dashboard/src/lib/captionRealign.test.mjs`
   (they are untracked in the nested repo, so the patch does not carry them).
3. Rebuild/verify: `docker exec openshorts-frontend sh -c 'cd /app && npx vite build --logLevel error'`
