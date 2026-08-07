# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""
Tests for python/basic_auth.py -- run directly with `python
test_basic_auth.py`, or with pytest if it happens to be installed.
Follows this app's existing convention for standalone test scripts (see
test_edge_mqtt_e2e.py, test_person_tracker.py): plain assert-based
test_*() functions, no test framework added to requirements.txt.
"""

import asyncio
import base64
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "python"))
from basic_auth import BasicAuthMiddleware  # noqa: E402

USERNAME = "admin"
PASSWORD = "s3cr3t"


def _auth_header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return (b"authorization", f"Basic {token}".encode())


def _run(scope, headers=None):
    scope = {**scope, "headers": [headers] if headers else []}
    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def inner_app(scope, receive, send):
        await send({"type": "inner.called"})

    middleware = BasicAuthMiddleware(inner_app, username=USERNAME, password=PASSWORD)
    asyncio.run(middleware(scope, receive, send))
    return sent


def test_http_request_without_credentials_is_rejected():
    sent = _run({"type": "http"})
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    assert (b"www-authenticate", b'Basic realm="Qonclave Edge"') in sent[0]["headers"]
    assert not any(m["type"] == "inner.called" for m in sent)


def test_http_request_with_wrong_password_is_rejected():
    sent = _run({"type": "http"}, headers=_auth_header(USERNAME, "wrong"))
    assert sent[0]["status"] == 401
    assert not any(m["type"] == "inner.called" for m in sent)


def test_http_request_with_correct_credentials_passes_through():
    sent = _run({"type": "http"}, headers=_auth_header(USERNAME, PASSWORD))
    assert any(m["type"] == "inner.called" for m in sent)


def test_websocket_without_credentials_is_closed():
    sent = _run({"type": "websocket"})
    assert sent[0] == {"type": "websocket.close", "code": 4401}
    assert not any(m["type"] == "inner.called" for m in sent)


def test_websocket_with_correct_credentials_passes_through():
    sent = _run({"type": "websocket"}, headers=_auth_header(USERNAME, PASSWORD))
    assert any(m["type"] == "inner.called" for m in sent)


def test_non_http_non_websocket_scope_passes_through_unchecked():
    sent = _run({"type": "lifespan"})
    assert any(m["type"] == "inner.called" for m in sent)


def test_malformed_authorization_header_is_rejected():
    sent = _run({"type": "http"}, headers=(b"authorization", b"Basic not-valid-base64!!"))
    assert sent[0]["status"] == 401


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"\n{len(tests)} tests passed.")
