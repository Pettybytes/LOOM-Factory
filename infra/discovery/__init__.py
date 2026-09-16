"""LOOM — Automated Source Discovery V1.

A tenant-driven, deterministic foundation that turns a *Source Brief* (what a
tenant wants to find) into a *normalized discovery record* (an eligible,
traceable source) — with no automatic download, processing, scoring, or
publication.

Pipeline (each stage is a separate, testable module):

    Source Brief  ──►  Connector  ──►  Normalized Record  ──►  Identity/Dedup
         │                                                      │
         └────────────── Qualification gate ◄───────────────────┘
                                 │
                        manual-ingest boundary (STOP — no download)

Scope boundaries enforced here (see LOOM_SOURCE_OF_TRUTH.md / blueprint):
  * No Topic Intelligence scoring — only reserved placeholder fields.
  * No paid model calls for deduplication (pure deterministic hashing).
  * No automatic media download, OpenShorts invocation, clipping, or publish.
  * A qualified source only ever advances to the *manual-ingest boundary*.

This package is pure stdlib (no third-party runtime dependency) so it stays
deterministic and portable.
"""

from . import source_brief, discovery_record, identity, connector, qualification, store  # noqa: F401

__version__ = "1.0.0"
# The Source Brief and discovery-record contracts are independently versioned;
# bump SCHEMA_VERSION below whenever their shape changes in a breaking way.
SCHEMA_VERSION = "1.0.0"

__all__ = [
    "source_brief",
    "discovery_record",
    "identity",
    "connector",
    "qualification",
    "store",
    "__version__",
    "SCHEMA_VERSION",
]
