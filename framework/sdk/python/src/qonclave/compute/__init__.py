"""
qonclave.compute — OPTIONAL stateless inference server.

A deployment with no compute node is fully supported: inference.resolve() falls back to a local
backend and nothing above it notices. This package exists to expose a backend over the network,
not to define what a backend is — that contract lives in qonclave.inference.
"""
