"""
qonclave.discovery — finding peers and knowing whether they are alive.

Two consumers, one mechanism (a node announcing itself):
  * placement (peers.py, health.py) — which nodes are federation-authorized HUB-tier candidates,
    and whether their heartbeat is current enough to still be one.
  * observability (registry.py) — what this deployment has ever heard from, for an operator-facing
    view (a network page, `qonclave doctor`), independent of placement.

Nodes on the `minimal` profile never participate. An mDNS browse costs more radio time than such
a device's entire useful exchange, so its hub endpoint is fixed at commissioning instead
(spec/v1/profiles/minimal.md).
"""

from .registry import clear, probe_targets, record, record_mqtt_topic, record_rtt, snapshot

__all__ = [
    "record", "record_mqtt_topic", "snapshot", "clear", "probe_targets", "record_rtt",
]
