"""
router.py -- choose a compute node by capability and load.

Distinct from placement: placement decides WHICH TIER, this picks WHICH NODE within the compute
tier once that decision is made.
"""

from __future__ import annotations
