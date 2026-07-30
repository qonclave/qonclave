#!/usr/bin/env python3
"""Run a small anonymous MQTT broker for local Qonclave testing."""

from __future__ import annotations

import argparse
import asyncio


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
    return parser.parse_args()


async def run_broker(host: str, port: int) -> None:
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

    broker = Broker(config)
    await broker.start()
    print(f"Qonclave MQTT broker listening on mqtt://{host}:{port}")
    print("Anonymous connections are enabled. Press Ctrl+C to stop.")

    try:
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()


def main() -> None:
    args = parse_args()
    try:
        asyncio.run(run_broker(args.host, args.port))
    except KeyboardInterrupt:
        print("\nBroker stopped.")


if __name__ == "__main__":
    main()
