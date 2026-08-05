"""
remote.py -- a ModelBackend that forwards to another tier.

Implements the same interface as a local backend, so nothing above inference/ can tell whether a
compute node is present. That indistinguishability is what makes the Compute role optional.
"""

from __future__ import annotations
