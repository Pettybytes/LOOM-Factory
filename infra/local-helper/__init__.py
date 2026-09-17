"""Local acquisition helper — browser-assisted Instagram video acquisition.

The LOOM Chrome extension (human-proven browser-assisted resolution) sends a
captured media candidate to this localhost-only helper, which validates,
downloads exactly one MP4, validates the asset, and records the Acquisition Job
+ Personal Reaction Library ledger.

Nothing in this package is invoked without an explicit human "Acquire for
Reaction" click. The helper never logs, persists, or returns the full signed CDN
URL.
"""
