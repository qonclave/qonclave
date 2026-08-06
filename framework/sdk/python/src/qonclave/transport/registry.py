"""
registry.py -- resolve a URI scheme to a Transport implementation.

Lets an endpoint string ("mqtt://10.0.0.4:1883") select a data link without the caller importing
the implementation. This is how "transport agnostic" stays true above this layer.
"""

from __future__ import annotations
