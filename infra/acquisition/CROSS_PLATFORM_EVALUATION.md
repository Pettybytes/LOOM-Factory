# Cross-Platform Acquisition Evaluation (read-only)

> 2026-09-17. **$0 spent, no Actor calls, no downloads, nothing built.** Exact-URL
> acquisition only. This documents the adapter ladder per platform; nothing is
> wired, and the Discord/extension UI is NOT connected to any provider yet.

## Summary verdict

| Platform · content | Local primary | Apify fallback/primary | Verdict |
|---|---|---|---|
| YouTube · video | **yt-dlp** (free) | `apify/youtube-scraper` (rarely needed) | **local primary** |
| Reddit · post media | **yt-dlp** (v.redd.it) | community Reddit scraper | **local primary** (host-diverse caveat) |
| TikTok · video | yt-dlp (unreliable) | **Apify** (single-URL downloader) | **Apify primary** |
| X/Twitter · tweet media | yt-dlp (needs auth) | **Apify** (media downloader) | **Apify primary** (hardest) |
| Twitch · clip | **yt-dlp** (free) | — | **local primary** |
| Twitch · VOD (bounded range) | **yt-dlp + ffmpeg** | — | **local primary** |
| Kick · clip | **yt-dlp** (free) | — | **local primary** |
| Kick · VOD (bounded range) | **yt-dlp + ffmpeg** | — | **local primary** |
| Instagram · Reel | chrome-extension (blocked) | **apify/instagram-reel-scraper** | **Apify primary (runtime-proven)** |

**Big picture:** 6 of 8 content types are **free/local (yt-dlp) primary**.
Only **TikTok** and **X/Twitter** require a paid provider as primary (yt-dlp is
unreliable against their anti-bot). Instagram is already provider-proven.

---

## Comparison table

Legend: `yt-dlp` = installed (2026.03.13) · `ffmpeg` = installed (8.0.1) ·
`streamlink`/`gallery-dl` = NOT installed. Prices are per-result, snapshots as of
2026-09-17, **verify before dispatch** (the Instagram work proved slug ≠ API ID).

| Platform · type | Best free/local | Local readiness | Rec. Apify Actor (slug) | Maintainer / adoption | Exact-URL | Output media | Source audio | Price model | Est. 1 / 100 / 1,000 | Extra charges | Known limits | Adapter order | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| YouTube · video | yt-dlp | installed; transcript proven; media standard | `apify/youtube-scraper` | Apify (1st-party) | ✅ video URL | `videoUrl` (mp4) | ✅ muxed | PPE ~$1.5/1k | $0 / $0 / $0 (local) | none (local) | age/region; DRM none | yt-dlp → apify | **local primary** |
| Reddit · post media | yt-dlp | installed; v.redd.it works | `harshmaur/reddit-scraper` | community (established) | ⚠️ post URL → media link | `media` URL (host-diverse) | ⚠️ varies | PPE ~$2/1k | $0 local / $0.002 · $0.20 · $2 | none | media often external (YT/etc.) | yt-dlp → apify | **local primary** |
| TikTok · video | yt-dlp | installed; anonymous hit-or-miss (anti-bot) | `memo23/tiktok-video-downloader` | community (new) | ✅ post URL/ID | stable hosted mp4 | ✅ | PPE (verify) | ~$0.01 · ~$1 · ~$10 | none stated | links die 48h; watermark variants | apify → yt-dlp | **Apify primary** |
| X/Twitter · tweet media | yt-dlp | installed; often fails (auth/anti-bot) | `scrapeai/twitter-media-downloader` | community | ✅ tweet URL | direct media (video/gif/img) | ⚠️ often video-only | PPE $0.5/1k | $0.0005 · $0.05 · $0.50 | none stated | video may need login; rate limits | apify → yt-dlp | **Apify primary** (hardest) |
| Twitch · clip | yt-dlp | installed; twitch:clips works | — (none needed) | — | ✅ clip URL | mp4 | ✅ muxed | free | $0 | none | public clips only | yt-dlp | **local primary** |
| Twitch · VOD (bounded) | yt-dlp + ffmpeg | installed; vod works | `agentx/twitch-vod-downloader` | community | ✅ VOD + timestamp | mp4 | ✅ | free local | $0 local | bandwidth (huge VOD) | range via `--download-sections` | yt-dlp | **local primary** |
| Kick · clip | yt-dlp | installed; kick:clips works | `agentx/kick-clip-downloader` | community | ✅ clip URL | mp4 | ✅ | free | $0 | none | public clips only | yt-dlp | **local primary** |
| Kick · VOD (bounded) | yt-dlp + ffmpeg | installed; kick:vod works | `getascraper/kick-video-downloader` | community | ✅ VOD + timestamp | mp4 | ✅ | free | $0 local | bandwidth | range via trim | yt-dlp | **local primary** |

---

## Bounded VOD ranges (Twitch & Kick)

LOOM can retrieve **only the approved timestamp range** instead of an entire
stream:

- **yt-dlp `--download-sections "*start-end"`** — for HLS/DASH VODs, downloads
  only the segments covering the range (via ffmpeg), not the whole stream. This
  is the preferred path and keeps bytes bounded.
- **Fallback:** full VOD download → ffmpeg trim → delete the full copy. Acceptable
  only when `--download-sections` is unsupported by the format.

Both stay within the existing ≤100 MiB / ≤5 min local ceiling logic (a bounded
range of a long VOD is a small clip; a full stream must be rejected upstream).

---

## Next implementation order (documented, NOT built)

1. **Wire Instagram provider into Acquire-for-Reaction** (Discord + extension) —
   finish the already-proven path. (Explicitly deferred by Bryan today.)
2. **YouTube local (yt-dlp)** — free, highest-volume reaction source, easiest;
   run a bounded media proof (like the Instagram one) to mark `runtime_proven`.
3. **Twitch + Kick clips (yt-dlp)** — free, near-identical to YouTube path.
4. **TikTok provider (Apify)** — paid; resolve numeric API ID + input schema
   (the Instagram lesson: slug ≠ ID), then a bounded proof.
5. **Reddit local (yt-dlp v.redd.it)** — free; add host-diverse fallback policy.
6. **X/Twitter provider (Apify)** — paid, hardest (anti-bot/auth); last provider.
7. **Bounded VOD ranges (Twitch/Kick)** — last (bandwidth-heavy, niche).

Each paid path reuses the existing provider boundary (`provider.py` allowlist +
`cost.py` circuit breaker + `orchestrate.py`), with a per-actor numeric-ID +
input/output schema verification **before any dispatch**.

## Notes / honesty

- Numeric API IDs: only Instagram is pinned (`xMc5Ga1oCONPmWJIa`). Every other
  actor is listed by slug; the numeric ID must be resolved via the Apify API at
  implementation time (the website slug is NOT the API ID).
- Adoptions/prices are search snapshots and drift; re-verify before dispatch.
- No actor was called, no money spent, nothing downloaded during this evaluation.
