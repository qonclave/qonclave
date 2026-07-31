# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

import base64
import secrets


class BasicAuthMiddleware:
    """ASGI middleware that gates every request behind HTTP Basic Auth.

    Plain ASGI (not Starlette's `app.middleware("http")`) so it also covers
    the "websocket" scope: Starlette's HTTP-only middleware helper skips
    WebSocket upgrades entirely, which would leave the Socket.IO channel
    (used for live detections and LED status) unauthenticated.
    """

    def __init__(self, app, username: str, password: str):
        self.app = app
        self.username = username
        self.password = password

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if self._is_authorized(dict(scope.get("headers") or [])):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"www-authenticate", b'Basic realm="Qonclave Edge"'),
                (b"content-type", b"text/plain"),
            ],
        })
        await send({"type": "http.response.body", "body": b"Unauthorized"})

    def _is_authorized(self, headers: dict) -> bool:
        auth_header = headers.get(b"authorization")
        if not auth_header or not auth_header.startswith(b"Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header[len(b"Basic "):]).decode("utf-8")
        except Exception:
            return False
        username, _, password = decoded.partition(":")
        return secrets.compare_digest(username, self.username) and secrets.compare_digest(password, self.password)
