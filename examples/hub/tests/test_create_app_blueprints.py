"""
test_create_app_blueprints.py — create_app()'s pluggable blueprint mechanism.

Replaces a hardcoded `from apps.assistant.routes import create_assistant_blueprint`
inside framework/server.py (backwards: framework reaching into a specific app) with
a generic `blueprints` parameter -- any number of app-built Blueprint objects,
registered with no knowledge of what they are. hub/server.py is the real-world
proof (two independent blueprints, assistant + security's sms_routes); this pins
the mechanism itself, including the no-blueprints and multiple-blueprints cases.
"""

from __future__ import annotations

import os
import sys

import pytest
from flask import Blueprint, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.policy import Policy, Verdict  # noqa: E402
from framework.server import create_app  # noqa: E402


class _StubPolicy(Policy):
    name = "stub"

    def evaluate(self, event, image_path=None):
        return Verdict(verified=False)


class _StubBackend:
    def status(self):
        return {"available": False}


def _make_app(tmp_path, **kw):
    static = tmp_path / "static"
    static.mkdir()
    (static / "dashboard.html").write_text("<html></html>", encoding="utf-8")
    app = create_app(
        policy=_StubPolicy(), vlm=_StubBackend(), mqtt=_StubBackend(),
        sms=_StubBackend(), static_dir=str(static), **kw,
    )
    app.config["TESTING"] = True
    return app


def test_no_blueprints_is_the_default_and_works_fine(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()

    resp = client.get("/health")

    assert resp.status_code == 200
    assert app.blueprints == {}


def test_one_blueprint_is_registered_and_reachable(tmp_path):
    bp = Blueprint("thing_a", __name__)

    @bp.get("/thing-a")
    def thing_a():
        return jsonify({"ok": True})

    app = _make_app(tmp_path, blueprints=[bp])
    client = app.test_client()

    resp = client.get("/thing-a")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert "thing_a" in app.blueprints


def test_multiple_independent_blueprints_all_register(tmp_path):
    bp_a = Blueprint("thing_a", __name__)
    bp_b = Blueprint("thing_b", __name__)

    @bp_a.get("/thing-a")
    def thing_a():
        return jsonify({"which": "a"})

    @bp_b.get("/thing-b")
    def thing_b():
        return jsonify({"which": "b"})

    app = _make_app(tmp_path, blueprints=[bp_a, bp_b])
    client = app.test_client()

    assert client.get("/thing-a").get_json() == {"which": "a"}
    assert client.get("/thing-b").get_json() == {"which": "b"}
    assert set(app.blueprints) == {"thing_a", "thing_b"}


def test_framework_server_does_not_import_from_apps():
    """The bug this phase fixes: framework/ must never reach into a specific
    app to build a blueprint. A regression here would reintroduce exactly
    the backwards dependency CONVENTIONS.md flagged."""
    import framework.server as server_module

    src = open(server_module.__file__, encoding="utf-8").read()
    assert "from apps." not in src
    assert "import apps." not in src
