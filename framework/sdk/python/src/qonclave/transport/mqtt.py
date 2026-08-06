"""
mqtt.py -- MQTT pub/sub transport.

The push path for devices that can hold a subscription. Devices that cannot -- anything on the
`minimal` profile -- never appear here; their commands accumulate in the hub mailbox instead.

Best-effort by design: an unreachable broker must not fail an event that was otherwise handled.

Spec: spec/v1/asyncapi/commands.yaml
Origin: hub/framework/mqtt_bus.py
"""

from __future__ import annotations
