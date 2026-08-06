"""
broker.py -- issue and verify capability grants.

Mints grants for all three audiences (operator, compute, peer hub) and verifies grants presented
by foreign edges. Also the WebRTC signalling endpoint for Direct-Bind.

Spec: SECURITY.md section 3, spec/v1/json-schema/capability-grant.schema.json
"""

from __future__ import annotations
