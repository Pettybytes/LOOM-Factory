# Adapter Evaluation & Acquisition Architecture — First Output

> Status: **evaluation only**. No media downloaded, no service installed, no
> cookies extracted. This document is the first output for Bryan's approval
> before any installation or real download.

---

## 1. Proposed adapter architecture (ladder)

```
LOOM Acquisition Boundary (authorization + idempotent job + ceilings)
  └─ Adapter Registry (platform -> ordered candidate adapters)
       ├─ Tier 1  yt-dlp        (YouTube, Reddit, Twitch clips/VOD, Kick clips/VOD,
       │                          TikTok, Instagram-where-supported)
       ├─ Tier 2  Streamlink     (Twitch live, Kick live, TikTok live only)
       ├─ Tier 3  Cobalt sidecar (Instagram, TikTok, Reddit, Twitch clips, YouTube)
       ├─ Tier 4  gallery-dl     (optional Instagram/Reddit fallback)
       └─ Tier 5  Chrome ext + local helper (browser-assisted capture)
```

Every adapter sits behind LOOM's own allowlist, authorization, byte/duration/
item-count/output-path/timeout controls. No adapter downloads anything without
an explicit acquisition job. Provider-returned filenames/URLs are never trusted
without validation. External providers (Apify) are out of scope until the local
ladder is exhausted and separately approved.

---

## 2. Platform test matrix (honest — presence in a list ≠ works)

Legend: ✅ = yes/anonymous, ⚠️ = partial/hit-or-miss, ❌ = no/blocked, `—` = n/a.

| Dimension | YouTube | Reddit | Twitch | Kick | TikTok | Instagram |
|---|---|---|---|---|---|---|
| URL type | video/short (VOD); live | post w/ hosted media | clip, VOD, live | clip, VOD, live | video; live | reel/post; story |
| candidate adapter | yt-dlp (+cobalt) | yt-dlp (+cobalt, gallery-dl) | yt-dlp (clip/VOD) + streamlink (live) | yt-dlp + streamlink | yt-dlp + cobalt (+streamlink live) | cobalt + gallery-dl + browser-helper |
| installed version | yt-dlp 2026.03.13 | yt-dlp 2026.03.13 | yt-dlp 2026.03.13 (streamlink ❌) | yt-dlp (streamlink ❌) | yt-dlp 2026.03.13 (cobalt ❌) | yt-dlp 2026.03.13 (cobalt/gallery-dl ❌) |
| anonymous metadata | ✅ (proven) | ✅ (RSS proven) | ✅ | ✅ | ⚠️ anti-bot | ❌ (yt-dlp "login required") |
| anonymous media | ✅ (not yet proven in LOOM) | ⚠️ host-diverse | ✅ clips / ⚠️ VOD | ✅ | ⚠️ often needs cookies | ❌ (yt-dlp) |
| browser assistance | ❌ | ❌ | ❌ | ❌ | ⚠️ may need | ⚠️ likely |
| credentials/cookies | ❌ | ❌ | ❌ (sub-only VOD ⚠️) | ❌ | ⚠️ often | ⚠️ likely (or browser-assisted) |
| full vs bounded | full + bounded | full | clip full / VOD bounded | clip full / VOD bounded | full | full (reel) |
| output | mp4/webm, audio | mp4/webm, audio, img | mp4 | mp4 | mp4 | mp4 |
| direct vs expiring | direct (signed, short-lived) | mostly direct | direct (signed) | direct | expiring | expiring / blob-MSE |
| byte/duration enforcement | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (post-check + ceilings) |
| known breakage | consent page (extractor exists) | external host variability | sub-only VOD | none known | tiktok:effect/sound/tag broken | instagram:user/tag broken; anonymous Reel blocked |
| license | yt-dlp Unlicense | Unlicense | Unlicense / streamlink BSD-2 | Unlicense | Unlicense / cobalt AGPL | cobalt AGPL / gallery-dl GPL-2 |
| **runtime proof** | **not_proven** | **not_proven** | **not_proven** | **not_proven** | **not_proven** | **blocked_pending_adapter** |

Only a real bounded proof may promote any platform to `runtime_proven`.

---

## 3. Cobalt license & deployment assessment

- **License:** AGPL-3.0 (repo remainder; `api/` + `web/` have separate READMEs).
- **AGPL implication:** copyleft triggers on *distribution/linking*. The compliant
  pattern (mandated): run Cobalt as a **separate contained service** (own process /
  container), call it over **localhost HTTP** only, and **do not copy Cobalt source
  into LOOM**. Network boundary ≠ linking, so LOOM's own code stays clean. This is
  the only permitted integration.
- **Deployment:** Docker Compose (image from GitHub) — Docker 28.4.0 present — or
  Node ≥18 + pnpm (Node 22 present). **Pin an exact image/version**; disable
  watchtower auto-update; scan/document dependencies.
- **Binding/security:** bind `127.0.0.1` only; require an API key (`API_KEYS`);
  never expose an unauthenticated public downloader.
- **Behavior:** "works like a fancy proxy", never caches, "can only download free
  & publicly accessible content" (aligns with our public-only boundary).
- **Auth caveat:** auth-required content needs `cookies.json` — we will **not**
  provide cookies, so Instagram/TikTok support is limited to what Cobalt resolves
  anonymously (must be proven, not assumed).

---

## 4. Chrome extension + local helper design (browser-assisted)

- **Primary:** capture the active page URL and submit explicit
  Acquire-for-Reaction authorization (no media fetch).
- **Secondary:** user-initiated resolution when the exact media is visibly
  playable but anonymous adapters fail.
- **Permissions:** `activeTab` + smallest host permissions only; no `<all_urls>`;
  no background passive interception; no history/passwords/messages/tabs access.
- **Never** export cookies or session databases.
- **Direct media capture:** if the active page exposes a normal direct media URL,
  validate host, scheme, expiry, and content-type before handing to LOOM. If the
  page uses blob/MSE/DRM and safe capture is impossible, **fail honestly** (no DRM
  bypass).
- **Local operations:** a localhost authenticated helper (or Chrome Native
  Messaging) performs any file/ffmpeg work — privileged secrets never live in
  extension JS.
- **No direct Supabase/GHL access** from the extension.

---

## 5. Exact smallest Instagram proof plan (target: the Reel)

Evaluation order (per directive), then stop for approval before any install/download:

1. **yt-dlp** — already recorded: ❌ anonymous ("login required").
2. **Cobalt sidecar** — self-host pinned container (127.0.0.1, API-key protected);
   test whether it resolves the Reel anonymously. If yes → bounded download under
   LOOM ceilings (≤5 min, ≤100 MiB, single item, confined temp dir, cleanup on
   failure). If no (needs cookies) → record and stop.
3. **gallery-dl** — `brew install gallery-dl`; run `--get-urls` (direct-URL
   extraction only, no download) to test anonymous feasibility. Same gate.
4. **Chrome extension + local helper** — build minimal extension; user opens the
   Reel in a normal tab; capture active URL + any safe direct media URL; validate;
   bounded ffmpeg download. Honest failure on blob/MSE/DRM.

Never: Apify, cookie extraction, unreviewed downloader, unrestricted fallback.

---

## 6. Cost

**$0.** All candidates are local/open-source (yt-dlp, streamlink, Cobalt
self-host, gallery-dl, ffmpeg, Chrome extension). Docker + ffmpeg + Node already
present. No paid provider.

---

## 7. Required installation / security decisions (need approval)

1. Apply migrations **031** + **032** (Supabase SQL editor or DB password) —
   provider-neutral contracts, no dependency on Instagram.
2. Install **gallery-dl** (`brew install gallery-dl`).
3. Install **streamlink** (`brew install streamlink`) — future live/stream work;
   not needed for the Instagram proof.
4. Run **Cobalt sidecar** (pinned Docker image, 127.0.0.1, API-key protected).
5. Build + install the **private Chrome extension** (unpacked, activeTab + minimal
   host perms) + local helper.

None of the above has been performed yet.
