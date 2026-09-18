# Reaction Library Relocation — Executed & Verified

**Date:** 2026-09-18 · **Branch:** `master` @ `b97be19` · **Commit:** none
**Provider calls:** 0 · **Downloads:** 0 · **Transcodes:** 0 · **Migrations:** 0 · **Deleted files:** 0

---

## 1. Copy-first migration, hash-verified

Copied (never moved) the whole tree into the visible root, then compared every file:

| File | Bytes | SHA-256 (prefix) |
|---|---|---|
| `library/instagram__DdQE_RUS0GJ__06e1da6120df.mp4` | 16,689,411 | `06e1da6120dfc9ab` ✅ |
| `library/instagram__DdZS1QRhrXE__9b22f887b2ff.mp4` | 23,721,338 | `9b22f887b2ff6a3b` ✅ |
| `library/instagram__DdQE_RUS0GJ__06e1da6120df__poster.jpg` | 64,010 | `2308095f4898845f` ✅ |

`src files: 3 · dst files: 3 · ALL IDENTICAL: True` — the switch happened only after every hash matched.

## 2. One source of truth with an override

New `infra/acquisition/library_root.py`:

```
ENV_VAR      LOOM_REACTION_LIBRARY_ROOT
DEFAULT_ROOT ~/Movies/LOOM-Reaction-Library
LEGACY_ROOT  ~/.openclaw/loom-reaction-library
BACKUP_ROOT  ~/.openclaw/loom-reaction-library.backup-2026-09-18
```
`library_root()` · `library_dir()` · `tmp_dir()` · `ensure_dirs()` · `describe()`. Every runtime path
resolves through it, and the override is honoured (test-asserted).

## 3. Every reference updated

**Runtime code (9 files):** `local-helper/helper.py` (root + gallery + media routes),
`discord-review/adapter.py` (live-runner `library_dir`/`tmp_dir`), `complete_attempt.py`,
`forensics_acquire_failure.py`, `verify_approve_click.py`, `prepare_live_proof.py`,
`prove_gate1_lane.py`, `acquisition/prove_apify_reel.py`, `acquisition/render_card_previews.py`.

**DB local-path references:** `loom_reaction_library.storage_url` for `DdZS1QRhrXE` re-pointed to
`/Users/user/Movies/LOOM-Reaction-Library/library/…` (1 row, CAS-filtered on the old value so a re-run
is a no-op). The `DdQE_RUS0GJ` row's `storage_url` was already empty — nothing to move.

**Docs/config:** `local-helper/README.md`, `CURRENT_STATE.md`, the `reaction_library.py` module
comment, and `card-previews/manifest.json`. Older dated reports were left untouched — they are
historical evidence of the old layout, not live configuration.

**Guard:** a repo-wide test asserts no runtime `.py` under `infra/` hardcodes the retired root again
(`library_root.py` excepted, which owns the legacy/backup constants).

## 4. Desktop shortcut

```
~/Desktop/LOOM-Factory/Reaction-Library -> ~/Movies/LOOM-Reaction-Library
resolves to: /Users/user/Movies/LOOM-Reaction-Library
```
No files are copied or duplicated — a real path, not a second tree.

## 5. Git hygiene

`.gitignore` gained `Reaction-Library`, so the large media never lands in git.

## 6. Browsable gallery

`GET http://127.0.0.1:8787/library` → a grid of every acquired asset showing **poster, filename,
platform, duration, size, acquisition status and acquired date**; each card opens its detail/player
page. Supporting routes, all verified:

```
/library                                 200   (both assets listed with all fields)
/library/<sha256>                        200   detail + native player
/library/<sha256>/media                  206   video/mp4, Range-capable (seeking)
/library/<sha256>/poster                 200   image/jpeg
```

A poster was generated for `DdZS1QRhrXE` (`extract_poster`, single frame, **no transcoding**, asset
SHA-256 re-verified unchanged); `DdQE_RUS0GJ` already had one. Posters are served only from inside
`LIBRARY_DIR` with containment checks.

## 7. Services restarted

Only the two that needed it: local helper (**pid 1314**) and the discord-review adapter (**pid 1381**).
`helper.py` was previously POST-only; routing for the library pages lives in `do_GET`.

## 8. Verification

| Check | Result |
|---|---|
| Desktop shortcut opens the real library | ✅ resolves to `~/Movies/LOOM-Reaction-Library` |
| Existing Discord library links still work | ✅ card advertises `http://127.0.0.1:8787/library/06e1da…` → **200** |
| Both videos play from the new location | ✅ ffprobe: `h264 720×1280 aac · 77.65 s` and `55.26 s`; full reads served at **200** with matching hashes (`06e1da…`, `9b22f887…`) |
| New acquisitions will write to the new location | ✅ `library_root.library_dir()` → `/Users/user/Movies/LOOM-Reaction-Library/library` (the adapter resolves this at click time) |
| The hidden OpenClaw dir is no longer active | ✅ renamed to the backup; `legacy_root().is_dir()` → **False**; gallery/playback unaffected |
| Backup preserved | ✅ 3 files, hashes intact |

*Observation:* QuickTime (pid 24957) held the old-location MP4 open during the move. A rename does not
break an open file handle, so playback continued — and it is a nice illustration of why the library
belongs somewhere visible and intentional.

## 9. Tests

```
acquisition     324 passed   ( +10 new: root resolution, override, gallery, anti-hardcode guard )
discord-review  134 passed
local-helper     76 passed
total           534 passed
```

---

## Notes for future moves
Set `LOOM_REACTION_LIBRARY_ROOT` to relocate without touching code; nothing else reads a hardcoded
root. The backup at `~/.openclaw/loom-reaction-library.backup-2026-09-18` can be removed once you're
satisfied — I have not deleted anything.
