"""
qonclave.edge — the sensing role.

Deliberately dependency-light: this package must be importable on a device that installed
`qonclave[edge]` and therefore has no web framework, no broker client, and no model runtime.

Devices that are awake use `agent`; devices that wake briefly and sleep use `checkin`, which
completes their entire network interaction in one round trip.
"""
