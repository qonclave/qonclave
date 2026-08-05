"""
server.py -- serve the inference contract over the network.

OPTIONAL. A deployment with no compute node is fully supported: inference/resolve.py falls back
to a local backend and nothing above it notices.

Spec: spec/v1/proto/compute.proto
"""

from __future__ import annotations
