"""
triage.py -- the Tier 1 heuristic contract.

ARCHITECTURE.md's "filter out 99% of the noise so the network isn't flooded". A TriageStage
returns a confidence; whether that escalates is a placement decision, not a hardcoded threshold.
"""

from __future__ import annotations
