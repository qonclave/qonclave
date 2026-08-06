"""
logging.py -- logger setup.

All framework loggers live under "qonclave.<layer>", so an application can silence the framework
without silencing itself, and a constrained deployment can drop everything below WARNING.

Origin: the logging config currently inline in hub/server.py.
"""

from __future__ import annotations
