# Apify Instagram Actor — 3-way side-by-side evaluation

> Phase 1 artifact (updated). **$0 spent, no Actor call, no token used.**
> Read-only comparison. STOP for Bryan's provider + budget decision.

## Executive summary

- **`crawlerbros/instagram-downloader-api` is DISQUALIFIED** for the LOOM pilot
  on multiple mandatory criteria (see "Disqualifying evidence" below). Its
  "much cheaper" price is **not trustworthy** for a cost-ceilinged pilot: the
  advertised price is disputed, it is pay-per-event **plus variable usage
  charges**, one post can expand to multiple results (carousel) and to duplicate
  results (open bug), and it embeds a shared Instagram session pool.
- **`agentx/instagram-reels-downloader` remains the pilot recommendation** —
  the only candidate that meets *all* criteria with a **flat, deterministic
  price** ($0.29/Reel, no usage charge).
- **`thenetaji/instagram-video-downloader`** is a viable alternative with higher
  adoption but a **non-deterministic per-MB transfer fee** and the lowest
  reliability (87.8% success, 3.40★).

---

## Side-by-side (18 criteria)

| # | Criterion | `agentx/instagram-reels-downloader` | `thenetaji/instagram-video-downloader` | `crawlerbros/instagram-downloader-api` |
|---|---|---|---|---|
| 1 | Actor ID / owner | ID `59aU9vig8wglWOBe1` · **AgentX** (community) | name-resolved · **The Netaji** (community) | name-resolved · **Crawler Bros** (community) |
| 2 | Build/update | verified 2026-08-09; exact build via versions tab | not captured; verify versions tab | listing pub ~2025-12-14; active issues; verify versions tab |
| 3 | Pricing model | pay-per-event, **no usage charge** | pay-per-event **+ usage** (per-MB) | pay-per-event **+ usage** (compute/proxy/storage) |
| 4 | Cost, 1 Reel + file | **$0.29** flat (Start .01 + Meta .08 + Download .20) | $0.001 result + **unpublished per-MB** | "from $1/1,000" + usage; price **disputed** |
| 5 | Compute/proxy/storage/transfer | **none** ("not charged for platform usage") | per-MB bandwidth (rate unpublished) | yes — variable usage charges |
| 6 | Failed/empty runs charged | file that fails → "not billed"; Start $0.01 likely still charged | failed/skipped inputs "never charged" | not documented; likely usage still billed |
| 7 | Startup/minimum/run charge | Actor Start $0.01 | none stated | none stated (usage floor) |
| 8 | One direct URL strictly limited to one result | **yes by design** (`video_url` string) | `codes` array (bulk-capable; boundary forces single) | **no** — `postUrls` array + `usernames` |
| 9 | Single Reel → one or many billable events | one row = one result | one result + per-MB | one record per media file; carousel → N |
| 10 | Carousel-expansion controls | n/a (Reel/post focus; carousel unverified) | not explicit | **expands** — "every image … separate files" |
| 11 | Media file URL / expiration | KV store URL; default retention | stored file (saveMedia) or 34h directUrl | KV store URL; "temporary", default retention |
| 12 | Remote-file deletion | KV store record deletable via API | KV store record deletable via API | KV store record deletable via API |
| 13 | Audio/video quality | `low/medium/high` (480/720/2160p cap) | `480/720/1080/best` | resolution captured; no quality selector documented |
| 14 | Instagram credentials/cookies | **none** ("No Instagram key") | **none** ("No login required") | ⚠️ **shared session pool** used internally |
| 15 | Usage / reviews / reliability | 9 users · 5 monthly · **100%** success · 4.00★ | 386 users · 39 monthly · **87.8%** · 3.40★ | 236 users · 37 monthly · 99.9% · 5.00★ |
| 16 | Input schema | `video_url`, `video_quality`, `max_comments` | `codes[]`, `saveMedia`, `quality` | `postUrls[]`, `usernames[]`, `maxPosts_per_username` |
| 17 | Output schema | 43 typed fields, one row | pk/code/media_type/counts/savedFile/directUrl | one record/file (filename, type, media_meta_data…) |
| 18 | Supports LOOM URL form (`/reel/<id>`) | ✅ | ✅ (shortcode/ID/URL) | ✅ (`/p/`, `/reel/`, `/tv/`) |
| 19 | Supply-chain/security | ⚠️ 9 users = low adoption | ⚠️ community dev; per-MB opaque | ⚠️ shared sessions; duplicate-writes + pricing issues |

---

## Recommendation standard — compliance matrix

> Choose the **cheapest** Actor that meets **all** of:

| Requirement | agentx | thenetaji | crawlerbros |
|---|---|---|---|
| accepts exactly one canonical Reel URL | ✅ | ⚠️ (array, but no profile input) | ❌ array + `usernames` |
| returns exactly one video with audio | ✅ | ⚠️ | ❌ carousel → N; duplicate-writes bug |
| requires no IG credentials/cookies | ✅ | ✅ | ⚠️ shared session pool |
| prevents profile/search/pagination/related expansion | ✅ | ✅ (no search input) | ❌ `usernames` + `maxPosts_per_username` |
| stable output contract | ✅ 43 fields | ✅ | ⚠️ duplicate-writes issue |
| sufficiently maintained + reliable | ⚠️ 9 users / 100% | ⚠️ 386 users / 87.8% | ⚠️ 236 users / 99.9% + open issues |
| local retrieval + remote cleanup | ✅ | ✅ | ✅ |
| **deterministic, pre-calculable cost** | ✅ flat $0.29 | ❌ per-MB unpublished | ❌ usage + price discrepancy |

**Result:** `crawlerbros` fails 4 mandatory requirements. `thenetaji` passes the
functional requirements but has a non-deterministic per-MB fee and the lowest
reliability. `agentx` is the only candidate passing all requirements with a
deterministic price.

---

## Why `agentx` over the "much cheaper" Crawler Bros Actor

The comparison is not "cheaper vs pricier." It is "**compliant + deterministic**
vs **non-compliant + non-deterministic**." Specific reasons:

1. **Not actually single-URL.** `crawlerbros` takes `postUrls[]` *and*
   `usernames[]` with `maxPosts_per_username` (1–100) — i.e., profile scraping
   and pagination are built into the Actor surface. LOOM's boundary would have to
   *fight* the Actor to keep it to one URL, rather than the Actor *guaranteeing*
   one URL by design (as `agentx`'s single `video_url` string does).

2. **"Cheaper" is not established.** The document cited `$5/1,000 ≈ $0.005`; the
   current listing shows **"from $1.00/1,000"**, and the Actor has an **open
   "Pricing discrepancy" issue**. The real per-result price is unresolved.

3. **One post ≠ one billable event.** A carousel "downloads every image … as
   separate files" (N records for one URL), and there is an open **"duplicate
   dataset writes per post"** issue — so a single Reel can be billed more than
   once. The $0.005/result figure does not map to $0.005/Reel.

4. **Variable usage charges.** `crawlerbros` is pay-per-event **and usage**
   (compute + proxy + storage). An 8 MB video proxy transfer adds unbounded,
   non-deterministic cost on top of the result fee. `agentx` explicitly has **no
   usage charge** — $0.29 is the whole price.

5. **Shared Instagram session pool.** `crawlerbros` relies on "a managed pool of
   shared Instagram sessions." That is a ban/rate-limit risk and a ToS gray area
   that `agentx` (no IG key, public page only) and `thenetaji` (no login) avoid.

None of this equates price with reliability. `agentx` is chosen for
**single-URL-by-design + no-expansion + no-cookies + deterministic flat cost +
100% observed success** — not for its price. Its real weakness is **low adoption
(9 users)**, which is flagged and mitigated (pin Actor ID + build, keep
`thenetaji` as fallback).

> ⚠️ Scale note (honest): at 100–1,000 Reels, `agentx` is 60–300× the advertised
> `crawlerbros` result rate. For the **pilot** (≤3 acquisitions under a $1
> ceiling) the difference is immaterial ($0.87 vs ≈$0.03). **Before any scale-up,
> re-evaluate** `crawlerbros` (if its single-URL/carousel/duplicate/pricing issues
> are fixed) or another single-URL-only competitor.

---

## Cost — expected vs worst-case (single Reel with file, FREE tier)

| Actor | 1 Reel | 10 | 100 | 1,000 | Deterministic? |
|---|---|---|---|---|---|
| `agentx` (flat $0.29) | $0.29 | $2.90 | $29.00 | $290.00 | ✅ exact |
| `thenetaji` ($0.001 + per-MB) | ~$0.01–0.05¹ | ~$0.10–0.50¹ | ~$1–5¹ | ~$10–50¹ | ❌ per-MB unpublished |
| `crawlerbros` ("from $1/1k" + usage) | ~$0.001 + usage² | ~$0.01 + usage² | ~$0.10 + usage² | ~$1 + usage² | ❌ usage + price dispute |

¹ per-MB bandwidth rate is **not published** — estimate only, cannot bound.
² plus unbounded compute/proxy/storage usage and possible duplicate-writes
(2×) and carousel expansion (N×). Cannot be bounded in advance.

**Worst-case for the pilot ($1 ceiling):** only `agentx` gives a hard, bounded
number (3 × $0.29 = $0.87 < $1). The other two cannot be hard-bounded, which
violates the pilot's "reserve worst-case before running" rule.

---

## Recommendation

- **Pilot (this decision): `agentx/instagram-reels-downloader`** — meets all
  criteria, deterministic $0.29, no usage charge, 100% success. Keep
  `thenetaji/instagram-video-downloader` as the documented fallback.
- **Not `crawlerbros` now** — disqualified on single-URL enforcement, expansion,
  carousel/duplicate-writes, shared-session, and cost-determinism grounds.
  Re-evaluate only for scale, and only after its open issues are resolved.
- **Budget unchanged:** keep the **$1 hard ceiling** + **max 3** pilot
  acquisitions (3 × $0.29 = $0.87), pending Bryan's final decision.

**Verification gaps (Phase 2/3, before any call):** exact numeric Actor IDs for
`thenetaji` and `crawlerbros` (resolve via API tab); exact build tags for all
three; `thenetaji`'s per-MB rate; confirm `agentx` failed-run Start billing.
