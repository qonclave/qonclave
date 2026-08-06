"""
tenancy.py -- tenant scoping and cross-tenant denial.

Checked on every inbound document before an application Policy sees it, and consulted by
placement/ladder.py when enforcing privacy denials.

Spec: SECURITY.md section 2
"""

from __future__ import annotations
