#!/usr/bin/env python3
"""Run a small anonymous MQTT broker for local Qonclave testing."""

from __future__ import annotations

import argparse
import asyncio
import logging

logger = logging.getLogger("qonclave.mqtt_broker")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Address to listen on (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1883,
        help="TCP port to listen on (default: 1883)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity: connect/subscribe/publish (-v) "
        "or also raw MQTT packets and payload bytes (-vv).",
    )
    return parser.parse_args()


def _register_traffic_logger(broker: "Broker") -> None:  # noqa: F821
    """Log client connections, subscriptions, and published messages as they happen."""
    from amqtt.events import BrokerEvents

    def on_connected(client_id: str, **_: object) -> None:
        logger.info("CONNECT   client=%s", client_id)

    def on_disconnected(client_id: str, **_: object) -> None:
        logger.info("DISCONNECT client=%s", client_id)

    def on_subscribed(client_id: str, topic: str, qos: object = "?", **_: object) -> None:
        logger.info("SUBSCRIBE client=%s topic=%s qos=%s", client_id, topic, qos)

    def on_unsubscribed(client_id: str, topic: str, **_: object) -> None:
        logger.info("UNSUBSCRIBE client=%s topic=%s", client_id, topic)

    def _describe_message(message: object) -> tuple[str, object, object]:
        data = getattr(message, "data", b"")
        preview = data[:200] if isinstance(data, (bytes, bytearray)) else data
        return getattr(message, "topic", "?"), getattr(message, "qos", "?"), preview

    def on_message_received(client_id: str, message: object, **_: object) -> None:
        topic, qos, preview = _describe_message(message)
        logger.info("PUBLISH IN  client=%s topic=%s qos=%s payload=%r", client_id, topic, qos, preview)

    def on_message_broadcast(client_id: str, message: object, **_: object) -> None:
        topic, qos, preview = _describe_message(message)
        logger.info("PUBLISH OUT from=%s topic=%s qos=%s payload=%r", client_id, topic, qos, preview)

    handlers = {
        BrokerEvents.CLIENT_CONNECTED: on_connected,
        BrokerEvents.CLIENT_DISCONNECTED: on_disconnected,
        BrokerEvents.CLIENT_SUBSCRIBED: on_subscribed,
        BrokerEvents.CLIENT_UNSUBSCRIBED: on_unsubscribed,
        BrokerEvents.MESSAGE_RECEIVED: on_message_received,
        BrokerEvents.MESSAGE_BROADCAST: on_message_broadcast,
    }

    async def dispatch(event_name: str, *args: object, **kwargs: object) -> None:
        handler = handlers.get(event_name)
        if handler is not None:
            handler(**kwargs)

    original_fire_event = broker.plugins_manager.fire_event

    async def patched_fire_event(event_name: str, *args: object, **kwargs: object) -> None:
        await dispatch(event_name, *args, **kwargs)
        await original_fire_event(event_name, *args, **kwargs)

    broker.plugins_manager.fire_event = patched_fire_event  # type: ignore[method-assign]


async def run_broker(host: str, port: int, verbosity: int) -> None:
    try:
        from amqtt.broker import Broker
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency: install it with `python3 -m pip install amqtt`."
        ) from exc

    config = {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"{host}:{port}",
            }
        },
        "sys_interval": 0,
        "auth": {
            "allow-anonymous": True,
        },
    }

    if verbosity >= 2:
        # Also trace raw MQTT packets (CONNECT/PUBLISH/SUBACK/...) in and out of the broker.
        logging.getLogger("amqtt").setLevel(logging.DEBUG)
        config["plugins"] = {
            "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow-anonymous": True},
            "amqtt.plugins.logging_amqtt.PacketLoggerPlugin": {},
        }

    broker = Broker(config)
    if verbosity >= 1:
        _register_traffic_logger(broker)

    await broker.start()
    print(f"Qonclave MQTT broker listening on mqtt://{host}:{port}")
    print("Anonymous connections are enabled. Press Ctrl+C to stop.")
    if verbosity == 0:
        print("Tip: pass -v to log connects/subscribes/publishes, -vv to also log raw packets.")

    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if args.verbose >= 2:
        # Scoped to the amqtt namespace so raw-packet tracing doesn't drag in
        # unrelated DEBUG spam from asyncio/transitions/passlib.
        logging.getLogger("amqtt").setLevel(logging.DEBUG)
    try:
        asyncio.run(run_broker(args.host, args.port, args.verbose))
    except KeyboardInterrupt:
        print("\nBroker stopped.")


if __name__ == "__main__":
    main()
