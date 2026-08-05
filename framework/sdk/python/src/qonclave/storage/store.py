"""
store.py -- the RecordStore contract.

Same structure as inference/backend.py and for the same reason: the capability lives in a shared
layer so a hub can persist locally without importing qonclave.archive, which is what makes the
Archive role optional.
"""

from __future__ import annotations
