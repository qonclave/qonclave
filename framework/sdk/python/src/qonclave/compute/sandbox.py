"""
sandbox.py -- the statelessness guarantee.

Flushes model context, prompts, and intermediate tensors between calls, including across tenants.
SECURITY.md section 2 states this as a guarantee, so it is enforced here rather than left to each
backend to remember.
"""

from __future__ import annotations
