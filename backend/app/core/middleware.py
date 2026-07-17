"""
Request-context + access-log middleware.

Existing implementation (MVP): none. No request IDs, no access logs, no way to
trace a single request through the logs.

Enterprise solution: every inbound request gets a `request_id` (and honours an
inbound `X-Correlation-ID` for tracing across services). Both are bound to
contextvars so every structured log line in the request carries them, echoed back
in response headers, and one access-log line is emitted per request with method,
path, status, and duration. This is the seam OpenTelemetry hooks into later.
"""
from __future__ import annotations

import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import correlation_id_ctx, get_logger, request_id_ctx

log = get_logger("dmrv.access")


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        corr = headers.get(b"x-correlation-id", b"").decode() or str(uuid.uuid4())
        rid = str(uuid.uuid4())
        rid_token = request_id_ctx.set(rid)
        corr_token = correlation_id_ctx.set(corr)

        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", rid.encode()))
                message["headers"].append((b"x-correlation-id", corr.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.info(
                "http.request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_holder["status"],
                duration_ms=duration_ms,
            )
            request_id_ctx.reset(rid_token)
            correlation_id_ctx.reset(corr_token)
