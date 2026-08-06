"""
budget.py -- deadline accounting across hops.

A task escalated with 200ms of budget after 140ms spent locally has 60ms left, and the receiving
tier cannot know that unless it is told. Without this the ladder blows an SLA nobody is tracking.
"""

from __future__ import annotations
