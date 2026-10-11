"""测试共享夹具。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from resume_pdf.core.config import Settings
from resume_pdf.main import create_app
from tests.factories import TEST_API_KEY, make_settings
from tests.fakes import StubEngine, build_app


@pytest.fixture
def app() -> FastAPI:
    return create_app(make_settings())


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def api_settings(tmp_path: Path) -> Settings:
    """接口测试配置：产物与临时目录落在临时目录，限流放宽避免相互干扰。"""
    return make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        rate_limit_rpm=600,
        rate_limit_burst=600,
    )


@pytest.fixture
def stub_engine() -> StubEngine:
    return StubEngine()


@pytest.fixture
def api_app(api_settings: Settings, stub_engine: StubEngine) -> FastAPI:
    """注入确定性替身引擎的应用实例。"""
    return build_app(api_settings, stub_engine)


@pytest.fixture
async def api_client(api_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """默认携带合法 API Key 的接口测试客户端。"""
    transport = httpx.ASGITransport(app=api_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": TEST_API_KEY},
    ) as http_client:
        yield http_client
