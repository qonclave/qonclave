"""
errors.py -- the framework's exception hierarchy.

Every error a caller might reasonably catch derives from QonclaveError, so an application can
wrap a whole interaction without enumerating failure modes it does not care about.
"""

from __future__ import annotations
