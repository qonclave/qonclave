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
from framework.llm import LLMBackend  # noqa: E402
from framework.mqtt_bus import MQTTBus  # noqa: E402
from framework.face_id.identity import FaceIdentityBackend  # noqa: E402
from framework.pose.pose import PoseBackend  # noqa: E402
from apps.security.egress.twilio_sms import SMSBus  # noqa: E402
from apps.security.policy import SecurityPolicy  # noqa: E402
from apps.security.placement import SecurityPlacement  # noqa: E402
from apps.security.sms_routes import create_sms_blueprint  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(HERE, "apps", "security", "static")

HOST = os.environ.get("QONCLAVE_HOST", "0.0.0.0")
PORT = int(os.environ.get("QONCLAVE_PORT", "8000"))
MQTT_HOST = os.environ.get("QONCLAVE_MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.environ.get("QONCLAVE_MQTT_PORT", "1883"))
MQTT_ENABLED = os.environ.get("QONCLAVE_MQTT_ENABLED", "1") == "1"
# Set to 0 to make POST /assistant/query serve canned template replies instead
# of generating with the LLM. Only affects the assistant; the Policy's own LLM
# use (SMS reasoning) and /health reporting are untouched.
ASSISTANT_LLM_ENABLED = os.environ.get("ASSISTANT_LLM_ENABLED", "1") == "1"

vlm = VLMBackend()
llm = LLMBackend()
mqtt = MQTTBus(host=MQTT_HOST, port=MQTT_PORT, enabled=MQTT_ENABLED)
face_id = FaceIdentityBackend()
pose = PoseBackend()
sms = SMSBus()
policy = SecurityPolicy(vlm, face_id, sms, llm, mqtt=mqtt)
placement = SecurityPlacement()
app = create_app(policy=policy, vlm=vlm, mqtt=mqtt, sms=sms, face_id=face_id,
                 static_dir=STATIC_DIR, llm=llm, pose=pose, placement=placement,
                 assistant_llm=llm if ASSISTANT_LLM_ENABLED else None)
# Twilio-specific routes (POST /sms, GET /user/sms_activity) are app-owned --
# registered directly here rather than through create_app(), so framework/
# never imports anything from apps/ for this.
app.register_blueprint(create_sms_blueprint(policy=policy, mqtt=mqtt, sms=sms))


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
    log.info("LLM status : %s", llm.status())
    log.info("MQTT status: %s", mqtt.status())
    log.info("Face ID    : %s", face_id.status())
    log.info("Pose       : %s", pose.status())
    log.info("SMS status : %s", sms.status())
    log.info("Assistant  : %s", "LLM" if ASSISTANT_LLM_ENABLED else
             "template replies (ASSISTANT_LLM_ENABLED=0)")
    if os.environ.get("QONCLAVE_WARMUP") == "1":
        log.info("QONCLAVE_WARMUP=1 -> loading VLM + LLM + face ID + pose models now...")
        vlm.warmup()
        llm.warmup()
        face_id.warmup()
        pose.warmup()
        log.info("VLM status after warmup: %s", vlm.status())
        log.info("LLM status after warmup: %s", llm.status())
        log.info("Face ID status after warmup: %s", face_id.status())
        log.info("Pose status after warmup: %s", pose.status())
    elif ASSISTANT_LLM_ENABLED:
        # Load Qwen3-4B before serving: the first voice query would otherwise
        # pay the load time and blow past the edge's HUB_TIMEOUT_SEC.
        log.info("Assistant LLM enabled -> loading the LLM now...")
        llm.warmup()
        log.info("LLM status after warmup: %s", llm.status())
    log.info("Edge  : POST /api/v1/events (or /edge/event) | POST /track/analyze (per-track-id face ID + pose)")
    log.info("SMS   : POST /sms  (Twilio inbound-reply webhook)")
    log.info("Voice : POST /assistant/query  (edge assistant)")
    log.info("User  : GET /user/dashboard | GET /user/events | GET /user/latest.jpg")
    log.info("        GET /user/network | GET /user/devices")
    log.info("        GET /user/tracks | GET /user/tracks/<id>.jpg")
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
