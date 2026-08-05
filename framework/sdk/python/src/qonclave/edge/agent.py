"""
agent.py -- the sense/triage/escalate loop.

The always-on shape: a node with mains power and a persistent link, which is the only shape this
binding targets. A duty-cycled device does not have a loop, it has a wake — that is the check-in
exchange, and it belongs to the `constrained` and `minimal` profiles, so it is implemented in
sdk/c/ (`checkin.c`) rather than here.

Not implemented. See ../../../README.md for what is and is not built in this binding.
"""

from __future__ import annotations
