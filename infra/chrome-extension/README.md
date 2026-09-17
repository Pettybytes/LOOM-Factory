# LOOM Source Capture — browser-assisted feasibility probe (MV3)

Purpose: determine whether a playing Instagram Reel exposes a **direct** media
URL, an HLS/DASH **manifest**, or **segmented** media — vs a `blob:`/MSE source
that cannot be fetched outside the browser. This is the last rung of the
approved Instagram acquisition ladder (yt-dlp ❌ login, Cobalt ⚠️ JPG-cover
misclassification, gallery-dl ❌ login).

## Two stages (both user-initiated, no download)

1. **Capture page media** — reads the active tab's `<video>` element,
   `buffered` ranges, `<source>` elements, and `performance` resource entries
   (media-like URLs). Uses only `activeTab` + `scripting`.
2. **Inspect Playing Media** — a bounded (12s) `chrome.debugger` Network probe
   (CDP) that captures media request *metadata only* while the Reel plays.

## ⚠️ `debugger` permission warning

Enabling stage 2 requires the `debugger` permission. Chrome will show a warning
that the extension can read/change data on the sites you visit while the
debugger is attached. This is **scoped** by this extension to:
- attach **only** when you click "Inspect Playing Media",
- **only** the current active Instagram tab,
- a **bounded 12-second window**, then automatic detach,
- **metadata only** — never request/response bodies, cookies, auth headers,
  passwords, messages, or unrelated browsing data.

It never attaches in the background, never inspects other tabs, and detaches on
timeout, navigation, tab close, or cancel.

## Install (unpacked)
1. Chrome → `chrome://extensions` → **Developer mode** → **Load unpacked** →
   select this directory (`infra/chrome-extension/`).
2. Review the `debugger` warning, then **allow**.

## Test
1. Open a confirmed video Reel in a normal Chrome tab and let it play.
2. Click the LOOM icon → **Capture page media** (stage 1).
3. If it reports blob/MSE, click **Inspect Playing Media** (stage 2), keep the
   Reel playing for ~12s, and read the JSON (query strings are redacted).

## Interpreting the result
- `direct_video` → a direct MP4 URL exists → browser-assisted acquisition feasible.
- `manifest` (`.m3u8`/`.mpd`) or `segment` (+ `audio`) → segmented/DASH/HLS →
  feasible, but `ffmpeg` muxing likely required.
- no candidates → record as not feasible (honest MSE/DRM failure).

Nothing here downloads or uploads media. It only reports redacted request
metadata. Signed CDN URLs are never persisted.
