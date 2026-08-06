"""
at_rest.py -- AES-256 encryption at rest and cryptographic erasure.

Erasure destroys the key rather than the objects, so every record sharing a key_id becomes
unreadable at once -- which is what makes "right to be forgotten" tractable across a data lake.

Spec: SECURITY.md section 4
"""

from __future__ import annotations
