"""
backpressure.py -- retry, backoff, and congestion signalling.

ARCHITECTURE.md section 3 requires a hub to be able to tell edge devices to slow down when the
network degrades. For duty-cycled devices that signal is `next_checkin_s` in the check-in
response rather than a live control message.
"""

from __future__ import annotations
