"""
telemetry.py -- record chosen tier and actual latency.

Placement thresholds are guesses until measured. This records what was decided and what it
actually cost, so the guesses can be corrected against real deployments.
"""

from __future__ import annotations
