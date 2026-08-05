"""
election.py -- dynamic role promotion and demotion.

Any node may take up hub duties if no hub is present, and demotes itself when a better-suited one
appears. Nodes on the `constrained` and `minimal` profiles never participate.

Spec: ARCHITECTURE.md section 3
"""

from __future__ import annotations
