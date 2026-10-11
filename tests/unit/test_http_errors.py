"""统一异常处理、链路 ID 与请求体上限的接口级测试。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from tests.factories import make_settings

from resume_pdf.core.errors import ErrorCode, RenderTimeoutError
from resume_pdf.main import create_app


@pytest.fixture
def error_app() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/boom-expected")
    async def boom_expected() -> None:
        raise RenderTimeoutError("渲染超过超时上限", retry_after=1)

    @app.get("/boom-unexpected")
    async def boom_unexpected() -> None:
        raise RuntimeError("内部实现细节不应外泄")

    return app


@pytest.fixture
async def error_client(error_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=error_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_unknown_path_uses_unified_envelope(error_client: httpx.AsyncClient) -> None:
    response = await error_client.get("/not-exists")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["code"] == ErrorCode.NOT_FOUND
    assert body["request_id"]
    assert response.headers["X-Request-Id"] == body["request_id"]


async def test_expected_error_maps_to_declared_status(error_client: httpx.AsyncClient) -> None:
    response = await error_client.get("/boom-expected")

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == ErrorCode.RENDER_TIMEOUT
    assert body["message"] == "渲染超过超时上限"
    assert response.headers["Retry-After"] == "1"


async def test_unexpected_error_does_not_leak_internals(error_client: httpx.AsyncClient) -> None:
    response = await error_client.get("/boom-unexpected")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == ErrorCode.INTERNAL_ERROR
    assert "内部实现细节不应外泄" not in body["message"]
    assert "RuntimeError" not in str(body)
    assert "Traceback" not in str(body)


async def test_request_id_is_echoed_when_valid(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-Id": "trace-123.abc"})

    assert response.headers["X-Request-Id"] == "trace-123.abc"


async def test_invalid_request_id_is_replaced(client: httpx.AsyncClient) -> None:
    invalid = "has spaces and !"
    response = await client.get("/healthz", headers={"X-Request-Id": invalid})

    generated = response.headers["X-Request-Id"]
    assert generated != invalid
    assert len(generated) == 32


async def test_request_body_over_limit_is_rejected_before_routing() -> None:
    app = create_app(make_settings(max_request_bytes=1024, max_html_bytes=1024))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/v1/render", content=b"x" * 4096)

    assert response.status_code == 413
    assert response.json()["code"] == ErrorCode.PAYLOAD_TOO_LARGE
    assert response.json()["request_id"]
