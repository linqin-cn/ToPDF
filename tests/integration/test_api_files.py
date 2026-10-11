"""产物下载、签名模式与运维端点用例。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from tests.factories import make_settings
from tests.fakes import StubEngine, build_app
from tests.integration.test_api_render import HTML, assert_error, make_client

from resume_pdf.core.config import Settings
from resume_pdf.core.errors import ErrorCode
from resume_pdf.core.signing import sign_download
from resume_pdf.storage.local import LocalArtifactStore


def make_store(settings: Settings, *, clock: Any = time.time) -> LocalArtifactStore:
    store = LocalArtifactStore(settings.artifact_dir, clock=clock)
    return store


async def test_download_round_trip_with_etag(
    api_client: httpx.AsyncClient, api_app: FastAPI, api_settings: Settings
) -> None:
    rendered = await api_client.post("/v1/render", json={"html": HTML})
    file_url = rendered.json()["file_url"]

    response = await api_client.get(file_url)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    assert "attachment;" in response.headers["content-disposition"]
    assert "must-revalidate" in response.headers["cache-control"]

    cached = await api_client.get(file_url, headers={"If-None-Match": response.headers["ETag"]})
    assert cached.status_code == 304
    assert cached.content == b""


async def test_download_unknown_id_returns_file_not_found(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/v1/files/AAAAAAAAAAAAAAAAAAAAAA")

    assert response.status_code == 404
    assert_error(response, ErrorCode.FILE_NOT_FOUND)


async def test_download_rejects_malformed_id(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/v1/files/not-a-valid-id")

    assert response.status_code == 404
    assert_error(response, ErrorCode.FILE_NOT_FOUND)


async def test_download_requires_api_key(api_app: FastAPI) -> None:
    async with make_client(api_app, with_api_key=False) as client:
        response = await client.get("/v1/files/AAAAAAAAAAAAAAAAAAAAAA")

    assert response.status_code == 401
    assert_error(response, ErrorCode.UNAUTHORIZED)


async def test_expired_artifact_returns_410(tmp_path: Path) -> None:
    settings = make_settings(artifact_dir=tmp_path / "artifacts", render_tmp_dir=tmp_path / "tmp")
    app = build_app(settings, StubEngine())

    class Clock:
        now = 1_700_000_000.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    store = LocalArtifactStore(settings.artifact_dir, clock=clock)
    app.state.artifact_store = store
    ref = await store.put(b"%PDF-1.7\n%%EOF\n", file_name="resume.pdf", ttl_seconds=60)

    clock.now += 120
    async with make_client(app) as client:
        response = await client.get(f"/v1/files/{ref.file_id}")

    assert response.status_code == 410
    assert_error(response, ErrorCode.FILE_EXPIRED)


async def test_signed_mode_needs_valid_signature(tmp_path: Path) -> None:
    secret = "x" * 40
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        download_url_mode="signed",
        signing_secret=secret,
    )
    app = build_app(settings, StubEngine())
    store = make_store(settings)
    app.state.artifact_store = store
    ref = await store.put(b"%PDF-1.7\n%%EOF\n", file_name="resume.pdf", ttl_seconds=600)
    expires_at = int(time.time()) + 600

    async with make_client(app, with_api_key=False) as client:
        no_signature = await client.get(f"/v1/files/{ref.file_id}")
        bad_signature = await client.get(f"/v1/files/{ref.file_id}?exp={expires_at}&sig=deadbeef")
        expired_link = await client.get(
            f"/v1/files/{ref.file_id}?exp=1&sig={sign_download(ref.file_id, 1, secret)}"
        )
        good = await client.get(
            f"/v1/files/{ref.file_id}"
            f"?exp={expires_at}&sig={sign_download(ref.file_id, expires_at, secret)}"
        )

    assert no_signature.status_code == 401
    assert bad_signature.status_code == 401
    assert expired_link.status_code == 410
    assert good.status_code == 200
    assert good.content.startswith(b"%PDF-")


async def test_signed_mode_returns_signed_file_url(tmp_path: Path) -> None:
    secret = "y" * 40
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        download_url_mode="signed",
        signing_secret=secret,
    )
    async with make_client(build_app(settings, StubEngine())) as client:
        response = await client.post("/v1/render", json={"html": HTML})

    body = response.json()
    assert "exp=" in body["file_url"] and "sig=" in body["file_url"]

    async with make_client(build_app(settings, StubEngine()), with_api_key=False) as client:
        download = await client.get(body["file_url"])
    assert download.status_code == 200


async def test_readyz_reports_engine_and_storage(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"engine": "ok", "storage": "ok"}}


async def test_readyz_degraded_when_storage_unwritable(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
    )
    app = build_app(settings, StubEngine())
    app.state.artifact_store = BrokenStore()

    async with make_client(app) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["storage"] == "error"


class BrokenStore(LocalArtifactStore):
    """模拟产物目录不可写。"""

    def __init__(self) -> None:
        super().__init__(Path("unused"))

    async def healthy(self) -> bool:
        return False


async def test_metrics_exposes_render_gauges(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/metrics")

    assert response.status_code == 200
    assert "rpr_render_inflight" in response.text
    assert "rpr_render_queue_depth" in response.text


async def test_metrics_can_be_disabled(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        metrics_enabled=False,
    )
    async with make_client(build_app(settings, StubEngine())) as client:
        response = await client.get("/metrics")

    assert response.status_code == 404
    assert_error(response, ErrorCode.NOT_FOUND)


async def test_metrics_token_is_enforced(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        metrics_token="s3cret",
    )
    async with make_client(build_app(settings, StubEngine())) as client:
        denied = await client.get("/metrics")
        allowed = await client.get("/metrics", headers={"X-Metrics-Token": "s3cret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200


async def test_expired_artifacts_are_swept(tmp_path: Path) -> None:
    settings = make_settings(artifact_dir=tmp_path / "artifacts", render_tmp_dir=tmp_path / "tmp")
    store = LocalArtifactStore(settings.artifact_dir)
    ref = await store.put(b"%PDF-1.7\n%%EOF\n", file_name="resume.pdf", ttl_seconds=1)
    meta = json.loads(ref.meta_path.read_text(encoding="utf-8"))

    # 直接改写过期时间，避免等待真实的 TTL。
    meta["expires_at"] = time.time() - 1
    ref.meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert await store.sweep() == 1
    assert await store.stat(ref.file_id) is None


@pytest.mark.parametrize("path", ["/v1/openapi.yaml", "/healthz"])
async def test_public_endpoints_do_not_require_key(api_app: FastAPI, path: str) -> None:
    async with make_client(api_app, with_api_key=False) as client:
        response = await client.get(path)

    assert response.status_code == 200
