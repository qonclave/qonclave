"""
End-to-end test for the edge MQTT client.

  1. spins up an in-process amqtt broker on 127.0.0.1:1884
  2. starts the real EdgeMQTTClient (from python/mqtt_client.py)
  3. publishes a command to qonclave/<device_id>/command (simulating the hub,
     using paho exactly like framework/mqtt_bus.py does)
  4. asserts the edge client received it and invoked its on_command callback
"""
import asyncio
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from mqtt_client import EdgeMQTTClient, command_topic  # noqa: E402

BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1884
DEVICE_ID = "unoq-test"

received = []


def _run_broker(loop):
    from amqtt.broker import Broker
    asyncio.set_event_loop(loop)
    config = {
        "listeners": {
            "default": {"type": "tcp", "bind": f"{BROKER_HOST}:{BROKER_PORT}"}
        },
        "sys_interval": 0,
        "auth": {"allow-anonymous": True},
    }

    async def _start():
        broker = Broker(config)
        await broker.start()
        return broker

    loop.run_until_complete(_start())
    loop.run_forever()


def main():
    # --- 1. broker in a background thread ---
    loop = asyncio.new_event_loop()
    threading.Thread(target=_run_broker, args=(loop,), daemon=True).start()
    time.sleep(2.0)  # let the broker bind
    print("[test] broker up on", f"{BROKER_HOST}:{BROKER_PORT}")

    # --- 2. real edge client ---
    class _Log:
        def info(self, m): print("[edge]", m)
        def warning(self, m): print("[edge][WARN]", m)
        def error(self, m): print("[edge][ERR]", m)

    client = EdgeMQTTClient(
        device_id=DEVICE_ID, host=BROKER_HOST, port=BROKER_PORT,
        enabled=True, on_command=lambda c: received.append(c), logger=_Log(),
    )
    client.start()

    # wait for the edge client to connect + subscribe
    for _ in range(50):
        if client.is_connected():
            break
        time.sleep(0.1)
    assert client.is_connected(), f"edge client failed to connect: {client.status()}"
    print("[test] edge client connected & subscribed")
    time.sleep(0.3)  # ensure SUBSCRIBE is acked before we publish

    # --- 3. publish a command the way the hub (framework/mqtt_bus.py) does ---
    import paho.mqtt.client as mqtt
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="qonclave-hub-sim")
    pub.connect(BROKER_HOST, BROKER_PORT, keepalive=30)
    pub.loop_start()
    time.sleep(0.3)
    topic = command_topic(DEVICE_ID)
    payload = {"command": "forward", "speed": 200}
    info = pub.publish(topic, json.dumps(payload), qos=1)
    info.wait_for_publish(timeout=3)
    print(f"[test] hub published to {topic}: {payload}")

    # --- 4. verify delivery ---
    for _ in range(50):
        if received:
            break
        time.sleep(0.1)

    pub.loop_stop(); pub.disconnect(); client.close()

    assert received, "edge client did NOT receive the command"
    got = received[0]
    assert got == payload, f"payload mismatch: {got} != {payload}"
    print(f"[test] edge client received command: {got}")
    print("\n✅ PASS: edge listens on qonclave/<device_id>/command and dispatches it")


if __name__ == "__main__":
    main()
