"""Early tenant-fit gate — cheap, metadata-only, BEFORE expensive inspection.

Runs after market qualification and before any transcript/media/model work, so an
obvious tenant mismatch never pays for content inspection.

Grounded in the tenant's CONTENT UNIVERSE, never a creator blacklist: the same
term rules apply to every source. The content universe terms come from
``discovery.moment_queries`` — one source of truth, so sourcing seeds and the fit
gate cannot drift apart.

Ambiguity is allowed through: a mismatch must be clear to fail, because
hallucinating a rejection is worse than spending a little inspection budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable
import re

PASS = "PASS"
FAIL = "FAIL"
AMBIGUOUS = "AMBIGUOUS"

# Strong "moment" evidence — derived from the moment-query library so the two
# cannot drift. Values are matched as substrings against low-cardinality fields.
def _moment_terms() -> tuple[str, ...]:
    from discovery.moment_queries import CATEGORIES
    terms: set[str] = set()
    for seeds in CATEGORIES.values():
        for s in seeds:
            t = s.strip().lower()
            terms.add(t)
            # also index the head noun phrase (e.g. "wild streamer moment" -> "moment")
            for word in t.split():
                if len(word) > 4:
                    terms.add(word)
    return tuple(sorted(terms))


STRONG_MOMENT_TERMS: tuple[str, ...] = _moment_terms()

# Clearly outside the entertainment/reaction/internet-culture universe.
OFF_UNIVERSE_TERMS: tuple[str, ...] = (
    # Uniform category terms — applied to title/description/category/creator for
    # EVERY source. This is the content universe, NOT a per-creator blacklist.
    "tech", "technology", "gadget", "pc build", "coding", "programming",
    "consumer tech", "hardware", "teardown", "benchmark", "spec sheet",
    "unboxing", "tutorial", "how to", "review unit", "firmware", "driver update",
    "ssd", "gpu", "cpu", "laptop", "smartphone", "earbuds", "keyboard review",
    "router setup", "enterprise software", "documentation", "course",
    "lecture", "webinar", "productivity software", "plumbing",
)

# An otherwise off-universe item can still be a cultural EVENT worth reacting to.
VIRAL_EVENT_MARKERS: tuple[str, ...] = (
    "viral", "went viral", "everyone is talking about", "caught on camera",
    "breaks the internet", "internet famous",
)


@dataclass(frozen=True)
class TenantFitResult:
    verdict: str
    reasons: tuple[str, ...] = ()
    matched: dict[str, list[str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == PASS

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


def _hits(terms: Iterable[str], haystack: str) -> list[str]:
    return sorted({t for t in terms if t and t in haystack})


def tenant_fit(*, title: str = "", description: str = "", hashtags: Iterable[str] = (),
               creator: str = "", category: str = "", topics: Iterable[str] = (),
               universe_terms: tuple[str, ...] | None = None, brief=None) -> TenantFitResult:
    """PASS / FAIL / AMBIGUOUS with grounded reasons.

    Only a clear FAIL stops the pipeline. Creator names are used only as ordinary
    text evidence (category context), never as a blacklist.
    """
    terms = universe_terms if universe_terms is not None else STRONG_MOMENT_TERMS
    text = " ".join(str(x) for x in [title, description, category, *hashtags, *topics]).lower()
    ctx = " ".join(str(x) for x in [title, description, category, creator]).lower()

    requirements = getattr(brief, "mining_requirements", {}) or {}
    if requirements:
        allowed = requirements.get("creator_identities") or []
        if allowed and creator.casefold() not in {str(x).casefold() for x in allowed}:
            return TenantFitResult(FAIL, ("target_creator_mismatch",))
        groups = requirements.get("required_concepts") or []
        found = [[t for t in group if re.search(r"(?<!\w)" + re.escape(t.lower()) + r"(?!\w)", text)]
                 for group in groups]
        if any(not hits for hits in found):
            return TenantFitResult(FAIL, ("required_lane_concept_missing",),
                                   {"missing": ["|".join(g) for g, h in zip(groups, found) if not h]})
        # Metadata is only a shortlist. Inspected media must independently
        # corroborate every required concept and the real reaction-worthy event.
        return TenantFitResult(PASS, ("lane_metadata_qualified_pending_inspection",),
                               {"concepts": [t for group in found for t in group]})

    moment_hits = _hits(terms, text)
    off_hits = _hits(OFF_UNIVERSE_TERMS, ctx)
    viral_hits = _hits(VIRAL_EVENT_MARKERS, ctx)
    matched = {"moment": moment_hits[:6], "off_universe": off_hits[:6], "viral_event": viral_hits[:4]}

    if moment_hits:
        return TenantFitResult(PASS, (f"moment_evidence:{moment_hits[0]}",), matched)
    if off_hits:
        if viral_hits:
            # Off-universe topic that is itself a cultural event: allow inspection.
            return TenantFitResult(AMBIGUOUS, (f"off_universe:{off_hits[0]}", "viral_event_present"), matched)
        return TenantFitResult(FAIL, (f"off_universe:{off_hits[0]}", "no_moment_evidence"), matched)
    # Nothing conclusive either way — proceed rather than invent a rejection.
    return TenantFitResult(AMBIGUOUS, ("no_conclusive_signal",), matched)
