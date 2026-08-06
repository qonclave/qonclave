"""
health.py -- heartbeats, liveness, and dead-peer eviction.

Feeds placement: a peer whose heartbeat has lapsed stops being a candidate tier, which is how a
placement decision survives a node dying mid-deployment.

Spec: ARCHITECTURE.md section 3
"""

from __future__ import annotations
