# OpenShorts Caption-Contract Fix — Recovery Artifact

**Purpose:** Preserve the LOOM caption/final-asset contract fix durably inside
the private `Pettybytes/LOOM-Factory` repository, since the upstream
`mutonby/openshorts` remote rejected the push with 403 (this clone's only
remote is upstream; no Bryan-owned fork exists yet).

**This artifact is NOT a replacement for establishing a proper Bryan-owned
OpenShorts remote later.** It only makes the current fix recoverable now.

## Identity

- **Upstream repository:** `https://github.com/mutonby/openshorts.git`
- **Local branch:** `loom/factory-mvp-baseline-fixes`
- **Local commit (the fix):** `9c4450a336bee30427bde2f22f213977b5b29fe3`
- **Base commit (parent):** `9299e5b56009c3bbe373d6dc71d77a630522d07e`
- **Affected file:** `app.py` (only)

## What the fix does

1. `_clip_asset_contract()` treats the renderer-declared `CLIP_READY` final
   asset (`ready_files`) as authoritative — it is printed by `main.py` only
   after the FULL reframe → watermark → hook → captions chain — instead of
   `_canonical_clip_file` (a `max(mtime)` guess that could surface a `hooked_`
   intermediate with captions missing).
2. `no_speech` is now **per-clip**: derived from transcript words/segments
   overlapping this clip's `[start, end]` window (`_clip_speech_stats`), never
   from whole-video segment count and never from a secondary/empty transcript
   fetch. Instrumental windows → `no_speech` (captions not required); spoken
   windows → captions expected.
3. Webhook clip entries carry `ready_file`, `master_asset`, `captions_applied`,
   `hook_applied`, `no_speech`, `transcript_segment_count`.

Runtime-verified against acceptance job `6c46c3ef-5eba-40bc-9b50-8f3457d1db79`
(source `b7QosSwMW84`): clip 0 instrumental → `no_speech: true`; clip 1 spoken →
`captions_applied: true`.

## Reapplication instructions

From the OpenShorts clone at `/Users/user/Desktop/LOOM-Factory/openshorts`
(branch `loom/factory-mvp-baseline-fixes`):

```bash
cd /Users/user/Desktop/LOOM-Factory/openshorts
git apply /Users/user/Desktop/LOOM-Factory/openshorts-recovery/0001-fix-caption-contract.patch
```

or, to re-apply as a commit from the patch file:

```bash
git am /Users/user/Desktop/LOOM-Factory/openshorts-recovery/0001-fix-caption-contract.patch
```

After applying, restart the OpenShorts backend so the in-memory change takes
effect:

```bash
cd /Users/user/Desktop/LOOM-Factory/openshorts
docker compose restart backend
```

## Exclusions (do NOT include)

- OpenShorts `/cloud` — not part of this fix and out of LOOM scope.
- Generated media / render output (`output/`).
- Secrets, credentials, `.env` files.
- `docker-compose.yml` and `security_utils.py` — these carry pre-existing
  uncommitted changes from prior LOOM sessions that are NOT part of this
  caption-contract fix and are intentionally excluded from the patch.

## When a Bryan-owned remote is established

Create a fork of `mutonby/openshorts`, add it as `origin`, push
`loom/factory-mvp-baseline-fixes`, and open a PR. Then this artifact can be
retired.
