"""
qonclave.archive — OPTIONAL long-term storage server.

Single-tenant by requirement: archive nodes are never shared (SECURITY.md §2). A deployment
without one persists through qonclave.storage to local disk instead.
"""
