"""
qonclave.inference — the capability of running a model.

Deliberately NOT inside qonclave.compute. A hub is allowed to run models itself and Compute is an
optional role; if the ModelBackend contract lived in the compute package, every hub doing local
work would have to import the optional server it is supposed to be able to do without.

    qonclave.inference   the capability      (local backends AND the remote client)
    qonclave.compute     an optional server  (exposes a backend over the network)
"""

from .backend import InferResult, ModelBackend
from .resolve import local_only, resolve

__all__ = ["ModelBackend", "InferResult", "resolve", "local_only"]
