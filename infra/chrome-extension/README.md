# LOOM Source Capture — browser-assisted feasibility probe (MV3)

Purpose: determine whether a playing Instagram Reel exposes a **direct** media
URL, an HLS/DASH **manifest**, or **segmented** media — vs a `blob:`/MSE source
that cannot be fetched outside the browser. Last rung of the Instagram
acquisition ladder (yt-dlp ❌ login, Cobalt ⚠️ JPG-cover, gallery-dl ❌ login).

## Two stages (both user-initiated, no download)

1. **Capture page media** — reads the active tab's `<video>` element, `buffered`
   ranges, `<source>` elements, and `performance` resource entries. `activeTab`
   + `scripting` only.
2. **Inspect Playing Media** / **Inspect + reload Reel** — a bounded (12s)
   `chrome.debugger` Network probe. The reload variant attaches *first*, clears
   the probe's in-memory candidates, reloads the active Reel, and observes the
   fresh load (fixes "Reel was already buffered → no new requests").

## ⚠️ `debugger` permission warning

Stage 2 needs the `debugger` permission; Chrome shows a warning that the
extension can read/change data on the sites you visit. This extension scopes it:
attach **only** on explicit click, **only** the active Instagram tab, **12s**
then auto-detach, **metadata only** (never bodies/cookies/auth/passwords), and
detach on timeout/navigation/close/cancel.

## Explicit phases (never a raw `null`)

`Ready → Attaching → Inspection active (replay/reload now) → Collecting →
Complete | No new media traffic observed | Failed (reason)`, with a live 12s
countdown and attached/detached status. State is persisted in
`chrome.storage.session`, so closing/reopening the popup shows the result, not
`null`.

## Rejection diagnostics

The result reports redacted counts by reason (`hostname_rejected`,
`scheme_rejected`, `resource_type_rejected`, `content_type_rejected`,
`image_rejected`, `analytics_ad_rejected`, `accepted`). Signed CDN URLs are
never persisted or displayed in full.

## Install (unpacked)
1. Chrome → `chrome://extensions` → **Developer mode** → **Load unpacked** →
   select this directory. Review + allow the `debugger` warning.

## Test
1. Open a confirmed video Reel, let it play.
2. **Inspect + reload Reel** (recommended — forces a fresh load), or
   **Inspect Playing Media** then replay the Reel when prompted.
3. Wait for the countdown to finish and paste the JSON.
