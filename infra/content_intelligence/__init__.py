"""LOOM — Content Intelligence Preflight V1 + Combined Source Selection V1.

The missing content-understanding layer: evaluates WHAT happens INSIDE a
source (hook, emotion, conflict, surprise, payoff, story, etc.) — strictly
gated by actually-inspected evidence — and combines it with Topic Intelligence's
market evidence into a single Source Selection Score + separate Confidence.

Truth boundary: LOOM never infers visual/audio qualities from a title alone.
Every content dimension has a minimum evidence level (see ``evidence.py``);
the assessment schema (``contract.py``) rejects any score that exceeds its
evidence level. The combined selection (``selection.py``) keeps score and
confidence separate.

Pure stdlib; no model call, no media download, no I/O in the contract layer.
"""

from . import evidence, contract, selection, provider, probe  # noqa: F401
from . import providers, pipeline, store_supabase  # noqa: F401

__version__ = "1.0.0"

__all__ = [
    "evidence",
    "contract",
    "selection",
    "provider",
    "probe",
    "providers",
    "pipeline",
    "store_supabase",
    "__version__",
]
