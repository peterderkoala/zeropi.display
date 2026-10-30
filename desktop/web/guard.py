"""The localhost guard (web-ui spec §8.2), all of it.

Browser-borne attacks only (§8.1): CSRF, DNS rebinding, clickjacking.

1. Host allowlist on every request, hardcoded, never a Configuration key.
2. Every `POST` is JSON, with its `Origin`, when present, naming this
   service. No `Origin` is accepted, so `curl` keeps working.
3. No CORS: nothing here ever sends an `Access-Control-*` header.
4. Never framed: both framing headers on every response, the guard's own
   refusals included.

No token of any kind (§8.2).
"""

from __future__ import annotations

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import PlainTextResponse

ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
FRAMING_HEADERS = {"Content-Security-Policy": "frame-ancestors 'none'", "X-Frame-Options": "DENY"}


class _Guard:
    def __init__(self, app, port: int) -> None:
        self.app = app
        self.origins = {f"http://{host}:{port}" for host in ALLOWED_HOSTS}

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def framed_send(message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in FRAMING_HEADERS.items():
                    headers[name] = value
            await send(message)

        if scope["method"] == "POST" and not self._post_allowed(Headers(scope=scope)):
            await PlainTextResponse("Forbidden", status_code=403)(scope, receive, framed_send)
            return
        await self.app(scope, receive, framed_send)

    def _post_allowed(self, headers: Headers) -> bool:
        media_type = headers.get("content-type", "").split(";")[0].strip().lower()
        origin = headers.get("origin")
        return media_type == "application/json" and (origin is None or origin in self.origins)


def install(app, port: int) -> None:
    """Wraps `app` in the guard. Added last, so it is outermost and frames
    `TrustedHostMiddleware`'s `400` too."""
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
    app.add_middleware(_Guard, port=port)
