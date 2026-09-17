# LOOM Source Capture — browser-assisted feasibility extension (MV3)

Purpose: evaluate whether Instagram exposes a **direct** media URL for a
playing Reel, or uses a `blob:`/MSE source (which cannot be fetched outside the
browser). This is the last rung of the approved Instagram acquisition ladder
(after yt-dlp ❌ login, Cobalt ⚠️ JPG-cover misclassification, gallery-dl ❌ login).

## Security
- `activeTab` + `scripting` + `host_permissions: https://*.instagram.com/*` only.
- No `<all_urls>`, no background service worker, no passive interception, no
  cookie/history/password/messages access.
- Runs only when the user clicks "Capture active media".

## Install (unpacked)
1. Chrome → `chrome://extensions` → enable **Developer mode**.
2. **Load unpacked** → select this directory (`infra/chrome-extension/`).

## Feasibility test (no download)
1. Open a confirmed video Reel in a normal Chrome tab and let it play.
2. Click the LOOM icon → **Capture active media**.
3. Read the JSON:
   - `isDirectUrl: true` → a direct `https://…` media URL was captured →
     browser-assisted acquisition is feasible.
   - `isBlobOrMse: true` (or `videoSrc` is `blob:`/empty) → Instagram uses MSE;
     no direct URL is exposed → record as **not feasible** (honest DRM/MSE failure).

Nothing here downloads or uploads. It only reports what the active tab exposes.
