# LOOM Source Capture — browser-assisted acquisition extension (MV3)

Browser-assisted Instagram media acquisition for LOOM Acquire-for-Reaction.
Resolution is **human-proven**: a playing Reel exposes one direct MP4 candidate
(`video/mp4`, Meta CDN), captured via a bounded `chrome.debugger` Network probe.

## Controls

- **Acquire for Reaction** (primary) — inspect the playing Reel, capture the
  first direct-MP4 signed URL (volatile memory only), POST it to the
  authenticated localhost LOOM helper, and show the acquisition phases.
- **Capture page media** — read the active tab's `<video>` element + resource
  timeline (`activeTab` + `scripting`).
- **Inspect Playing Media** / **Inspect + reload Reel** — feasibility-only
  bounded network probe.

## Phases

`Inspecting playing media → Media found → Sending to local LOOM → Downloading
within limits → Validating → Saved to Reaction Library | Failed (reason)`.

## ⚠️ `debugger` permission warning

The `debugger` permission makes Chrome warn that the extension can read/change
data on the sites you visit. This extension scopes it to: explicit click → active
Instagram tab only → bounded window → metadata only (never bodies/cookies/auth) →
auto-detach. It never attaches in the background.

## Config (gitignored)

`config.js` holds the helper URL + high-entropy token (mirrors
`infra/local-helper/.token`). `config.js.example` documents the shape. The
extension has `host_permissions` for `https://*.instagram.com/*` and
`http://127.0.0.1:8787/*`.

## Install / run

1. Start the helper: `infra/local-helper/run.sh`.
2. Chrome → `chrome://extensions` → Developer mode → Load unpacked →
   `infra/chrome-extension/` (accept the `debugger` warning).
3. Open a confirmed video Reel, let it play, click **Acquire for Reaction**.

The signed CDN URL is never persisted or displayed in full.
