"""
federation.py -- hub-to-hub trust.

Exchanges and pins peer CA roots so that a grant issued by Hub A can be verified by Hub B with no
callback to Hub A. Without this, cross-hub failover requires the failed hub to be reachable.

Spec: spec/v1/json-schema/capability-grant.schema.json
"""

from __future__ import annotations
