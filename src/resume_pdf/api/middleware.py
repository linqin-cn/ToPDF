"""纯 ASGI 中间件：请求体上限预检与访问日志。"""

from __future__ import annotations

import json
import time
from typing import Any, Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from resume_pdf.core.errors import ErrorCode
from resume_pdf.core.logging import get_logger
from resume_pdf.core.request_id import (
    REQUEST_ID_HEADER,
    new_request_id,
    normalize_request_id,
    reset_request_id,
    set_request_id,
)

logger = get_logger("resume_pdf.access")

HTTP_SCOPE: Final = "http"


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value).decode("latin-1")
    return None


def _error_body(code: ErrorCode, message: str, request_id: str) -> bytes:
    return json.dumps(
        {"success": False, "code": str(code), "message": message, "request_id": request_id},
        ensure_ascii=False,
    ).encode("utf-8")


class RequestContextMiddleware:
    """注入链路 ID、统计耗时并输出访问日志；最外层中间件。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return

        request_id = normalize_request_id(_header(scope, REQUEST_ID_HEADER.lower().encode())) or (
            new_request_id()
        )
        token = set_request_id(request_id)
        started = time.perf_counter()
        status_holder: dict[str, Any] = {"status": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "http_request",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_holder["status"],
                    "duration_ms": duration_ms,
                    "client": (scope.get("client") or ("-", 0))[0],
                    "request_id": request_id,
                },
            )
            reset_request_id(token)


class RequestBodyLimitMiddleware:
    """按 Content-Length 预检请求体大小。

    不带 Content-Length 的分块请求无法在此拦截，由 html 字段级上限兜底
    （见 docs/05-deployment-ops.md 的说明）。
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return

        raw_length = _header(scope, b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                content_length = 0
            if content_length > self.max_bytes:
                request_id = (
                    normalize_request_id(_header(scope, REQUEST_ID_HEADER.lower().encode()))
                    or new_request_id()
                )
                body = _error_body(
                    ErrorCode.PAYLOAD_TOO_LARGE,
                    f"请求体超过上限 {self.max_bytes} 字节",
                    request_id,
                )
                headers = [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (REQUEST_ID_HEADER.lower().encode(), request_id.encode()),
                ]
                await send({"type": "http.response.start", "status": 413, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)
