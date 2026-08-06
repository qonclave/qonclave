"""
qonclave.discovery — finding peers and knowing whether they are alive.

Feeds placement: a peer whose heartbeat has lapsed stops being a candidate tier.

Nodes on the `minimal` profile never participate. An mDNS browse costs more radio time than such
a device's entire useful exchange, so its hub endpoint is fixed at commissioning instead
(spec/v1/profiles/minimal.md).
"""
