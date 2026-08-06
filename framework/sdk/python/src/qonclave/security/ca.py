"""
ca.py -- the hub acting as local Certificate Authority.

Issues node certificates during commissioning and publishes the root that peer hubs pin during
federation. That pinned root is what makes offline grant verification possible.

Spec: SECURITY.md section 3
"""

from __future__ import annotations
