"""Evidence-level contract for Content Intelligence Preflight.

Content understanding must be backed by ACTUAL inspected evidence. Each
evidence level defines (a) which content dimensions may be scored and (b) the
maximum content-confidence ceiling. A score must never claim evidence that was
not inspected — e.g. metadata alone cannot produce a "visual interest" or
"vocal intensity" score, and a transcript cannot produce visual/audio claims.

Levels:
  0 metadata_only   — title, description/excerpt, platform, creator, pub time,
                      market metrics.
  1 text_content    — transcript/captions, Reddit body, linked article text.
  2 static_visuals  — thumbnail, permitted preview/storyboard/keyframes.
  3 bounded_media   — short, precisely targeted audio/video windows (temporary,
                      strict limits, deleted after inspection).
  4 full_content    — complete media inspected. NOT authorized.

This module is pure and deterministic (no model, no I/O).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------
LEVEL_METADATA = 0
LEVEL_TEXT = 1
LEVEL_STATIC_VISUAL = 2
LEVEL_BOUNDED_MEDIA = 3
LEVEL_FULL = 4  # NOT authorized

LEVEL_NAMES: dict[int, str] = {
    0: "metadata_only",
    1: "text_content",
    2: "static_visuals",
    3: "bounded_media",
    4: "full_content",
}

# The highest evidence level authorized during Content Preflight V1.
MAX_AUTHORIZED_LEVEL = LEVEL_BOUNDED_MEDIA

# Content-confidence ceiling per evidence level. A score produced from
# metadata-only evidence can never carry high confidence, regardless of how
# high the numeric score is. Level 4 (full) is reserved/unauthorized.
CONFIDENCE_CEILING: dict[int, float] = {
    0: 0.35,
    1: 0.60,
    2: 0.70,
    3: 0.95,
    4: 1.00,
}


def level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, f"unknown({level})")


def confidence_ceiling(level: int) -> float:
    """Max content-confidence allowed at a given evidence level."""
    return CONFIDENCE_CEILING.get(level, 0.0)


def is_authorized_level(level: int) -> bool:
    return 0 <= level <= MAX_AUTHORIZED_LEVEL


# ---------------------------------------------------------------------------
# Content dimensions + minimum evidence level required to score each.
# ---------------------------------------------------------------------------
# A dimension is ONLY allowed to be scored (non-None) when the assessment's
# evidence level is >= its minimum level. This is the core anti-fabrication
# guard: it is impossible to emit, say, a "vocal_intensity" score from a
# transcript (level 1), or a "visual_interest" score from metadata (level 0).
DIMENSION_MIN_LEVEL: dict[str, int] = {
    # Semantic text dimensions (need transcript/body — level 1).
    "hook": LEVEL_TEXT,
    "emotion": LEVEL_TEXT,
    "conflict_tension": LEVEL_TEXT,
    "surprise": LEVEL_TEXT,
    "humor": LEVEL_TEXT,
    "relatability": LEVEL_TEXT,
    "stakes": LEVEL_TEXT,
    "story_progression": LEVEL_TEXT,
    "payoff": LEVEL_TEXT,
    "narrative_completeness": LEVEL_TEXT,
    "context_independence": LEVEL_TEXT,
    # Deterministic / structural / text-only dimensions (metadata is enough).
    "originality_novelty": LEVEL_METADATA,
    "reaction_potential": LEVEL_METADATA,
    "commentary_value": LEVEL_METADATA,
    "tenant_fit": LEVEL_METADATA,
    "format_fit": LEVEL_METADATA,
    "saturation_differentiation": LEVEL_METADATA,
    # Visual (needs at least a static image).
    "visual_interest": LEVEL_STATIC_VISUAL,
    # Audio (needs at least bounded audio/media).
    "vocal_intensity": LEVEL_BOUNDED_MEDIA,
}

# Canonical dimension keys (the full set the schema may carry).
KNOWN_DIMENSIONS: frozenset[str] = frozenset(DIMENSION_MIN_LEVEL.keys())


def allowed_dimensions(level: int) -> frozenset[str]:
    """Dimensions that MAY be scored (non-None) at the given evidence level."""
    return frozenset(
        d for d, min_lv in DIMENSION_MIN_LEVEL.items() if level >= min_lv
    )


def required_min_level(dimension: str) -> int:
    """Minimum evidence level required to score ``dimension``."""
    if dimension not in DIMENSION_MIN_LEVEL:
        raise KeyError(f"unknown dimension: {dimension}")
    return DIMENSION_MIN_LEVEL[dimension]


def dimension_supported(dimension: str, level: int) -> bool:
    """True if ``dimension`` may be scored at ``level`` (without fabricating)."""
    return dimension in DIMENSION_MIN_LEVEL and level >= DIMENSION_MIN_LEVEL[dimension]


def evidence_ceiling_description(level: int) -> str:
    """Human-readable description of what a level can/cannot support."""
    desc = {
        0: "metadata only — no semantic content, visual, or audio claims",
        1: "text content — semantic text dimensions only; no visual/audio claims",
        2: "static visuals — adds visual-interest; still no audio/vocal claims",
        3: "bounded media — adds vocal-intensity + confirms visual/audio payoff",
        4: "full content — NOT authorized",
    }
    return desc.get(level, "unknown")
