"""
audit.py -- append-only access ledger.

Every read of an archived record is logged and signed. Required for the compliance claims in
SECURITY.md section 4.
"""

from __future__ import annotations
