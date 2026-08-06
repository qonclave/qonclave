"""
spool.py -- persistent store-and-forward.

MUST survive a power cycle. On a duty-cycled device the retry may be a day later, so a spool that
lives in RAM is lost to the sleep cycle and is therefore not a spool.

Spec: spec/v1/profiles/minimal.md
"""

from __future__ import annotations
