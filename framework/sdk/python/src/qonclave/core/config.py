"""
config.py -- QONCLAVE_* environment resolution, in one place.

Scattered os.environ.get calls make it impossible to answer "what is configurable?" without
grepping. Everything the framework reads from the environment is declared here.

Origin: the QONCLAVE_* reads currently spread across hub/server.py and hub/framework/*.
"""

from __future__ import annotations
