"""
base.py — the transport abstraction that makes "transport agnostic" more than a claim.

COMMUNICATION.md defines the schemas as data-link independent: IP, BLE GATT, LoRaWAN, Zigbee, or a
custom serial radio. That only holds if nothing above this layer knows which one is in use.

Two shapes, because the difference is not cosmetic:

* `Transport` — request/response. HTTP, CoAP, gRPC. The caller blocks for an answer.
* `PubSubTransport` — fire and forget with subscription. MQTT. Delivery is asynchronous and may
  never happen.

A duty-cycled device uses exactly one `Transport.request()` per wake and never subscribes at all,
which is why subscription lives in a separate protocol rather than as optional methods returning
NotImplemented on half the implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass(slots=True)
class Response:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class Transport(ABC):
    """A request/response data link."""

    scheme: str = ""

    @abstractmethod
    def request(
        self,
        endpoint: str,
        body: bytes,
        *,
        content_type: str = "application/json",
        timeout_s: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Send `body` and wait for a reply.

        Implementations MUST honor `timeout_s`. A placement deadline is meaningless if the
        transport below it can block indefinitely — the ladder will have already promised the
        caller a bound it cannot keep.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release resources. Safe to call more than once."""


class PubSubTransport(ABC):
    """A publish/subscribe data link."""

    scheme: str = ""

    @abstractmethod
    def publish(self, topic: str, body: bytes, *, qos: int = 1, retain: bool = False) -> bool:
        """Publish. Returns False on failure rather than raising.

        Best-effort by design: an unreachable broker must not fail an event that was otherwise
        handled. The hub's HTTP response is the authoritative path; MQTT is the second one.
        """
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, topic: str, handler: Callable[[str, bytes], None]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """Release resources. Safe to call more than once."""
