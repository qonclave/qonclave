"""
server.py — Qonclave hub entrypoint.

Wires the security app's Policy into the generic framework HTTP server and
runs it. Route groups, transport, event store, and VLM plumbing all live in
framework/; only the app choice (SecurityPolicy) and its static/ dir are
specific to this deployment.

Endpoints: see hub/framework/server.py create_app().

Design goals:
    * Runs on ANY laptop (regular x86 Windows/Linux included). The reasoning
      part is conditional — see hub/framework/vlm.py — so only that piece is
      Snapdragon-only. Everything else runs anywhere.
    * Everything is logged to the terminal where the server runs.

Run:
    pip install -r hub/requirements.txt
    python hub/server.py                 # http://0.0.0.0:8000
    # options via env: QONCLAVE_HOST, QONCLAVE_PORT, QONCLAVE_WARMUP=1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# Load .env from the repo root before any framework imports read os.environ.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

# Make "import framework" / "import apps" work regardless of CWD.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- logging: everything to the terminal (configure before importing framework) -
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("qonclave.hub")

from framework.server import create_app  # noqa: E402
from framework.vlm import VLMBackend  # noqa: E402
from framework.mqtt_bus import MQTTBus  # noqa: E402
from framework.sms_bus import SMSBus  # noqa: E402
from apps.security.policy import SecurityPolicy  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "apps", "security", "static")

HOST = os.environ.get("QONCLAVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("QONCLAVE_PORT", "8000"))
MQTT_HOST = os.environ.get("QONCLAVE_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("QONCLAVE_MQTT_PORT", "1883"))
MQTT_ENABLED = os.environ.get("QONCLAVE_MQTT_ENABLED", "1") == "1"

vlm = VLMBackend()
mqtt = MQTTBus(host=MQTT_HOST, port=MQTT_PORT, enabled=MQTT_ENABLED)
sms = SMSBus()
policy = SecurityPolicy(vlm)
app = create_app(policy=policy, vlm=vlm, mqtt=mqtt, sms=sms, static_dir=STATIC_DIR)


def main():
    parser = argparse.ArgumentParser(description="Qonclave hub server")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="show per-request access logs (werkzeug). Off by "
                             "default so the dashboard's 2s polling doesn't flood "
                             "the console; our own event/alert logs always show.")
    parser.add_argument("--host", default=HOST, help=f"bind address (default {HOST})")
    parser.add_argument("--port", type=int, default=PORT, help=f"port (default {PORT})")
    args = parser.parse_args()

    # Quiet werkzeug's access log unless --verbose. Our qonclave.hub logs
    # (events, alerts, warnings) stay at INFO either way; --verbose also turns
    # on our own debug lines (e.g. per-request /health).
    logging.getLogger("werkzeug").setLevel(logging.INFO if args.verbose else logging.WARNING)
    if args.verbose:
        logging.getLogger("qonclave").setLevel(logging.DEBUG)

    log.info("=" * 60)
    log.info("Qonclave hub starting on http://%s:%s", args.host, args.port)
    log.info("App        : %s", policy.name)
    log.info("Static dir : %s", STATIC_DIR)
    log.info("VLM status : %s", vlm.status())
    log.info("MQTT status: %s", mqtt.status())
    log.info("SMS status : %s", sms.status())
    if os.environ.get("QONCLAVE_WARMUP") == "1":
        log.info("QONCLAVE_WARMUP=1 -> loading VLM model now...")
        vlm.warmup()
        log.info("VLM status after warmup: %s", vlm.status())
    log.info("Edge  : POST /edge/event")
    log.info("User  : GET /user/dashboard | GET /user/events | GET /user/latest.jpg")
    log.info("        GET /user/frames/<name> | POST /user/reason | GET /user/")
    log.info("Other : GET /health | GET / (-> /user/)")
    log.info("Access logs: %s (use --verbose to show per-request logs)",
             "ON" if args.verbose else "OFF")
    log.info("=" * 60)
    # threaded=True so /health stays responsive; generation is serialized
    # inside the VLM backend via its own lock.
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
