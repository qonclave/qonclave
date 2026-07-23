"""
server.py — Qonclave hub HTTP server (app assembly).

Route groups live in separate blueprints:
    edge_routes.py  ->  /edge/*   device-facing (Arduino UNO Q -> hub)
    user_routes.py  ->  /user/*   human-facing (browser / operator)

This file wires them together and keeps the top-level /health probe.

Endpoints:
    GET  /health              liveness + VLM availability
    GET  /                    redirects to /user/ (upload test page)

    POST /edge/event          edge event JSON + frame -> verification response

    GET  /user/dashboard      live dashboard page
    GET  /user/events         recent events + results (JSON)
    GET  /user/latest.jpg     most recent frame
    GET  /user/frames/<name>  a specific stored frame
    POST /user/reason         raw VLM tester
    GET  /user/               upload test page

Design goals:
    * Runs on ANY laptop (regular x86 Windows/Linux included). The reasoning
      part is conditional — see hub/vlm_backend.py — so only that piece is
      Snapdragon-only. Everything else runs anywhere.
    * Arduino UNO Q friendly: /edge/event accepts BOTH multipart form uploads
      and raw image bodies.
    * Everything is logged to the terminal where the server runs.

Run:
    pip install -r hub/requirements.txt
    python hub/server.py                 # http://0.0.0.0:8000
    # options via env: QONCLAVE_HOST, QONCLAVE_PORT, QONCLAVE_WARMUP=1
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, redirect, request

# --- logging: everything to the terminal (configure before importing state) -
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("qonclave.hub")

import state  # noqa: E402  (shared config, VLM, event store)
from edge_routes import edge_bp  # noqa: E402
from user_routes import user_bp  # noqa: E402


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = state.MAX_UPLOAD_MB * 1024 * 1024

    app.register_blueprint(edge_bp)
    app.register_blueprint(user_bp)

    @app.get("/health")
    def health():
        log.info("GET /health from %s", request.remote_addr)
        return jsonify({
            "status": "ok",
            "service": "qonclave-hub",
            "time": state.now_iso(),
            "vlm": state.vlm.status(),
        })

    @app.get("/")
    def root():
        return redirect("/user/", code=302)

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify({"ok": False,
                        "error": f"upload exceeds {state.MAX_UPLOAD_MB} MB limit"}), 413

    return app


app = create_app()


def main():
    log.info("=" * 60)
    log.info("Qonclave hub starting on http://%s:%s", state.HOST, state.PORT)
    log.info("Static dir : %s", state.STATIC_DIR)
    log.info("Upload dir : %s", state.UPLOAD_DIR)
    log.info("VLM status : %s", state.vlm.status())
    if os.environ.get("QONCLAVE_WARMUP") == "1":
        log.info("QONCLAVE_WARMUP=1 -> loading VLM model now...")
        state.vlm.warmup()
        log.info("VLM status after warmup: %s", state.vlm.status())
    log.info("Edge  : POST /edge/event")
    log.info("User  : GET /user/dashboard | GET /user/events | GET /user/latest.jpg")
    log.info("        GET /user/frames/<name> | POST /user/reason | GET /user/")
    log.info("Other : GET /health | GET / (-> /user/)")
    log.info("=" * 60)
    # threaded=True so /health stays responsive; generation is serialized
    # inside the VLM backend via its own lock.
    app.run(host=state.HOST, port=state.PORT, threaded=True)


if __name__ == "__main__":
    main()
