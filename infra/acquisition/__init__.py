"""Acquisition Boundary V1 — deterministic contracts (no download).

Submodules:
  * platform_capabilities — acquisition-readiness states + capability matrix
  * acquisition           — durable idempotent acquisition-job contract
  * reaction_library      — tenant-neutral Personal Reaction Library contract

Nothing in this package downloads media. The Acquisition Boundary records
authorization and produces a job status; actual download is a separate,
explicitly-authorized step gated on a runtime-proven platform adapter.
"""
