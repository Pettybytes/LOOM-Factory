# Segment 5 — delivered human-review source cards

Updated September 25, 2026. **Three real cards delivered and read back.**
Bryan changed acceptance to native shorts/clips, `funny beach moments`, and
human judgment with advisory ratings. These are **review options, not automatic
Grade-A acceptance**. All retain real scores and FIRE failures. No scores inflated.

## Delivered

| Platform | Discord message | Recommended window | Market / Content / Selection / Confidence |
|---|---|---|---|
| twitch | [1553148840482316383](https://discord.com/channels/1546266463663423518/1550207989569421444/1553148840482316383) | 0–42s | 79 / 75 / 100 / 100 |
| instagram | [1553149522069299362](https://discord.com/channels/1546266463663423518/1550207989569421444/1553149522069299362) | 0–13.26s | 0 / 82 / 18 / 52 |
| reddit | [1553150857460191414](https://discord.com/channels/1546266463663423518/1550207989569421444/1553150857460191414) | 0–6s | 68 / 85 / 90 / 20 |

- Twitch: verified India Love (`indialovewestbrooks`, broadcaster `1290016492`), dramatic smell reaction. Cached actual inspection reused; verified platform thumbnail.
- Instagram: Michelle Lewin's pig encounter on the beach, 13.26s complete inspected window. Public access failed; existing browser session successfully provided metadata and bounded media in-process. No cookie file or cookie value exported. Candidate still captured at 2.5s. View count unknown; Market 0 is not zero views. 748,053 likes and 71,667 comments observed.
- Reddit: adult creator's German-mother-at-the-beach comedy sketch, initial joke 0–6s, payoff at 5s. Full 54s source inspected. 12,147 native score and 225 comments observed. Candidate still at 2s. Canonical source verified through public web and native extractor/playback; plain urllib HTML client was blocked with 403.

Each card has canonical Open Source, actual preview, event/window, hook/payoff,
completeness, honest quality, and Approve/Reject/Hold controls. Cards remain for
Bryan's decision; no human approval was impersonated. Acquisition remains disabled.

## Repairs landed

- `e8d15d6`: source-review decision persistence, protected worker error boundary,
  deterministic duplicates, retryable pending-job dispatch, resolved card windows.
- `bd1a5aa`: tenant-isolated theme/media/creator gates, observed-demand Market
  model (strict threshold 75 unchanged), actual-media evidence propagation,
  measured contiguous native coverage, first-complete boundaries, timed payoff
  validation, independent moment ranking/dedup, and advisory human-review mode.
- `3a4772d`: Reddit bounded inspection prefers native HTTPS video/audio renditions
  over broken HLS audio segments. Runtime retry succeeded; no full-source fallback.

Tenant `dreemzy` / profile `segment5-acceptance-20260925` briefs for Twitch,
Instagram and Reddit now configure human-curated, media-first review. Numerical
market gates become advisory only in this explicit mode; metadata scores are not
fabricated. Theme, media, actual-event, adult/safety and completeness gates remain.
The redundant metadata LLM call is skipped in media-first mode. The real database
accepted the advisory queue filter. Adapter restarted and health verified live-off.

## Original approval defect and proof boundary

Original diagnostic card had an 11-character video ID where buttons required a
64-character fingerprint: HTTP 200 was an invalid-button reply, not worker success.
A separate identity lookup outside the worker error boundary was also fixed.
Diagnostic card `1551847589278781541` produced one durable harness review and one
queued job `353d82f0-859e-44ac-bda9-c0d12d1c2d0a`, bounds 0–8s; duplicate did not
create another job. This golf diagnostic is explicitly labelled **not Grade A**.
New human-review cards are not represented as approved. Full original six-platform
Grade-A acceptance and accepted-card human handoff are not claimed.

## Other lane evidence / exclusions

- Twitch: 48 unique clips, 17 strict Market survivors, six actually inspected.
  Four adult-qualified complete interactions; one selected as the useful review
  option. Two age-ambiguous clips withheld. Inventory not exhausted.
- Reddit: original bikini pass had no verified adult/theme survivor. New simple
  search returned 20 posts, three video references but unrelated/weak themes.
  Two additional public-search videos were inspected: paddleboard/mattress clip
  withheld for uncertain age; mother sketch delivered. One metadata-model response
  failed JSON parsing; media-first path avoided paying to repeat that metadata call.
- Instagram: original ten outputs were nine videos and one restricted page.
  Generic profile reels did not match; a surfer was a minor at recording and was
  rejected. The known adult pig incident became usable with authorized session access.
- TikTok: original ten videos had weak traction/unproven adult status, including
  two toddler-tagged results. No new purchase or false acceptance card.
- Kick: target identity not verified; sparse `indialove` account, empty clips,
  aliases 404. No creator substitution or repeated expensive search.
- YouTube: regular 87s news story removed from automatic acceptance after the
  native-shorts scope change; no news-source card posted. Earlier wrong payoff
  window was withdrawn. Long-form work was stopped.

## Verification

- Content intelligence: **468 passed**.
- Discovery: **148 passed**.
- Discord review: **255 passed**.
- Acquisition: **578 passed**.
- Total: **1449 passed** across relevant suites.
- Python compile, diff whitespace and credential-pattern checks passed.
- Migration 038 remains untracked, unstaged, unmodified; SHA1
  `3da12826ef96dd05ce089b490ee490e66baf01c0`.
- `LOOM_ACQUISITION_LIVE=0`; no acquisition, edit, render or publication executed.

## Costs — do not confuse with agent/OpenAI credits

Apify actual reconciled discovery: **$0.065** (TikTok $0.038, Instagram $0.027).
25 unique cached successful Gemini responses: **$0.058758 usage-based
estimate**, not verified billing. One failed response has unknown cost; its
reservation remains outstanding. Purpose-scoped model exposure remains bounded.
These figures **exclude Bentley/OpenAI agent inference credits**, including the
$54 Bryan reported; that charge was not independently audited here. This continuation
reused cached artifacts and used free native/browser access, not another Apify run.

## Durable evidence

Runtime receipts and caches: `/tmp/loom-segment5-20260925/` (private local artifacts).
Final card readback: `human-cards-verified.json`; model responses: `inspection-cache`;
focused/full suite logs in the same directory. Raw artifacts may include internal
media URLs; never publish raw dumps. This report contains only canonical/card links.
