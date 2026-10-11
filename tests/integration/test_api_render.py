"""接口与契约用例（`docs/06-test-and-acceptance.md` 4.1 节）。

使用确定性替身引擎，因此不需要浏览器；真实 Chromium 的端到端链路见
`test_render_api_chromium.py`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from tests.factories import TEST_API_KEY, make_settings
from tests.fakes import StubEngine, build_app

from resume_pdf.core.config import Settings
from resume_pdf.core.errors import ErrorCode, RenderFailedError
from resume_pdf.storage.base import ArtifactStore

HTML = (
    "<html><head><style>@page{size:A4;margin:12mm}</style></head><body><h1>张三</h1></body></html>"
)


def make_client(app: FastAPI, *, with_api_key: bool = True) -> httpx.AsyncClient:
    headers = {"X-API-Key": TEST_API_KEY} if with_api_key else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
        headers=headers,
    )


def assert_error(response: httpx.Response, code: ErrorCode) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    assert body["success"] is False
    assert body["code"] == str(code)
    assert body["request_id"]
    assert response.headers["X-Request-Id"] == body["request_id"]
    return body


async def test_api_01_minimal_render_returns_downloadable_metadata(
    api_client: httpx.AsyncClient, api_app: FastAPI
) -> None:
    response = await api_client.post("/v1/render", json={"html": HTML})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["file_name"] == "resume.pdf"
    assert body["page_count"] == 1
    assert body["size_bytes"] > 0
    assert body["duration_ms"] >= 0
    assert body["engine"] == "stub"
    assert body["file_url"] == f"http://testserver/v1/files/{body['file_id']}"
    assert body["expires_at"].endswith("Z")
    assert response.headers["X-Page-Count"] == "1"
    assert response.headers["X-Engine"] == "stub"
    assert response.headers["X-Render-Duration-Ms"].isdigit()
    assert response.headers["X-Request-Id"] == body["request_id"]

    store: ArtifactStore = api_app.state.artifact_store
    ref = await store.stat(str(body["file_id"]))
    assert ref is not None
    assert ref.pdf_path.read_bytes().startswith(b"%PDF-")


async def test_api_02_missing_html_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/render", json={"file_name": "resume.pdf"})

    assert response.status_code == 400
    body = assert_error(response, ErrorCode.INVALID_ARGUMENT)
    assert any(detail["field"] == "html" for detail in body["details"])


async def test_api_03_blank_html_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/render", json={"html": "   \n\t "})

    assert response.status_code == 400
    assert_error(response, ErrorCode.INVALID_ARGUMENT)


async def test_api_04_invalid_json_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/v1/render",
        content=b'{"html": "<h1>x</h1>",}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    body = assert_error(response, ErrorCode.INVALID_JSON)
    assert "Traceback" not in str(body)


async def test_api_05_unsupported_content_type_is_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/render", content=b"<h1>x</h1>", headers={"Content-Type": "text/plain"}
    )

    assert response.status_code == 415
    assert_error(response, ErrorCode.UNSUPPORTED_MEDIA_TYPE)


async def test_api_06_unknown_page_size_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/render", json={"html": HTML, "page": {"size": "A0"}})

    assert response.status_code == 400
    body = assert_error(response, ErrorCode.INVALID_ARGUMENT)
    assert any(detail["field"] == "page.size" for detail in body["details"])


async def test_api_07_margin_over_limit_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/render", json={"html": HTML, "page": {"margin": "100mm"}})

    assert response.status_code == 400
    body = assert_error(response, ErrorCode.INVALID_ARGUMENT)
    assert any("page.margin" in detail["field"] for detail in body["details"])


@pytest.mark.parametrize("timeout_ms", [999, 60_000])
async def test_api_08_timeout_out_of_range_is_rejected(
    api_client: httpx.AsyncClient, timeout_ms: int
) -> None:
    response = await api_client.post("/v1/render", json={"html": HTML, "timeout_ms": timeout_ms})

    assert response.status_code == 400
    assert_error(response, ErrorCode.INVALID_ARGUMENT)


async def test_api_09_accept_pdf_returns_binary_stream(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/v1/render",
        json={"html": HTML, "file_name": "张三-简历.pdf"},
        headers={"Accept": "application/pdf"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")
    disposition = response.headers["content-disposition"]
    assert 'filename="' in disposition
    assert "filename*=UTF-8''%E5%BC%A0%E4%B8%89-%E7%AE%80%E5%8E%86.pdf" in disposition


async def test_api_09b_response_mode_binary_returns_binary_stream(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/render",
        json={"html": HTML, "response_mode": "binary"},
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-")


async def test_api_10_file_name_path_traversal_is_sanitized(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/render", json={"html": HTML, "file_name": "../../etc/passwd"}
    )

    assert response.status_code == 200
    file_name = response.json()["file_name"]
    assert "/" not in file_name
    assert "\\" not in file_name
    assert file_name.endswith(".pdf")


async def test_api_11_cjk_file_name_is_rfc5987_encoded(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/v1/render",
        json={"html": HTML, "file_name": "张三\r\nX-Injected.pdf"},
        headers={"Accept": "application/pdf"},
    )

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert "filename*=UTF-8''%E5%BC%A0%E4%B8%89_X-Injected.pdf" in disposition


async def test_api_12_form_submissions_match_json(api_client: httpx.AsyncClient) -> None:
    urlencoded = await api_client.post(
        "/v1/render",
        data={
            "html": HTML,
            "file_name": "表单.pdf",
            "page_size": "A4",
            "page_margin": "12mm",
            "page_orientation": "portrait",
            "page_media": "print",
            "page_print_background": "true",
            "max_pages": "3",
        },
    )
    multipart = await api_client.post(
        "/v1/render",
        files={"html": (None, HTML), "file_name": (None, "表单.pdf")},
    )

    assert urlencoded.status_code == 200, urlencoded.text
    assert multipart.status_code == 200, multipart.text
    assert urlencoded.json()["file_name"] == multipart.json()["file_name"] == "表单.pdf"
    assert urlencoded.json()["page_count"] == multipart.json()["page_count"] == 1


async def test_api_12b_bearer_token_is_accepted(api_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TEST_API_KEY}"},
    ) as client:
        response = await client.post("/v1/render", json={"html": HTML})

    assert response.status_code == 200


async def test_api_13_missing_api_key_is_unauthorized(api_app: FastAPI) -> None:
    async with make_client(api_app, with_api_key=False) as client:
        response = await client.post("/v1/render", json={"html": HTML})
        wrong_key = await client.post(
            "/v1/render", json={"html": HTML}, headers={"X-API-Key": "wrong-key"}
        )

    assert response.status_code == 401
    assert_error(response, ErrorCode.UNAUTHORIZED)
    assert wrong_key.status_code == 401
    assert_error(wrong_key, ErrorCode.UNAUTHORIZED)


async def test_api_13b_auth_can_be_disabled(tmp_path: Path) -> None:
    settings = make_settings(
        auth_enabled=False,
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
    )
    async with make_client(build_app(settings, StubEngine()), with_api_key=False) as client:
        response = await client.post("/v1/render", json={"html": HTML})

    assert response.status_code == 200


async def test_api_14_rate_limit_returns_retry_after(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        rate_limit_rpm=60,
        rate_limit_burst=1,
    )
    engine = StubEngine()
    async with make_client(build_app(settings, engine)) as client:
        first = await client.post("/v1/render", json={"html": HTML})
        second = await client.post("/v1/render", json={"html": HTML})

    assert first.status_code == 200
    assert second.status_code == 429
    assert_error(second, ErrorCode.RATE_LIMITED)
    assert int(second.headers["Retry-After"]) >= 1
    # 被限流的请求不进入渲染，不占用并发闸门。
    assert engine.render_calls == 1


async def test_api_15_legacy_endpoint_keeps_old_fields(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/render", json={"html": HTML, "file_name": "旧接口.pdf"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["file_name"] == "旧接口.pdf"
    assert body["message"] == "PDF 已生成"
    assert body["file_url"].startswith("http://testserver/v1/files/")
    assert body["request_id"]
    assert body["page_count"] == 1
    assert body["duration_ms"] >= 0
    assert response.headers["Deprecation"] == "true"
    assert response.headers["Link"] == '</v1/render>; rel="successor-version"'


async def test_api_16_legacy_endpoint_error_uses_unified_envelope(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/render", content=b"not-json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400
    assert_error(response, ErrorCode.INVALID_JSON)


async def test_page_options_unknown_field_is_rejected(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/v1/render", json={"html": HTML, "page": {"zoom": 1.5}})

    assert response.status_code == 400
    assert_error(response, ErrorCode.INVALID_ARGUMENT)


async def test_custom_page_size_requires_width_and_height(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/v1/render", json={"html": HTML, "page": {"size": "Custom", "width": 100}}
    )

    assert response.status_code == 400
    assert_error(response, ErrorCode.INVALID_ARGUMENT)


async def test_html_over_size_limit_is_rejected(
    api_client: httpx.AsyncClient, api_settings: Settings
) -> None:
    oversized = "x" * (api_settings.max_html_bytes + 1)
    response = await api_client.post("/v1/render", json={"html": f"<p>{oversized}</p>"})

    assert response.status_code == 413
    body = assert_error(response, ErrorCode.PAYLOAD_TOO_LARGE)
    assert any(detail["field"] == "html" for detail in body["details"])


async def test_too_many_pages_is_reported_as_unprocessable(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        max_pages=1,
    )
    async with make_client(build_app(settings, StubEngine(pages=3))) as client:
        response = await client.post("/v1/render", json={"html": HTML})

    assert response.status_code == 422
    assert_error(response, ErrorCode.PDF_TOO_MANY_PAGES)


async def test_engine_failure_maps_to_render_failed(tmp_path: Path) -> None:
    settings = make_settings(artifact_dir=tmp_path / "artifacts", render_tmp_dir=tmp_path / "tmp")
    engine = StubEngine(failure=RenderFailedError("PDF 渲染失败"))
    async with make_client(build_app(settings, engine)) as client:
        response = await client.post("/v1/render", json={"html": HTML})

    assert response.status_code == 500
    assert_error(response, ErrorCode.RENDER_FAILED)


async def test_render_timeout_maps_to_gateway_timeout(tmp_path: Path) -> None:
    settings = make_settings(
        artifact_dir=tmp_path / "artifacts",
        render_tmp_dir=tmp_path / "tmp",
        render_timeout_ms=1000,
        render_max_timeout_ms=1000,
    )
    async with make_client(build_app(settings, StubEngine(delay_seconds=5))) as client:
        response = await client.post("/v1/render", json={"html": HTML, "timeout_ms": 1000})

    assert response.status_code == 504
    assert_error(response, ErrorCode.RENDER_TIMEOUT)
