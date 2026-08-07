"""
mqtt.py -- MQTT pub/sub transport.

The push path for devices that can hold a subscription. Devices that cannot -- anything on the
`minimal` profile -- never appear here; their commands accumulate in the hub mailbox instead.

Best-effort by design: an unreachable broker must not fail an event that was otherwise handled.

Deliberately left as a placeholder rather than a `paho`-backed implementation: CONVENTIONS.md is
explicit that `transport/` holds the `PubSubTransport` ABC and scheme registry only -- the client
library ("somebody else's library for reaching somebody else's process") is the developer's
choice. `hub/framework/mqtt_bus.py` is the reference implementation, built directly against paho,
and lives there rather than here for exactly that reason. An earlier pass (2026-08-06) put a
`MQTTTransport` class wrapping paho here; that was the same mistake this file's own conventions
doc says the project already made twice before, and has been reverted -- see CONVENTIONS.md.

Spec: spec/v1/asyncapi/commands.yaml
Origin: hub/framework/mqtt_bus.py
"""

from __future__ import annotations
