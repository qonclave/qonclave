"""
erasure.py -- cryptographic shredding.

Destroys the tenant data key rather than the objects, rendering associated records permanently
unreadable without relying on disk deletion that can leave forensic traces.

Spec: SECURITY.md section 4
"""

from __future__ import annotations
