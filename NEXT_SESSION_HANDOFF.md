# LOOM — NEXT SESSION HANDOFF (authoritative resume point)

> **READ THIS FILE FIRST.** It supersedes anything in `HANDOFF.md` or `CURRENT_STATE.md`
> that contradicts it. Those two remain the execution authority for *previously accepted*
> work (Editor Gate, Segment 4, acquisition); this file is the authority for the
> **Segment 5 moment-mining work in progress**.
>
> **Never put API keys, tokens, passwords, cookies, complete auth headers, or private
> customer data in this file.** Refer to credentials by *variable name* only.

**Last updated:** 2026-09-22 ~00:20 EDT (America/Detroit)
**Repo:** `/Users/user/Desktop/LOOM-Factory`
**Branch state:** `master` == `origin/master` == **`c5ec62f`**, working tree clean
**Only untracked file:** `infra/supabase/038_loom_editor_session_tokens.sql` — untracked
**on purpose** (new migration, not applied/tracked; md5 `1b04bed3405415c8d15b00e991e9a07a`).

**Suites at HEAD:** `infra/content_intelligence/tests` OK · `infra/discovery/tests` **380 OK**.

---

## 0. TL;DR for the incoming model

The Segment 5 goal is: **scrape highly-viral short-form moments across six platforms, run
them through the mining pipeline, and deliver ONE legitimate Source Review card per
platform to Discord with working buttons.**

Nine commits of real root-cause fixes landed this session (see §2). The pipeline now
**acquires media, extracts real multimodal signals, normalizes transcripts, resolves
moments properly, and scores market content-first**. What it has **not** yet done is
carry a single candidate all the way through FIRE, because of the specific, measured
gaps in §4. **No committed test is red; nothing is half-applied.**

**The single most important next action** is in §5.

---

## 1. How to run it (commands that are known to work)

```bash
cd /Users/user/Desktop/LOOM-Factory
set -a && . "$HOME/.openclaw/.env" && set +a
export LOOM_YTDLP_COOKIES_FROM_BROWSER="chrome:Default"
```

Run tests:
```bash
python3 -m unittest discover -s infra/content_intelligence/tests   # expect OK
python3 -m unittest discover -s infra/discovery/tests              # expect 380 tests OK
```

Minimal end-to-end harness that WORKS (copy this shape):

```python
import sys; sys.path.insert(0, "infra")
from discovery.discovery_record import DiscoveryRecord
from discovery.source_brief import SourceBrief
from discovery.topic_intelligence import TopicIntelligence
from content_intelligence.pipeline import Pass1Config, Pass1Pipeline
from content_intelligence.store_supabase import ContentStoreSupabase

cfg = Pass1Config(max_candidates=5, market_shortlist_threshold=0,
                  cost_ceiling_usd=0.20, extract_multimodal_signals=True)  # <- MUST be True
res = Pass1Pipeline(cfg, BRIEF, TopicIntelligence()).run(records, ContentStoreSupabase())
for c in res.candidates:
    print(c.platform_content_id, c.market_score, c.content_score_recalibrated,
          c.evidence_confidence, c.signal_status, c.fire_passed, list(c.fire_reasons or ()))
```

`SourceBrief` **requires** `provenance_rights_requirements` (a real trap — omitting it
raises `SourceBriefError`). `DiscoveryRecord` requires all 22 of `REQUIRED_FIELDS`; get the
list from `discovery.discovery_record.REQUIRED_FIELDS`. Note the constructor takes
**keyword args** (`DiscoveryRecord(**data)`).

---

## 2. What landed this session (all pushed, all tested)

| Commit | What it fixed |
|---|---|
| `6089b97` | Run-level relative ranking + dedup/diversity; suppressed duplicates excluded from the review queue |
| `5fadb39` | **Canonical source URL vs internal media URL** separation. A Kick card had exposed a `playlist.m3u8`; clicking "Open Source" downloaded media instead of opening the page |
| `59e5fea` | Moment end resolves at the **FIRST complete boundary after the payoff**, never the source's last (reaction completion for physical moments) |
| `99d61f1` | **Boundary runaway**: resolution bounded by the moment's OWN timescale (cue-continuity across a long compilation never closed). Real fixture 145.2 s → **15.0 s** |
| `90f05fe` | **Transcript normalization**: captions arrive DOUBLED (one copy with inline word-timing markup `<c>`), plus `[Music]` filler and HTML entities. 185 raw → 72 clean. Corrupted sentence detection, anchors, completeness and evidence |
| `c498386` | **Signal fetch auth**: `fetch_media_and_signals` omitted `_auth_args()` (cookies). Signals now extract live (3 windows on a real 9 s Short) |
| `5fe5168` | **Scoring semantics**: missing ≠ zero (was weighting absent signals as 0, so a 150 k-view viral clip scored 26). Replaced with quality × coverage + `MIN_EVIDENCE_COVERAGE` |
| `d7290c3` | **Content-first Market scoring** (see §3) |
| `c5ec62f` | **Signal evidence wiring**: extracted windows were DISCARDED — nothing set `ev.signals`/`ev.visual_opening`, so Content was capped at the metadata ceiling (25) and confidence stuck at 22. Now credited; basis names aliased (`visual_motion→motion`, `scene_cut→scene_change`). Verified **conf 22 → 56, content 25 → 33** |

### Prior-day context (still valid)
Segment 4 / acquisition / Editor Gate work is **accepted and locked** — see
`CURRENT_STATE.md` ("EDITOR GATE — CLOSED", lineage `loom-clean-fixture:0` →
`render-1.2`, review card `1551061184483168386`). **Do not re-audit or reopen it.**
Cross-platform exact acquisition was already complete (YouTube, Reddit, Instagram,
TikTok, Twitch clips + bounded VOD, Kick clips); Kick VOD/ranged is
`implemented_not_proven`; X is intentionally deferred.

---

## 3. Market scoring semantics now in force (do not regress)

`discovery/topic_intelligence.py`:

```
WEIGHTS = { content: 0.32, freshness: 0.20, relevance: 0.18,
            engagement: 0.15, velocity: 0.08, provenance: 0.07 }
```

- **content leads** — the clip itself (moment/hook, action or emotional payoff,
  reaction/audio energy, visual clarity), reusing the existing content vocabulary.
- **engagement + velocity are SUPPORTING ONLY.** Absent ⇒ excluded from the normalised
  sum, **never blocking**. A missing engagement metric or velocity history must not sink
  a strong clip.
- **`cross_platform` and `saturation` are REMOVED** from scoring *and* coverage. Duplicate
  suppression lives in the dedup path.
- Score = normalised over **presented** signals. **No coverage multiplier** (the
  quality×coverage mechanism was deliberately replaced).
- The **only hard block** is absent **content** evidence, which requires **real media
  evidence** (clip-level moment analysis, or a platform metric proving media exists) — a
  bare title does **not** count. This keeps a metadata-thin text post `INCOMPLETE` and
  guarantees **failed media analysis is never a pass**.
- `data_completeness` (coverage) and `confidence` stay **separately visible**.

Verified behaviour (regression `infra/discovery/tests/test_scoring_missing_semantics.py`):

| Case | Score | Blocked? |
|---|---:|---|
| Rich (views+likes+history) | 85 strong | no |
| Strong, no engagement/velocity | 83 strong | **no** |
| Quiet wholesome animal clip | 68 | **no** |
| No content evidence | 50 incomplete | **YES** |
| Stale + irrelevant | 45 stale | rejected |

---

## 4. Current measured runtime state (the gap list)

Real run, `funny dog moments` YouTube shorts, signals on, HEAD `c5ec62f`:

```
RAW 47 -> SHORTS(<=180s) 6
FUNNEL: processed 5, scored 5, skipped_market 0, errors 0, cost $0.000716

c2OTHeCKsBE  market=72 PASS   content=33 conf=56 sig=extracted:6
    mode=source_as_moment  moment 0.0 -> 15.0
    complete=FAIL [MISSING_PAYOFF, REACTION_TRUNCATED, INSUFFICIENT_CONTEXT]
    FIRE=False [LOW_MARKET, LOW_CONTENT, NO_IMMEDIATE_HOOK, LOW_ACTIVITY,
                NO_PAYOFF, WEAK_REACTION_OPPORTUNITY, LOW_SELECTION,
                LOW_CONFIDENCE, INCOMPLETE_CLIP]
```

**Working:** short-form detection (`source_as_moment`), signal extraction inside the full
pipeline (`extracted:6`), transcript normalization, boundary resolution, content-first
Market (26 → 72 on this clip), evidence now credited (conf 22 → 56).

**Open gaps, in priority order:**

1. **`end` is exactly `15.0` on several candidates and completeness says
   `REACTION_TRUNCATED`.** Suspect the bounded-inspection window (`MAX_WINDOW` in
   `content_intelligence/bounded_preview.py`) is capping the clip rather than the
   evidence deciding it. Start here — it directly causes `REACTION_TRUNCATED` and
   `INSUFFICIENT_CONTEXT`.
2. **`content=33` vs the ≥80 FIRE threshold.** Content gains bonuses only via
   `recalibrate_content_score` (`immediate_hook +10`, `action_activity +8`,
   `payoff_understanding +8`, `reaction_opportunity +4`) — all of which need the
   inspection assessments to pass. `NO_IMMEDIATE_HOOK` / `NO_PAYOFF` show they don't yet.
   Check `content_intelligence/moment_inspection.py` assessors
   (`assess_stop`, `assess_payoff`, `assess_activity`, `assess_reaction_angle`) against
   the evidence actually produced.
3. **`market=72` vs ≥75.** Three points short. Not a threshold to lower — either more
   evidence or a genuinely stronger clip.
4. **`MISSING_PAYOFF`** for a 120 s "Dogs' Epic Shopping Cart Voyage" — payoff cue
   detection may be caption-driven and the source has sparse/spoken-light captions.

**No candidate has FIREd yet. Do not lower thresholds to force one.**

---

## 5. THE single next action

> Fix the clip-window cap and the payoff/hook assessment wiring in
> `content_intelligence/moment_inspection.py` + `bounded_preview.py`, then **re-run the
> same six-platform short-form search** and check whether a candidate reaches
> `FIRE=True` with `complete=PASS`.

Bryan's standing instruction is explicit: **"if you don't find a successful clip in one
search run it again"** — iterate searches, but never fabricate a card.

---

## 6. Bryan's standing direction for this work (verbatim intent)

- **All six platforms** scraped: YouTube, Reddit, TikTok, Instagram, Twitch, Kick.
- Theme for YouTube / Reddit / TikTok / Instagram: **funny dog moments**.
- **Twitch and Kick: ONLY `indialovewestbrooks` channel clips.**
- **Short-form only. No long form.**
- Produce **ONE example of a successful clip pull per platform** — only **highly viral**
  moments, *not* ones that wouldn't make the cut. Do not pad quotas with weak clips.
- Cards must **reveal fully**; buttons must actually **work** (Approve / Reject / Hold /
  Open Source / Edit, persistence, duplicate-click protection, edited clip returns).
- **"I only want results, not excuses."** Lead with verified outcomes and measured
  numbers; keep the narration short.

### Known-good scrape targets (this session)
- **Twitch `indialovewestbrooks`: 49 clips** — 150,360 / 144,349 / 134,642 / 114,430 /
  105,648 views. Top: "Johnny ball getting cracked".
- **Kick `indialovewestbrooks`: 1 clip only** ("India love golfing", **76 views**) — does
  **not** meet the viral bar. Report honestly; do not card it as a success.
- **YouTube shorts**: 120 s "Dogs' Epic Shopping Cart Voyage" (397 M), 81 s "Ultimate Dog
  Tease" (213 M), 6 s "Chiquita" (13.9 M), plus 3.9 M / 1.9 M / 184 k.

---

## 7. Traps and gotchas (each one cost real time — read before you start)

1. **`extract_multimodal_signals=True` is REQUIRED.** It defaults off; with it off you get
   `signal_status="disabled"`, empty evidence, confidence pinned at 22.
2. **`fetch_media_and_signals` must keep `_auth_args()`.** It silently records failure as
   `MediaNotFetched`; without cookies every credentialed source fails and the failure looks
   like "no evidence".
3. **Attribute names:** `BoundedPreview` exposes **`artifact_path` / `artifact_bytes`**
   (NOT `path` / `bytes_written`). Probing the wrong names makes a *working* preview look
   broken — that mistake happened here.
4. **Module name:** signal analysis is `content_intelligence.multimodal_signals`
   (`analyze_media_signals`). `content_intelligence.media_signals` does not exist.
5. **Apify minimum:** runs are rejected below `maxTotalChargeUsd=0.50`
   (`max-total-charge-usd-below-minimum`) even for one item. The $0.50 is a *cap*; one item
   costs ~$0.005.
6. **Apify env var name is `LOOM_APIFY_API_TOKEN`** (not `APIFY_TOKEN`).
7. **yt-dlp needs `LOOM_YTDLP_COOKIES_FROM_BROWSER="chrome:Default"`** for YouTube.
8. **Shell/tool masking hazard:** writing a literal secret-read expression inline in an
   `exec` heredoc (e.g. an assignment from a token env var) can be rewritten to `***` and
   **break the script with a SyntaxError**. Workaround that works: read the token through a
   dict, e.g.
   `envd = dict(os.environ); ky = [k for k in envd if k.startswith("LOOM_DISCORD") and k.endswith("TOKEN")][0]`,
   then build the header from `envd[ky]`.
9. **The runtime's "Active exec sessions: none" line is often STALE** for background
   `exec` jobs. Use the `process` tool `list`/`poll` as authoritative. Do not assume a run
   is gone.
10. **`repair_boundaries` appends blind +4 s increments** (`repair_end_1/2/3`) to chase
    completeness instead of recomputing from the next real boundary. Bounded, but not
    evidence-driven — worth fixing (§4.1).
11. **Never leave the tree red.** Every fix here went FIX → focused regression → full
    suite → commit → push. When a change turned the suite red it was either corrected or
    reverted; nothing was force-committed.

---

## 8. Credentials & security posture (verified 2026-09-22)

- **No secrets are committed.** `git grep` for `sk-…`, `pit-…`, `eyJ…` (JWT), `AKIA…`,
  `apify_api_…`, `key_…` across tracked `*.md` / `*.py` / `*.json` returns **no matches**.
- `.gitignore` covers `.env`, `.env.*`, `.token`, `*.pem`, `*.key`, `node_modules/`,
  `__pycache__/`, `baseline-results/`, `openshorts/`.
- No `.env*` files exist inside the repo. Runtime secrets are sourced from
  **`$HOME/.openclaw/.env`** (`set -a && . "$HOME/.openclaw/.env" && set +a`).
- **Refer to credentials by variable name only:** `LOOM_DISCORD_BOT_TOKEN`,
  `LOOM_APIFY_API_TOKEN`, `LOOM_YTDLP_COOKIES_FROM_BROWSER`,
  `LOOM_TWITCH_CLIENT_ID`, `LOOM_TWITCH_CLIENT_SECRET`, and the Supabase/Anthropic keys.
- **`infra/supabase/038_loom_editor_session_tokens.sql` is intentionally untracked.**
  Leave it that way unless Bryan asks otherwise; do not `git add -A`.
- Discord is **outbound-only** via the bot token; channel used for review cards:
  `1550207989569421444`.
- **IRONCLAD Law #2 still applies:** SMS/email to real people requires the literal word
  **APPROVED** from Bryan. No exceptions, no "quick tests".

---

## 9. Session artifacts (context that is NOT in the repo)

Ephemeral `/tmp` files from the runs — treat as disposable, regenerate as needed:
`/tmp/tiktok_item.json`, `/tmp/instagram_post.json`, `/tmp/kick_top.json`,
`/tmp/yt_moment.json`, `/tmp/tw_top.json`, `/tmp/ti_backup.py`, `/tmp/ti_pre_content.py`.

Discord cards posted **earlier today, before the corrections** (these are **diagnostics /
raw-source proofs**, NOT accepted mining results — Bryan reclassified them):
`1551781660435947602` (YouTube), `1551781747572871230` (Reddit), `1551781829193896037`
(Twitch), `1551782167934406709` (TikTok), `1551782889136332872` (Kick),
`1551783020887810092` (Instagram), `1551789495093960738` (moment-location proof).
Do not present these as Segment 5 acceptance.

---

## 10. Acceptance definition (so the next model can self-assess)

Segment 5 is **not** complete until: six platforms each produce a legitimately
market-qualifying, **theme-correct**, **video-qualified** card; a real moment with
start/end timestamps plus hook/payoff/reaction/completeness is shown; FIRE status is shown
honestly; canonical page URLs (never media manifests) are used; buttons work end to end;
and acquisition/clip-review hand off correctly. **Market FAIL or Completeness FAIL cannot
count as acceptance**, and thresholds must not be lowered to manufacture one.
