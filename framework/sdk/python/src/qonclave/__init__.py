"""
Qonclave — privacy-first, autonomous distributed intelligence.

This package is a *binding* of the wire specification in framework/spec/v1/, not the definition
of it. Where the two disagree, the spec wins and this is the bug. That inversion exists because
Qonclave has to run on a Snapdragon laptop and on a coin-cell sensor that will never execute
Python, so the framework cannot be a library — it has to be a contract several implementations
satisfy independently.

Layering (enforced by tests/test_layering.py):

    core                            imports nothing from qonclave
    transport, security             import core only
    discovery                       peer manifests, liveness, load
    placement                       decides WHICH tier runs a task
    inference, storage              ask placement, then execute
    edge | hub | compute | archive  import layers below — never each other
    app, cli

Roles never import siblings. That is what keeps `pip install qonclave[edge]` free of Flask, and
what makes the Compute and Archive roles genuinely optional rather than nominally so.
"""

__version__ = "0.1.0"

from .core import SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION", "__version__"]
