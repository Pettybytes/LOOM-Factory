"""One reliable way to reach the shared human-title contract from the review service.

``infra/acquisition/title_contract.py`` is the SINGLE definition of what counts as
a human title — and, centrally, that a provenance pointer (a source URL) is not
one. There is deliberately no second copy of that rule anywhere.

Reaching it BY NAME is not reliable from here. ``infra/acquisition`` also contains
a module literally called ``acquisition.py``, so ``import acquisition`` resolves to
THAT MODULE whenever ``infra/acquisition`` sits on ``sys.path`` ahead of ``infra/``
— which is exactly the layout the review test harness uses. Loading the file by
its known path is layout-independent and cannot be shadowed, so a caller either
gets the real contract or an explicit file error.

Only the pieces the review service actually needs are re-exported; this is an
adapter, not a second implementation.
"""

from __future__ import annotations

import importlib.util
import pathlib
from typing import Any

_TITLE_CONTRACT_PATH = (pathlib.Path(__file__).resolve().parents[1]
                        / "acquisition" / "title_contract.py")

_module: Any = None


def contract() -> Any:
    """The shared title contract module, loaded once from its file path."""
    global _module
    if _module is None:
        spec = importlib.util.spec_from_file_location(
            "loom_title_contract", _TITLE_CONTRACT_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load the title contract at {_TITLE_CONTRACT_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def is_provenance_reference(value: Any) -> bool:
    """True when a value identifies WHERE content came from, not what it is."""
    return bool(contract().is_provenance_reference(value))


def clean_title(value: Any) -> str:
    """Normalize a human title for display."""
    return str(contract().clean_title(value))


def resolve_display_title(**kwargs: Any) -> str:
    """Build a human display title using the shared priority order."""
    return str(contract().resolve_display_title(**kwargs))
