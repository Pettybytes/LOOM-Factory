# Apify Instagram Acquisition — broadened provider search (v2)

> Phase 1 artifact (v2). **$0 spent, no token used, no API call, no migration, no adapter.**
> Read-only. STOP for Bryan's provider decision.

## Decision (2026-09-17)

**HOLD `agentx` / `thenetaji` / `crawlerbros`.** The scalable path is the
**official Apify-maintained Actors**, which return a *direct video CDN URL in the
base metadata result* — no premium "stored file" event, no usage charge — at
**$0.0026–0.0027 per Reel**. That is ~4× under the ≤$0.01 preferred target and
~110× cheaper than `agentx` ($0.29).

---

## The key finding (category 6 + 1)

LOOM does **not** need an Actor that stores a file (the "$0.20 stored file"
premium). The official Apify Actors return the **original signed video URL** as
metadata. LOOM fetches that URL **locally, immediately** (before its ~34 h expiry),
validates (ffprobe + SHA-256), and finalizes to the Reaction Library — the same
local validation pipeline already built. No remote copy to delete.

| Official Actor | Result price (Free) | Usage charge | Video URL in result | Adoption / rating |
|---|---|---|---|---|
| `apify/instagram-reel-scraper` | **$0.0026** ($2.60/1,000) | **none** | ✅ "video URL + downloaded video URL" + transcript + duration | 145,555 users · 13,702 mo · 100% success · 4.39★ |
| `apify/instagram-scraper` | **$0.0027** ($2.70/1,000) | **none** | ✅ "image and video URLs" (`videoUrl`) | 396,415 users · 43,140 mo · 100% success · 4.70★ |

Both are **Apify-owned** (not community), pay-per-event, "not charged for
platform usage", and accept a **direct Reel/post URL** with a per-URL **results
limit** (→ `maxResults=1` is enforceable).

---

## Top 3 ranked (contract compliance → unit cost → reliability → complexity)

### 1. `apify/instagram-reel-scraper` (official) — **recommended**
- **Provider/owner:** Apify (first-party). Reel-specialized.
- **Direct single-Reel URL:** ✅ accepts reel URLs individually.
- **Video-with-audio:** returns "original video URL" + "downloaded video URL" (+ transcript, duration, music, dimensions). ⚠️ verify in Phase 3 that the original URL is a muxed mp4 with audio.
- **Credentials/cookies:** none (public page only).
- **All-in success cost:** **$0.0026/Reel** (metadata result) + **$0** local fetch.
- **Failure cost:** ~$0 (pay-per-result; 0 results → 0 result events). Verify no minimum-run fee.
- **Min commitment:** none (pay-per-use; Free tier ~2,000 reels with $5 credit).
- **Expansion controls:** LOOM hard-codes direct-URL mode + single URL + results limit 1; profile/username inputs omitted. Reel is a single video (no carousel expansion).
- **Reliability/adoption:** 145,555 users, 13,702 monthly, 100.0% success, 4.39★.
- **Output contract:** 128 typed fields (caption, transcript, duration, likes/shares/views, music, video URLs, creator).
- **Deletion/retention:** n/a for the direct-URL path (LOOM fetches from IG CDN; no Apify file retained). If the "downloaded video" feature is used, its KV record is deletable via API.
- **Security/supply-chain:** first-party Apify (highest trust tier). Signed URL is volatile (~34 h) — never persisted.
- **Complexity:** low — one Actor call + local fetch (pipeline already exists).

### 2. `apify/instagram-scraper` (official) — **fallback**
- **Provider/owner:** Apify (flagship Instagram Actor).
- **Direct single-Reel URL:** ✅ URL mode ("Post URL or Reel URL → details"), `Results limit` per URL.
- **Video-with-audio:** returns `videoUrl` (CDN mp4) + `videoDuration`/`videoPlayCount`. ⚠️ verify audio in Phase 3.
- **Credentials/cookies:** none.
- **All-in success cost:** **$0.0027/Reel** (Free); Starter $2.30/1,000, Scale $1.90, Business $1.50.
- **Failure cost:** ~$0 (per-result).
- **Min commitment:** none.
- **Expansion controls:** URL mode + results limit 1; search mode omitted.
- **Reliability/adoption:** 396,415 users, 43,140 monthly, 100.0% success, 4.70★ (highest of all).
- **Output contract:** post/reel schema incl. `videoUrl`, engagement, owner, carousel children.
- **Deletion/retention:** n/a (direct URL path).
- **Security/supply-chain:** first-party Apify.
- **Complexity:** low.

### 3. Browser-extension manual download — **zero-cost emergency route (already built)**
- **Provider/owner:** LOOM (`infra/chrome-extension`).
- **Direct single-Reel URL:** ✅ (user is on the exact Reel tab).
- **Video-with-audio:** depends on browser capture; the automated fMP4 auto-assembly failed (`INSUFFICIENT_FMP4_SET`), but a *human manual save* remains a fallback.
- **Credentials/cookies:** uses the user's own logged-in session (no export).
- **All-in success cost:** **$0**.
- **Failure cost:** $0.
- **Min commitment:** none.
- **Expansion controls:** single active tab, content-id locked.
- **Reliability:** manual (human-dependent); automated path closed out as `blocked_local_fmp4_incomplete`.
- **Output contract:** local file only (no structured metadata).
- **Deletion/retention:** local only.
- **Security/supply-chain:** first-party.
- **Complexity:** low, but **manual — keep clearly separated from automated tenant sourcing** (per policy).

---

## Economics at scale (all-in, metadata-result + local fetch)

| Candidate | /Reel | 100/mo | 500/mo | 900/mo | 1,000/mo |
|---|---|---|---|---|---|
| `instagram-reel-scraper` | $0.0026 | **$0.26** | **$1.30** | **$2.34** | **$2.60** |
| `instagram-scraper` (Free) | $0.0027 | $0.27 | $1.35 | $2.43 | $2.70 |
| `instagram-scraper` (Business) | $0.0015 | $0.15 | $0.75 | $1.35 | $1.50 |
| ~~`agentx`~~ (HOLD) | $0.29 | $29 | $145 | $261 | $290 |
| Browser-extension manual | $0 | $0 | $0 | $0 | $0 (human time only) |

Target met: **≤$0.01/Reel by ~4×** on the Free plan; failed/empty-run exposure ~$0.

---

## The 6 categories — coverage

1. **Official Apify Actors** → ✅ **chosen** (above). They return a usable video URL for one exact post/Reel *without* separately storing the file.
2. **Other Apify Actors with strict direct-URL + maxResults=1** → official Actors already support URL mode + results limit; no community Actor beats $0.0026 with comparable trust.
3. **Private LOOM Apify Actor** → feasible but **not needed now**: a private Actor still pays compute + proxy + session infra to resolve Instagram, which won't beat $0.0026/result at LOOM's volume. Revisit only if volume or contract requirements change.
4. **Other reputable API providers** → no transparent pay-per-success provider found that matches Apify's first-party trust + $0.0026 + zero usage charge. Not pursued.
5. **Browser-extension manual** → ✅ retained as the $0 emergency route (ranked #3), separated from automated sourcing.
6. **Metadata returning a complete downloadable URL (avoiding stored-file premium)** → ✅ **this is the answer** — official Actors return the original signed video URL in the base result.

---

## Prior candidates — HOLD (from v1)

- **`agentx/instagram-reels-downloader`** — HOLD. $0.29/Reel fails production economics (100 = $29 … 1,000 = $290). Retained only as a documented emergency/manual fallback for rare high-value sources.
- **`thenetaji/instagram-video-downloader`** — HOLD. Unpublished per-MB fee; non-deterministic cost.
- **`crawlerbros/instagram-downloader-api`** — HOLD (disqualified). Expansion (`usernames`), carousel→N results, duplicate-writes issue, shared session pool, pricing discrepancy + variable usage.

---

## Recommendation

Adopt **`apify/instagram-reel-scraper`** as LOOM's default Instagram acquisition
provider (official, $0.0026/Reel, Reel-specialized with transcript), with
**`apify/instagram-scraper`** as the documented fallback and the **browser
extension** as the $0 emergency route. `agentx` demoted to emergency/manual-only.

**Verification gaps (Phase 3, before any real call — still $0 until then):**
1. Confirm the base result's video URL field is a **muxed mp4 with audio** (not
   video-only), and that the "downloaded video URL" is a *separate optional* event
   (not required to get a fetchable URL).
2. Confirm exact output field name (e.g. `videoUrl`) and signed-URL expiry (~34 h).
3. Confirm a 0-result (private/deleted) run bills **$0** (no minimum-run fee).
4. Confirm `maxResults=1` + single URL yields exactly one result (no carousel drift).

**Budget:** keep the **$1 hard ceiling** for the pilot — at $0.0026/Reel it now
covers ~380 Reels, so the prior "max 3" constraint is obsolete. Propose: **$1
ceiling, no fixed acquisition cap** (still one URL + one result per run, no
automatic retries, circuit breaker on ceiling).
