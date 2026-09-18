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

---

## TikTok + X/Twitter provider deep-dive (correction, read-only 2026-09-17)

Verified against the live Apify API (numeric IDs + adoption) and actor READMEs.

### TikTok — ranked candidates

| Role | Actor | API ID | Maintainer | Adoption | Success / rating | Price | Notes |
|---|---|---|---|---|---|---|---|
| **Cheapest compliant** | `memo23/tiktok-video-downloader` | `pQGdDfhFC35QToTxZ` | community | 24 users | 99.5% / 5.0★ | $2.00/1k posts | single-URL `posts[]`, no profile/feed/search, no cookies, re-hosts stable no-watermark MP4, `maxFileSizeMb` cap |
| **Most reliable** | `clockworks/tiktok-scraper` | `GdWCkxBtKWOsKjdch` | Clockworks | 286,773 users | 98.2% / 4.76★ | $1.70/1k | supports "video URLs" but bulk-first (profiles/hashtags/search/`scrapeRelatedVideos`) |
| Established alt | `pocesar/download-tiktok-video` | `5kThqg0kUkp50prLA` | community | 558 | 2.77★ | — | post links → KV-store video |
| Official API | `api/tiktok-api` | (resolve) | Apify | — | — | $0.03 start + $0.004 item + $0.001 video | **$0.03 start fee → ~$0.035 single-item, ABOVE $0.01** |

**Verdict:** recommended primary = `memo23` (contract-compliant; verify output + pricing before dispatch);
inactive fallback = `clockworks` (reliable but bulk-first — must hard-code single video-URL + disable
`scrapeRelatedVideos`/profiles/hashtags/search); emergency = yt-dlp (hit-or-miss).

### X/Twitter — ranked candidates

| Role | Actor | API ID | Maintainer | Adoption | Price | Notes |
|---|---|---|---|---|---|---|
| Cheapest claimed | `scrapeai/twitter-media-downloader` | `hSJvwRUtIBWMDm2KW` | community | **2 users** | $2.49/1k **+ usage** | non-deterministic (usage charge), no rating, media output unproven |
| Cheapest claimed | `feedminer/x-media-scraper` | `CrPZmLxFE32UibRPc` | community | **2 users** | $0.40/1k | no adoption |
| Official | `apify/twitter-scraper` | (unresolved) | Apify | large | $0.18–0.40/1k | metadata-first, not a media downloader |

**Verdict:** NO candidate meets the exact-item + deterministic-cost + established bar.
X is the hardest platform (auth walls, anti-bot, video often login-gated). **Defer X** —
keep it `evaluated_not_proven` (high risk); emergency = yt-dlp (often fails) or manual browser save.

> ⚠️ **X is not even in the LOOM platform allowlist** (`PLATFORMS` = youtube/reddit/tiktok/
> instagram/twitch/kick). It is documented here for future consideration only — do NOT add it
> to the intake allowlist or capability matrix without a separate decision.

### Capability-language correction

"Extractor exists" / "expected to work" is NOT runtime-proven. Only **Instagram** is
runtime-proven (provider). Every other platform remains `implemented_not_proven`
(yt-dlp installed) or `evaluated_not_proven` (provider evaluated: TikTok, X) until a
bounded live media proof succeeds.

---

## Snapchat + Universal Media Downloaders (final read-only investigation)

### Universal / all-in-one — ranked candidates (live-API verified)

| Rank | Actor | API ID | Maintainer | Adoption | Success / rating | Price | Notes |
|---|---|---|---|---|---|---|---|
| **Most reliable** | `agentx/all-video-scraper` | `etzrjsBIFSOb19k5E` | AgentX | 271 users · 21 mo | 100% / 4.59★ | $0.01 start + $0.09 meta + $0.28 file | one `video_url` → 43 fields, no usage charge, metadata/file priced apart |
| **Cheapest advertised** | `easyapi/all-in-one-media-downloader` | `iZbsVYT4VfdMxoIPL` | EasyApi | 888 users · 69 mo | 100% / **2.46★ (API) vs 4.95★ (README)** | $2.99/1k **+ usage** | single `link` → `medias[]` of direct signed URLs; usage charge; rating discrepancy |
| Established alt | `agentx/all-video-scraper` family / `burbn/all-in-one-downloader` | — | community | — | — | — | marketing-heavy, unverified schemas |

### Primary candidate `easyapi/all-in-one-media-downloader` — findings

- **ID:** `iZbsVYT4VfdMxoIPL` · build `0.0.55` · community (EasyApi) · 888 users, 69 monthly.
- **Input:** single `link` (string) + `proxyConfiguration`. ✅ exact one-URL.
- **Output:** `medias[]` array (multiple quality variants: `no_watermark`, `hd_no_watermark`, `audio` mp3) — **NOT one result; a list**. LOOM must select one. Direct signed CDN URLs (48h expiry + exact-header requirement), not stored files.
- **Watermark removal:** present as separate `no_watermark` / `hd_no_watermark` variants (base result, no extra event).
- **Pricing:** $2.99/1k **pay-per-event AND usage** → non-deterministic (compute/proxy/storage). ⚠️.
- **Rating discrepancy:** API `actorReviewRating` = 2.46 vs README "4.95". Trust the API (2.46). ⚠️.
- **Platform support:** marketing lists Snapchat/Reddit/Spotify/etc.; the actual schema + sample only prove TikTok. Per-platform support is **unverified** for most listed names.

### Snapchat — dedicated candidates (public Spotlight only)

| Actor | API ID | Notes |
|---|---|---|
| `agentx/snapchat-video-downloader` | (resolve) | "one public Spotlight link → title/desc/thumbnail + optional stored video"; agentx family contract |
| `automation-lab/snapchat-public-video-downloader` | (resolve) | "high-quality videos + metadata from Snapchat URLs"; public Spotlight |

**Snapchat verdict:** public Spotlight/share URLs are the only compliant target (no private/friends/story/cookie capture). Dedicated Actors exist but are low-adoption; a bounded runtime proof is feasible but should be **last** in priority. Do NOT add Snapchat to the allowlist without a separate decision.

### Platform-support matrix (universal Actors)

| Platform | easyapi | agentx/all-video | Exact-URL | Audio | Expansion risk | Proof required |
|---|---|---|---|---|---|---|
| Instagram Reel | marketing (unverified) | explicit | ✅ | ✅ (muxed) | low | yes |
| TikTok | ✅ (proven sample) | explicit | ✅ | ✅ | low | yes |
| YouTube | marketing | explicit | ✅ | ✅ | low | yes |
| Reddit v.redd.it | marketing ("Others") | unclear ("1000+") | ⚠️ | varies | medium | yes |
| X/Twitter | marketing | explicit | ⚠️ | often video-only | high (auth) | yes |
| Snapchat Spotlight | marketing | not listed (dedicated actor) | ⚠️ | unverified | high | yes |
| Twitch clip | not listed | explicit | ✅ | ✅ | low | yes |
| Kick clip | not listed | not listed | ❌ | — | — | — |

**No inference across platforms:** one successful platform does not prove another.

### Economic comparison (all-in, with file)

| Path | 1 | 100 | 1,000 | 1,800 |
|---|---|---|---|---|
| **Platform-specific** (Instagram $0.0026 + TikTok ~$0.002 + local $0) | ~$0.003 | ~$0.26 | ~$2.60 | ~$4.68 |
| **agentx/all-video-scraper** ($0.38/file) | $0.38 | $38 | $380 | $684 |
| **easyapi/all-in-one** ($0.003/result + usage) | ~$0.003+usage | ~$0.30+usage | ~$3+usage | ~$5.40+usage |
| Free/local (yt-dlp) | $0 | $0 | $0 | $0 |

### Universal economics — separated (corrected)

- **`agentx/all-video-scraper`** — deterministic **$0.38 with file** ($0.01 start + $0.09 metadata + $0.28 download; no usage charge). That is **~146× the proven $0.0026 Instagram acquisition**. Deterministic but too expensive for routine LOOM use — suitable only as an inactive fallback.
- **`easyapi/all-in-one-media-downloader`** — headline base **~$0.00299/result**, superficially comparable to Instagram/TikTok. BUT it is pay-per-event **plus usage** (non-deterministic all-in price), with a rating discrepancy (API 2.46 vs README 4.95), `medias[]` multiplicity, a signed-header requirement, and unproven platform support. **Rejected for trust, determinism, and contract risk — NOT because its headline price is higher** (its base price is competitive; its risk profile is not).

### Snapchat economics (resolved, live-API)

| Item | `agentx/snapchat-video-downloader` | `automation-lab/snapchat-public-video-downloader` |
|---|---|---|
| Numeric ID | `q5qkPabhaMeWxWVGa` | `LIdfZyy13VzOzEUNx` |
| Pricing model | pay-per-event, **no usage** | pay-per-event **+ usage** |
| File-download price | $0.20 (Download event) | UNRESOLVED |
| Metadata price | $0.09 (Metadata event) | UNRESOLVED |
| Actor start | $0.01 | UNRESOLVED |
| All-in with file (1 item) | **$0.30** | ~$0.0011 + usage |
| Input field | `video_url` (string, one Spotlight URL) | URL array (up to 100) + `saveVideos` |
| Output media field | `video` (43-field row) + `audio_codec` | `mediaUrl` (direct CDN URL) |
| Source audio | ✅ `audio_codec` present | ⚠️ UNRESOLVED |
| One URL → multiple media? | No (one row) | No (one snap) |
| Adoption / success | 4 users · 0★ · 100% (31/0/4) | 2 users · 0★ · 75.7% (28/9/0) |
| Last update | 2026-08-26 (build 1.0.27) | 2026-08-20 (build 0.1.4) |
| Est. 1 / 100 / 1,000 / 1,800 | $0.30 / $30 / $300 / $540 | ~$0.001 / ~$0.11 / ~$1.12 / ~$2.02 (+usage) |

**Snapchat verdict:** recommended bounded-proof candidate = **`agentx/snapchat-video-downloader`**
(numeric ID verified, deterministic $0.30/file, single `video_url`, 43-field contract,
audio included, 100% success) — but only 4 users, so a bounded live proof is still
required before any runtime-proven claim. Conditional candidate = `automation-lab/…`
(non-deterministic usage, array input, 75.7% success, audio unresolved).

### Architecture decision

1. **One universal replace Instagram+TikTok? No.** The proven `apify/instagram-reel-scraper`
   ($0.0026) and `memo23/tiktok-video-downloader` ($0.002) are far cheaper and better-scoped.
2. **Universal for X + Snapchat? Conditional.** `agentx/all-video-scraper` claims X;
   dedicated Snapchat Actors exist. A universal *fallback* for these two is defensible,
   but each needs a bounded proof (X is auth-walled; Snapchat is unproven).
3. **Stay free/local:** YouTube, Twitch, Kick, Reddit (yt-dlp/ffmpeg).
4. **Does it reduce plumbing? Marginally** — one dispatch interface, but per-platform
   canonicalization/content-ID/host/media validation/cost/provenance remains.
5. **Platform-specific validation that must remain regardless:** URL canonicalization,
   content-ID match, host/CDN allowlist, ffprobe+SHA-256, source-audio check, cost
   accounting, provenance, failure classification.
6. **Single point of failure? Yes** if universal is primary — one vendor, one pricing
   change, one de-list breaks everything. Avoid by keeping platform-specific primaries.
7. **Recommendation: HYBRID.** Platform-specific primary (proven Instagram + memo23 TikTok
   + yt-dlp local) + universal `agentx/all-video-scraper` as **inactive fallback** for
   X/Snapchat/niche. Do NOT delete the proven Instagram adapter.

### Bounded runtime proofs still required (tomorrow, in order)

1. Instagram UI wiring (Discord/extension → proven provider) — deferred by Bryan.
2. YouTube local (yt-dlp) media proof.
3. Twitch + Kick clips (yt-dlp).
4. TikTok provider (memo23) proof.
5. Universal fallback (agentx/all-video-scraper) — only for X + Snapchat, last.
