"""Moment-oriented sourcing query library (configurable, not hard-coded phrases).

LOOM sources MOMENTS people react to — not "reaction videos". Generic seeds such
as "streamer reaction", "streamer fails" and "streamer rage" are deliberately
ABSENT and actively rejected, because they bias discovery toward weak
compilation/ragebait inventory.

Ideas were studied from donor repositories; none of those carry a licence file,
so every line here is an independent LOOM-native implementation (nothing copied).
"""

from __future__ import annotations

from dataclasses import dataclass

STREAMER_CREATOR: tuple[str, ...] = (
    "wild streamer moment",
    "crazy live moment",
    "unexpected live moment",
    "creator crash out",
    "wild fan interaction",
    "surprising creator moment",
)

PUBLIC_HUMAN: tuple[str, ...] = (
    "crazy fall",
    "prank gone wrong",
    "unexpected confrontation",
    "awkward interaction",
    "embarrassing moment",
    "accidental kiss",
    "crazy public moment",
    "surprising reveal",
)

ANIMALS: tuple[str, ...] = (
    "funny dog moment",
    "unexpected dog moment",
    "crazy animal moment",
    "funny animal interaction",
    "surprising animal behavior",
)

# Named creators are legitimate SOURCING TARGETS (not default seeds); they are
# composed with a moment type via compose_creator_moment().
CREATOR_TARGETS: tuple[str, ...] = ("IShowSpeed", "Kai Cenat")

CATEGORIES: dict[str, tuple[str, ...]] = {
    "streamer_creator": STREAMER_CREATOR,
    "public_human": PUBLIC_HUMAN,
    "animals": ANIMALS,
}

# Too broad; bias discovery toward compilation/ragebait inventory.
BANNED_GENERIC_SEEDS: tuple[str, ...] = (
    "streamer reaction",
    "streamer fails",
    "streamer rage",
    "reaction video",
    "reaction videos",
)


@dataclass(frozen=True)
class MomentQuery:
    """One sourcing seed plus the identity needed to measure its yield."""

    text: str
    category: str
    creator: str = ""

    @property
    def seed_key(self) -> str:
        """Stable key for per-seed yield tracking (candidates/market/FIRE)."""
        base = self.text.strip().lower()
        return f"{self.category}::{base}" if not self.creator else f"{self.category}::{base}::{self.creator.lower()}"


def is_banned_seed(text: str) -> bool:
    """True when a seed is a known over-broad generic phrase."""
    t = text.strip().lower()
    return any(t == b or t.startswith(b) for b in BANNED_GENERIC_SEEDS)


def moment_queries(
    categories: tuple[str, ...] | list[str] | None = None,
    creators: tuple[str, ...] | list[str] = (),
) -> list[MomentQuery]:
    """The configurable seed library.

    ``categories`` selects which moment categories to draw from; ``creators``
    additionally expands each category with ``<creator> + <moment type>`` seeds
    (e.g. "IShowSpeed + wild fan moment").
    """
    names = list(categories) if categories else list(CATEGORIES)
    unknown = [c for c in names if c not in CATEGORIES]
    if unknown:
        raise KeyError(f"unknown moment categories: {', '.join(unknown)}")
    out: list[MomentQuery] = []
    for name in names:
        for text in CATEGORIES[name]:
            out.append(MomentQuery(text=text, category=name))
            for creator in creators:
                out.append(compose_creator_moment(creator, text, category=name))
    return out


def compose_creator_moment(creator: str, moment: str, *, category: str = "streamer_creator") -> MomentQuery:
    """``creator + moment type`` — e.g. 'IShowSpeed + wild fan moment'."""
    c, m = creator.strip(), moment.strip()
    if not c or not m:
        raise ValueError("creator and moment are both required")
    return MomentQuery(text=f"{c} + {m}", category=category, creator=c)


def assert_no_banned_defaults(queries: list[MomentQuery]) -> None:
    """Guard used by tests and callers: a production seed set must not contain
    the over-broad generic phrases this library exists to remove."""
    bad = [q.text for q in queries if is_banned_seed(q.text)]
    if bad:
        raise ValueError(f"banned generic seeds present: {bad}")
