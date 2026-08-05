"""
qonclave.storage — the capability of persisting records.

Same structure as qonclave.inference, for the same reason: the capability lives in a shared layer
so a hub can persist without importing qonclave.archive, which is what makes the Archive role
optional rather than merely described as optional.
"""
