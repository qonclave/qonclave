"""
manifest.py -- building this node's own NodeManifest.

Assembles identity, capabilities, load, and power into the document broadcast by discovery.
Nodes on the `minimal` profile never call this -- they do not advertise.

Spec: spec/v1/json-schema/node-manifest.schema.json (COMMUNICATION.md section 1)
"""

from __future__ import annotations
