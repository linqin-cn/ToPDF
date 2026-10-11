"""契约端点与星辰平台接入用例（4.6 节 PLT-01～PLT-04 与契约漂移防护）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi import FastAPI
from tests.factories import make_settings
from tests.fakes import StubEngine, build_app

from resume_pdf.api.routers.contract import clear_spec_cache, resolve_spec_dir
from resume_pdf.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL_SPEC = REPO_ROOT / "docs" / "04-openapi.yaml"
AGENT_SPEC = REPO_ROOT / "docs" / "04-openapi.agent.yaml"


@pytest.fixture
def contract_settings(tmp_path: Path) -> Settings:
    return make_settings(artifact_dir=tmp_path / "artifacts", render_tmp_dir=tmp_path / "tmp")


@pytest.fixture
def contract_app(contract_settings: Settings) -> FastAPI:
    clear_spec_cache()
    return build_app(contract_settings, StubEngine())


@pytest.fixture
def contract_client(contract_app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=contract_app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def load_spec(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


async def test_full_contract_is_served_as_yaml(contract_client: httpx.AsyncClient) -> None:
    async with contract_client as client:
        response = await client.get("/v1/openapi.yaml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/yaml")
    spec = yaml.safe_load(response.text)
    assert spec["openapi"] == "3.0.3"
    assert {"/v1/render", "/v1/files/{file_id}", "/render"} <= set(spec["paths"])


async def test_plt_01_agent_contract_only_exposes_two_operations(
    contract_client: httpx.AsyncClient,
) -> None:
    async with contract_client as client:
        response = await client.get("/v1/openapi.agent.yaml")

    assert response.status_code == 200
    spec = yaml.safe_load(response.text)
    assert set(spec["paths"]) == {"/v1/render", "/v1/files/{file_id}"}
    operations = [
        (method, path)
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    ]
    assert sorted(operations) == [("get", "/v1/files/{file_id}"), ("post", "/v1/render")]


async def test_plt_02_public_base_url_is_injected_into_servers(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        public_base_url="https://pdf.example.com/",
    )
    app = build_app(settings, StubEngine())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        configured = await client.get("/v1/openapi.agent.yaml")

    assert yaml.safe_load(configured.text)["servers"][0]["url"] == "https://pdf.example.com"


async def test_plt_02b_servers_fall_back_to_request_host(
    contract_client: httpx.AsyncClient,
) -> None:
    async with contract_client as client:
        response = await client.get("/v1/openapi.agent.yaml")

    assert yaml.safe_load(response.text)["servers"][0]["url"] == "http://testserver"


@pytest.mark.parametrize("spec_path", [FULL_SPEC, AGENT_SPEC])
def test_plt_03_contracts_are_valid_openapi_30(spec_path: Path) -> None:
    from openapi_spec_validator import validate_spec

    validate_spec(load_spec(spec_path))


def test_plt_04_agent_contract_is_derived_from_full_contract() -> None:
    full = load_spec(FULL_SPEC)
    agent = load_spec(AGENT_SPEC)

    for path in agent["paths"]:
        assert agent["paths"][path] == full["paths"][path]
    # 精简契约不得引入完整契约中不存在的组件。
    agent_schemas = agent["components"].get("schemas", {})
    for name, schema in agent_schemas.items():
        assert full["components"]["schemas"][name] == schema


def test_fastapi_route_body_schema_matches_contract() -> None:
    """`/docs` 展示的请求体字段必须与权威契约一致，防止两处漂移。"""
    from resume_pdf.api.routers.render import FORM_BODY_SCHEMA, _inline_schema
    from resume_pdf.api.schemas import RenderRequest

    contract = load_spec(FULL_SPEC)["components"]["schemas"]
    json_schema = _inline_schema(RenderRequest)

    contract_properties = set(contract["RenderRequest"]["properties"])
    assert set(json_schema["properties"]) == contract_properties
    assert json_schema["required"] == contract["RenderRequest"]["required"] == ["html"]

    page_properties = set(contract["PageOptions"]["properties"])
    assert set(json_schema["properties"]["page"]["properties"]) == page_properties

    form_keys = set(contract["RenderRequestForm"]["properties"])
    assert set(FORM_BODY_SCHEMA["properties"]) == form_keys


def test_spec_dir_resolution_prefers_repository_docs(contract_settings: Settings) -> None:
    assert resolve_spec_dir(contract_settings) == REPO_ROOT / "docs"
