"""
app.py -- create_app(): the hub's HTTP surface.

Wires a Policy into generic routes. Route groups, transport, event store, and placement all live
in the framework; only the Policy and its static assets vary per use case.

Spec: spec/v1/openapi/hub.yaml
Origin: hub/framework/server.py
"""

from __future__ import annotations
