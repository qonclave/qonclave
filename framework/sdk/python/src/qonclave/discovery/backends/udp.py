"""
udp.py -- UDP broadcast discovery.

Origin: hub/framework/discovery.py, which broadcasts a JSON heartbeat on port 8888 and answers
probes. Kept as a backend because it works on networks where multicast is filtered.
"""

from __future__ import annotations
