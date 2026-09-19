# Reddit Video Acquisition — Provider / Method Selection

**Status: research complete, recommendation made. NOTHING IMPLEMENTED.**
No paid actor was called, no media was downloaded, no production data, ledger,
Discord card, or Reaction Library row was touched.

Scope: close the **provider/method selection** gap only. Reddit discovery,
normalization, dedup, qualification, Supabase storage and the human review
boundary are already runtime-proven and were not re-audited.

---

## 1. Bottom line

**Adopt the zero-cost local path (yt-dlp) as the primary Reddit acquisition
mechanism, with the existing bounded local lane.** It is the only option that
satisfies the LOOM-consumed contract *and* routes external-host posts, at $0, using
machinery the repo has already runtime-proven. Apify has exactly one plausible
exact-URL actor, and it is a **fallback only** (low adoption, and its media lands in
Apify storage rather than the platform CDN, which changes the download and expiry
semantics we already govern).

---

## 2. What was actually tested (read-only, metadata only)

| Step | Command / endpoint | Result |
|---|---|---|
| yt-dlp version | `yt-dlp --version` | `2026.08.19`, ships a `Reddit` extractor |
| Reddit public JSON | `GET /r/<sub>/hot.json`, `/comments/<id>.json` | **403** (HTML block page, 189 KB) |
| Reddit public API host | `GET api.reddit.com/r/<sub>/hot` | **403** |
| `old.reddit.com` JSON | `GET old.reddit.com/.../.json` | **302** redirect (no data) |
| Reddit RSS (first-party) | `GET /r/videos/.rss` with a contact UA | **200** (45,951 B) |
| Reddit RSS, rapid repeats | 3 subreddits back-to-back | **429** — rate-limits quickly |
| yt-dlp, native v.redd.it post | post `1wk5o10` | ✅ `Reddit` extractor, **20 formats** |
| yt-dlp, native v.redd.it post | post `1wjv4fh` | ✅ same shape |
| yt-dlp, external-host posts | two `r/videos` posts | ✅ resolved to the **`Youtube`** extractor |
| yt-dlp URL forms | `www.`, `old.`, `np.`, `redd.it/<id>` | ✅ all resolve identically |
| yt-dlp subreddit listing | `https://www.reddit.com/r/videos/` | ❌ `Unsupported URL` (post URLs only) |

---

## 3. Option A — zero-cost local acquisition

### 3.1 yt-dlp `Reddit` extractor — **viable, and it is the recommendation**

Native post `1wk5o10` (`r/oddlysatisfying`, titled "... (no music)") returned:

```
extractor: Reddit   id: tbdxx9kc3dqh1   display_id: 1wk5o10   duration: 11   720x1280
20 formats, every URL on host v.redd.it:
  dash-10, dash-11          vcodec: none          acodec: mp4a.40.2   ext: m4a   <- AUDIO ONLY
  dash-5..dash-9            vcodec: avc1.4d401e/f  acodec: none       ext: mp4   <- VIDEO ONLY
  hls-* (253..2235), fallback   vcodec: h264/avc1  acodec: none       ext: mp4   <- VIDEO ONLY
  hls-5-audio_0, hls-6-audio_0  v: none  a: None                       ext: mp4   <- ambiguous
```

Three conclusions that matter:

1. **Reddit genuinely serves video and audio as separate streams.** A download of the
   video stream alone plays silent — exactly the trap the task warns about.
   A **local mux is mandatory**, not optional.
2. The repo already owns that mux: `assemble.build_mux_args` + `fmp4`/`media_normalize`
   (the same path already proven for the Instagram Reels mux and reused in
   `tiktok_media`). No new media machinery is required.
3. **Audio presence must be probed, never inferred from metadata.** The post is
   literally titled "(no music)" and *still* exposes `mp4a.40.2` audio renditions.

Field availability (from the extractor output):

| LOOM-consumed field | yt-dlp | Note |
|---|---|---|
| Exact single-post URL input | ✅ | post URLs only — no listings, no search |
| Stable Reddit post ID | ✅ | `display_id` = `1wk5o10` (the `t3_` id) |
| Canonical post URL | ⚠️ derive | Build from `display_id` + subreddit from the input URL |
| Subreddit | ❌ `None` | Must come from the input URL (or RSS) |
| Author | ✅ | `uploader` (`TerraByteTerror`) |
| Post title | ✅ | verbatim |
| Post body / caption | ❌ | Not exposed by the extractor |
| Post type | ⚠️ derive | Extraction success + formats ⇒ native video |
| `is_video` | ❌ `None` | Not set for Reddit; derive instead |
| Native media identification | ✅ | extractor `Reddit` + `v.redd.it` hosts |
| Direct video URL | ✅ | video-only formats |
| Audio stream / presence | ✅ streams, ⚠️ presence | Audio-only DASH exists; probe to confirm real audio |
| Duration | ✅ | 11 s |
| Width / height | ✅ | 720×1280 |
| Thumbnail / poster | ✅ | `thumbnail` present |
| MIME / container | ✅ | `ext: mp4` / `m4a` |
| Media URL expiry | ⚠️ **unverified** | No `expires`/`sig`/`?v=` surfaced on the top-level URL — must be confirmed on the actual stream URLs before relying on retry-after-expiry |
| Crosspost / source identity | ❌ | Not exposed |
| External-host destination | ✅ | proven: two `r/videos` posts resolved to the `Youtube` extractor |
| Deleted / removed / private | ⚠️ **unverified** | Needs a bounded probe; expect extractor errors rather than typed reasons |
| One input → multiple billable rows | ✅ N/A | Zero cost; one post = one asset |
| Empty / failed-run billing | ✅ N/A | No provider, no billing |
| Rate limits | ⚠️ real | Reddit RSS 429s quickly; extractor traffic is separate but must be serialized |
| Authentication | ✅ none required for the posts tested | |
| NSFW / age-gate | ⚠️ **unverified** | Must be treated as a refusal until proven |

### 3.2 Reddit public JSON / embedded page metadata — **not usable**

`.json` is **403 unauthenticated** from this host on both `www.reddit.com` and
`api.reddit.com`; `old.reddit.com/.../.json` 302-redirects without data. The repo's
own discovery connector documents the same conclusion independently: *"The public
JSON API (`.json`) returns 403 unauthenticated and the OAuth API
(`oauth.reddit.com`) requires an app registration — neither is used."*

So the "public post JSON" route in the task brief is **not a viable metadata source
here**, and it is not a fallback to build on.

### 3.3 Direct `v.redd.it` manifests — **useful, but insufficient alone**

The DASH/HLS manifests are real and reachable (yt-dlp reads them). But a bare
`v.redd.it/<id>` URL carries **no post identity** (no author, title, subreddit,
duration, dimensions) and the ID is the *media* id, not the `t3_` post id — the two
differ (`tbdxx9kc3dqh1` vs `1wk5o10`). Driving acquisition from the manifest alone
would break identity reconciliation. Treat manifests as the **media layer underneath**
a post-identity layer.

### 3.4 Existing LOOM helpers that can be reused safely

| Helper | Reuse |
|---|---|
| `ytdlp_adapter.py` (probe → validate → download → ffprobe → hash → finalize, with `_assert_hygiene`) | **Primary.** Already the proven local lane for YouTube/Twitch/Kick |
| `assemble.build_mux_args` + `fmp4` + `media_normalize` | The mandatory video+audio mux, then MP4/H.264/AAC/yuv420p/fast-start |
| `local-helper/acquire.py` | Bounded download, HTML/fragment rejection, byte ceiling. **Its CDN allowlist is Meta/IG by default** — already made platform-scoped for TikTok, so Reddit needs `v.redd.it` added to a Reddit predicate |
| `source_adapters` registry + `platform_capabilities` | Reddit is `implemented_not_proven`; readiness flips only on a real bounded proof |
| `manual_intake` | Already accepts `reddit.com`, `www.`, `np.`, `old.`, `new.` hosts |
| Discovery connector (RSS) | Proven route for **identity/context** (post id, canonical URL, author, title, embedded source URLs) — explicitly *not* media metadata |

---

## 4. Option B — Apify actors

Only actors with demonstrated **downloadable media or manifest access** were
considered; search/metadata-only actors were excluded by rule.

| Actor | Exact post URL? | Media access | Price | Adoption / success | Scope limits |
|---|---|---|---|---|---|
| `automation-lab/reddit-public-video-downloader` | ✅ post URLs **and** `redd.it` short links | MP4 download **+ assembles separate HLS audio into M4A**; metadata-only mode; files in Apify KV | **$8.08 / 1,000 items** ($0.00808) | 3 users, 2 monthly, 100% success, no ratings | Excludes private, deleted, quarantined, age-gated, **external-hosted**, DRM, login-required; validates post IDs; requires `v.redd.it`; no proxy fallback |
| `dami_studio/reddit-video-scraper` | ❌ **`sources` = subreddits/users only** | Muxed MP4 with audio | $30 / 1,000 ($0.03) | 7 users, 1 monthly, 100% success, 5.0★ | Discovery-shaped, not exact-URL → **disqualified by the brief's rule** |
| `khadinakbar/reddit-media-downloader` | ✅ post URL | Images, GIFs, galleries, v.redd.it video/audio streams → Apify KV | **$0.005 / asset** | — | Cheapest media-capable option found |
| `easyapi/reddit-media-downloader` | ✅ multiple links | Direct **audio URLs** + metadata | — | — | Audio-focused |
| `agentx/reddit-video-downloader` | ✅ `video_url` input | Metadata by default; optional low/med/high file storage | — | — | Quality-gated file storage |
| `solid-scraper/reddit-media-downloader` | ✅ | MP4 video URLs + audio when available | — | — | — |
| `bytepulselabs/reddit-video-downloader` | ✅ post URLs | Saves media files | — | oldest listing (2025-08) | — |
| `maximedupre/reddit-media-downloader` | ❌ search-shaped | Media in rich metadata | — | — | Excluded by rule |

**Corroboration worth noting:** `dami_studio` independently documents both of my
findings — it uses *"Reddit's public RSS feeds (so it doesn't touch the rate-limited
JSON API)"* and explains that *"a v.redd.it clip is two files: the picture and the
sound."*

**Why Apify is not the primary:**
- The one exact-URL actor has **3 total users / 2 monthly** — thin operational
  evidence for a production lane.
- Its media is delivered from **Apify key-value storage**, not `v.redd.it`. That
  inserts a third-party host into the download path whose **expiry semantics are
  undocumented**, and it does not match the repo's existing "download from the
  platform CDN through a bounded local helper" contract.
- It returns **audio as a separate M4A** — so LOOM would still need its own mux
  (the task's own muxing case 8 would remain open).
- It **excludes external-hosted posts**, so case 19 would still need the platform
  adapters anyway.
- Cost is small but not free, and it adds a second billing surface next to the
  existing Apify ceilings.

---

## 5. Required content cases — support matrix

| # | Case | Local (yt-dlp) | Apify (automation-lab) |
|---|---|---|---|
| 1 | Standard post URLs | ✅ proven | ✅ documented |
| 2 | `redd.it/<id>` short links | ✅ **proven** (resolved to same media + t3 id) | ✅ documented |
| 3 | `/r/<sub>/comments/<id>/...` | ✅ proven | ✅ documented |
| 4 | `www.reddit.com` | ✅ proven | ✅ documented |
| 5 | `old.reddit.com` | ✅ **proven** | ✅ documented |
| 6 | `np.reddit.com` | ✅ **proven** | ⚠️ not listed |
| 7 | Native `v.redd.it` video | ✅ proven (20 formats) | ✅ documented |
| 8 | Separate video + audio streams | ✅ **proven**; mux mandatory (repo already owns it) | ⚠️ returns audio as a separate M4A — **mux still LOOM's** |
| 9 | Silent source media | ⚠️ must probe; metadata lies ("(no music)" still had audio streams) | ⚠️ `null` audio possible |
| 10 | Crossposts | ❌ not exposed | ❌ not documented |
| 11 | Deleted posts | ⚠️ unverified | ✅ documented refusal |
| 12 | Removed posts | ⚠️ unverified | ✅ documented refusal |
| 13 | Private / quarantined | ⚠️ unverified | ✅ documented refusal |
| 14 | Age-restricted / login-gated | ⚠️ unverified | ✅ documented refusal |
| 15 | Image-only posts | ✅ extractor declines → refuse | ✅ (media-capable variants) |
| 16 | Galleries | ✅ extractor declines → refuse | ✅ `khadinakbar` claims support |
| 17 | Text-only posts | ✅ extractor declines → refuse | ✅ refusal |
| 18 | GIF/GIFV-style media | ⚠️ unverified | partially documented |
| 19 | External-host posts → route to platform adapter | ✅ **proven** — two posts auto-routed to the `Youtube` extractor | ❌ **excluded from scope** |

**Case 19 is decisive.** The local route already routes external hosts to the correct
extractor in the same bounded tool — which is exactly the behaviour required — while
the leading Apify actor explicitly excludes external-hosted content.

---

## 6. Recommended architecture

```text
Reddit URL (www | old | np | redd.it)
→ identity: parse t3 post id (+ subreddit) from the URL          [local, deterministic]
→ context:  RSS entry for title/author/canonical URL             [proven route, rate-limited]
→ classify: yt-dlp metadata probe on the exact post URL          [metadata only]
     ├─ extractor == Reddit + v.redd.it ......... native video  → cases 7,8,9
     └─ extractor == Youtube/TikTok/… ............ external     → route to that adapter (case 19)
→ native: select ONE video-only format + ONE audio-only format
→ bounded local download of both streams (byte + duration ceilings)
→ mux locally (assemble.build_mux_args)                          [already proven]
→ shared normalization: MP4 / H.264 / AAC / yuv420p / fast-start [already proven]
→ hash → atomic finalize → title+filename contract → Library row
→ idempotent: repeat acquisition answers from the durable row, no second download
```

Refusals stay typed and honest: image-only, gallery, text-only, deleted, removed,
private, quarantined, age-gated → a classified refusal, never a fabricated asset.

**Apify fallback trigger (explicit, not automatic):** only if a bounded local proof
fails on *ordinary public* native video in a way the local lane cannot fix. Anything
paid requires its own authorization and its own ceiling, consistent with the TikTok
precedent.

---

## 7. Gaps to close with bounded probes before implementation

1. **Deleted / removed / private / quarantined / age-gated** behaviour and whether
   the refusal is *typed* enough to classify (cases 11–14).
2. **Crosspost identity** (case 10) — how the source post is exposed, if at all.
3. **Stream-URL expiry** on real `v.redd.it` stream URLs (must be confirmed, not
   assumed) so a retry-after-expiry policy is evidence-based.
4. **Silent-source detection** (case 9) — prove that probing the audio stream is the
   only reliable signal.
5. **GIF/GIFV** handling (case 18).
6. **Rate-limit posture**: RSS 429s quickly; the acquisition path must serialize and
   respect backoff rather than retry hard.
7. **Reddit CDN predicate** for the bounded downloader (`v.redd.it`), mirroring the
   platform-scoped change already made for TikTok.

---

## 8. Explicit non-actions

No paid actor called · no media downloaded · no production rows written · no ledger
or ceiling change · no Discord card posted · no Reaction Library change · no code
implemented. This document is the deliverable and is **not committed**; it is left
untracked for review.
