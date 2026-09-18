"""Single source of truth for the Reaction Library root (relocated 2026-09-18).

The active root is:

    $LOOM_REACTION_LIBRARY_ROOT   (if set)
    ~/Movies/LOOM-Reaction-Library (default)

It was relocated out of the hidden ``~/.openclaw/loom-reaction-library`` on
2026-09-18 so the library lives somewhere a human can actually browse, with the
old directory preserved as a ``.backup-2026-09-18`` sibling.

Every runtime path resolution (adapter, live runner, local helper, tooling, tests)
must come through this module — never a hardcoded root — so a future deployment
moves the library by setting one environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "LOOM_REACTION_LIBRARY_ROOT"
DEFAULT_ROOT = "~/Movies/LOOM-Reaction-Library"
LEGACY_ROOT = "~/.openclaw/loom-reaction-library"
BACKUP_ROOT = "~/.openclaw/loom-reaction-library.backup-2026-09-18"


def library_root() -> Path:
    """Active root, honouring the environment override."""
    raw = (os.environ.get(ENV_VAR) or "").strip() or DEFAULT_ROOT
    return Path(os.path.expanduser(raw)).resolve()


def library_dir() -> Path:
    """Where finalized assets live."""
    return library_root() / "library"


def tmp_dir() -> Path:
    """Scratch space for in-flight downloads (never the final destination)."""
    return library_root() / "tmp"


def legacy_root() -> Path:
    return Path(os.path.expanduser(LEGACY_ROOT)).resolve()


def backup_root() -> Path:
    return Path(os.path.expanduser(BACKUP_ROOT)).resolve()


def ensure_dirs() -> Path:
    library_dir().mkdir(parents=True, exist_ok=True)
    tmp_dir().mkdir(parents=True, exist_ok=True)
    return library_root()


def describe() -> dict:
    """Health/verification view: active root plus how it was chosen."""
    root = library_root()
    lib = library_dir()
    files = sorted(p.name for p in lib.iterdir() if p.is_file()) if lib.is_dir() else []
    return {
        "env_var": ENV_VAR,
        "override_set": bool((os.environ.get(ENV_VAR) or "").strip()),
        "root": str(root),
        "library_dir": str(lib),
        "tmp_dir": str(tmp_dir()),
        "exists": root.is_dir(),
        "asset_files": len([f for f in files if f.endswith(".mp4")]),
        "files": files,
        "legacy_root": str(legacy_root()),
        "legacy_root_active": False,
    }
