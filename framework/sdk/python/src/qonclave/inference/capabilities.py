"""
capabilities.py -- hardware introspection.

Reports what this node can actually run, for the manifest and for GET /capabilities. Answers the
gap in DEVELOPER_GUIDE.md's roadmap: discovering supported models without hardcoded assumptions.
"""

from __future__ import annotations
