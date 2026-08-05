"""
peers.py -- the peer registry, including grants held for each.

A peer hub the local node holds a valid capability grant for becomes an additional candidate at
the HUB tier. That is the entirety of what federation adds to placement: entries in a candidate
list, not a new rung on the ladder.
"""

from __future__ import annotations
