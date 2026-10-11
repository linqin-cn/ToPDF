"""渲染接口：`POST /v1/render` 与兼容的 `POST /render`。

这里只做「协议层」工作：解析入参、解析限额、编排渲染与存储、拼装响应。
渲染规则本身全部在 `render/` 下，路由层不接触 HTML 内容。
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from resume_pdf.api.body import read_render_payload
from resume_pdf.api.deps import (
    enforce_rate_limit,
    get_artifact_store,
    get_render_service,
    get_settings_dep,
    public_base_url,
)
from resume_pdf.api.schemas import (
    LegacyRenderRequest,
    LegacyRenderSuccessResponse,
    RenderRequest,
    RenderSuccessResponse,
    parse_model,
)
from resume_pdf.core.config import Settings
from resume_pdf.core.errors import ErrorDetail, PayloadTooLargeError, ValidationError
from resume_pdf.core.filenames import content_disposition, sanitize_file_name
from resume_pdf.core.logging import get_logger
from resume_pdf.core.request_id import get_request_id
from resume_pdf.core.signing import sign_download
from resume_pdf.render.engine import RenderOptions
from resume_pdf.render.pagesize import resolve_page
from resume_pdf.render.service import RenderService
from resume_pdf.storage.base import ArtifactStore

logger = get_logger("resume_pdf.render")

PDF_MEDIA_TYPE = "application/pdf"
JSON_MEDIA_TYPE = "application/json"
FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
LEGACY_SUCCESSOR_LINK = '</v1/render>; rel="successor-version"'

JSON_BODY_SCHEMA_KEY = "application/json"
FORM_BODY_SCHEMA_KEY = "application/x-www-form-urlencoded"


@dataclass(frozen=True, slots=True)
class ResolvedLimits:
    """请求级限额：已与调用方入参和配置上限对齐。"""

    timeout_ms: int
    max_pages: int
    ttl_seconds: int


def _inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """把模型 JSON Schema 内联成自包含结构，供 `/docs` 展示（无 $ref 漂移）。"""
    schema = model.model_json_schema()
    definitions: dict[str, Any] = schema.pop("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return _resolve(copy.deepcopy(definitions[ref.rsplit("/", 1)[-1]]))
            return {key: _resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [_resolve(item) for item in node]
        return node

    resolved: dict[str, Any] = _resolve(schema)
    return resolved


FORM_BODY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["html"],
    "properties": {
        "html": {"type": "string"},
        "file_name": {"type": "string"},
        "page_size": {"type": "string"},
        "page_orientation": {"type": "string", "enum": ["portrait", "landscape"]},
        "page_margin": {"type": "string"},
        "page_media": {"type": "string", "enum": ["print", "screen"]},
        "page_print_background": {"type": "string", "enum": ["true", "false"]},
        "timeout_ms": {"type": "integer"},
        "max_pages": {"type": "integer"},
        "response_mode": {"type": "string", "enum": ["url", "binary"]},
        "artifact_ttl_seconds": {"type": "integer"},
    },
}


def _render_request_body(
    *, json_schema: dict[str, Any], form_schema: dict[str, Any]
) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            JSON_BODY_SCHEMA_KEY: {"schema": json_schema},
            FORM_BODY_SCHEMA_KEY: {"schema": form_schema},
            "multipart/form-data": {"schema": form_schema},
        },
    }


router = APIRouter(tags=["render"], dependencies=[Depends(enforce_rate_limit)])


def _resolve_limits(payload: RenderRequest, settings: Settings) -> ResolvedLimits:
    """把可选入参与配置合并为最终限额，越界立即拒绝。"""
    timeout_ms = payload.timeout_ms or settings.render_timeout_ms
    if timeout_ms > settings.render_max_timeout_ms:
        raise ValidationError(
            f"timeout_ms 不得超过 {settings.render_max_timeout_ms}",
            details=[
                ErrorDetail(
                    reason=f"最大允许 {settings.render_max_timeout_ms} 毫秒",
                    field="timeout_ms",
                )
            ],
        )

    max_pages = payload.max_pages or settings.max_pages
    if max_pages > settings.hard_max_pages:
        raise ValidationError(
            f"max_pages 不得超过 {settings.hard_max_pages}",
            details=[
                ErrorDetail(reason=f"最大允许 {settings.hard_max_pages} 页", field="max_pages")
            ],
        )

    ttl_seconds = payload.artifact_ttl_seconds or settings.artifact_ttl_seconds
    if ttl_seconds > settings.hard_max_ttl_seconds:
        raise ValidationError(
            f"artifact_ttl_seconds 不得超过 {settings.hard_max_ttl_seconds}",
            details=[
                ErrorDetail(
                    reason=f"最大允许 {settings.hard_max_ttl_seconds} 秒",
                    field="artifact_ttl_seconds",
                )
            ],
        )
    return ResolvedLimits(timeout_ms=timeout_ms, max_pages=max_pages, ttl_seconds=ttl_seconds)


def _assert_html_size(html: str, settings: Settings) -> int:
    html_bytes = len(html.encode("utf-8"))
    if html_bytes > settings.max_html_bytes:
        raise PayloadTooLargeError(
            f"html 超过大小上限 {settings.max_html_bytes} 字节",
            details=[
                ErrorDetail(
                    reason=f"limit_bytes={settings.max_html_bytes}, actual_bytes={html_bytes}",
                    field="html",
                )
            ],
        )
    return html_bytes


def _build_options(
    payload: RenderRequest, settings: Settings, limits: ResolvedLimits
) -> RenderOptions:
    page = resolve_page(
        size=payload.page.size,
        width=payload.page.width,
        height=payload.page.height,
        unit=payload.page.unit,
        orientation=payload.page.orientation,
        margin=payload.page.margin,
        default_size=settings.default_page_size,
    )
    return RenderOptions(
        page=page,
        media=payload.page.media,
        print_background=payload.page.print_background,
        timeout_ms=limits.timeout_ms,
        max_pages=limits.max_pages,
        max_pdf_bytes=settings.max_pdf_bytes,
    )


def _wants_binary(request: Request, payload: RenderRequest, settings: Settings) -> bool:
    """`Accept: application/pdf` 优先于请求参数；两者都没有时用服务默认值。"""
    accept = request.headers.get("accept", "").lower()
    if PDF_MEDIA_TYPE in accept:
        return True
    if payload.response_mode is not None:
        return payload.response_mode == "binary"
    if JSON_MEDIA_TYPE in accept:
        return False
    return settings.response_mode == "binary"


def _iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


def _file_url(request: Request, settings: Settings, file_id: str, ttl_seconds: int) -> str:
    """拼装下载地址；签名模式下附带 `exp` 与 `sig`，链接自带有效期。"""
    base = public_base_url(request, settings)
    if settings.download_url_mode != "signed":
        return f"{base}/v1/files/{file_id}"
    expires_at = int(time.time()) + ttl_seconds
    signature = sign_download(file_id, expires_at, settings.signing_secret)
    return f"{base}/v1/files/{file_id}?exp={expires_at}&sig={signature}"


def _metric_headers(rendered_page_count: int, duration_ms: int, engine: str) -> dict[str, str]:
    return {
        "X-Page-Count": str(rendered_page_count),
        "X-Render-Duration-Ms": str(duration_ms),
        "X-Engine": engine,
    }


def _log_success(
    *,
    page_count: int,
    duration_ms: int,
    engine: str,
    html_sha256: str,
    html_bytes: int,
    blocked_requests: int,
    response_mode: str,
) -> None:
    logger.info(
        "render_succeeded",
        extra={
            "page_count": page_count,
            "duration_ms": duration_ms,
            "engine": engine,
            "html_sha256_12": html_sha256[:12],
            "html_bytes": html_bytes,
            "blocked_requests": blocked_requests,
            "response_mode": response_mode,
        },
    )


@router.post(
    "/v1/render",
    summary="将完整 HTML 渲染为 PDF",
    description=(
        "接收完整 HTML（含内联 CSS）并原样渲染为 PDF，不修改、补全或美化任何结构、"
        "内容与样式。响应形态由 `response_mode` 与 `Accept` 共同决定，"
        "`Accept: application/pdf` 优先。权威契约见 `/v1/openapi.yaml`。"
    ),
    openapi_extra={
        "requestBody": _render_request_body(
            json_schema=_inline_schema(RenderRequest), form_schema=FORM_BODY_SCHEMA
        )
    },
)
async def render_pdf(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[RenderService, Depends(get_render_service)],
    store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> Response:
    payload = parse_model(RenderRequest, await read_render_payload(request))
    html_bytes = _assert_html_size(payload.html, settings)
    limits = _resolve_limits(payload, settings)
    file_name = sanitize_file_name(payload.file_name)
    binary = _wants_binary(request, payload, settings)

    rendered = await service.render(payload.html, _build_options(payload, settings, limits))
    _log_success(
        page_count=rendered.page_count,
        duration_ms=rendered.duration_ms,
        engine=rendered.engine,
        html_sha256=rendered.html_sha256,
        html_bytes=html_bytes,
        blocked_requests=rendered.blocked_requests,
        response_mode="binary" if binary else "url",
    )
    headers = _metric_headers(rendered.page_count, rendered.duration_ms, rendered.engine)

    if binary:
        headers["Content-Disposition"] = content_disposition(file_name)
        return Response(content=rendered.content, media_type=PDF_MEDIA_TYPE, headers=headers)

    artifact = await store.put(
        rendered.content, file_name=file_name, ttl_seconds=limits.ttl_seconds
    )
    body = RenderSuccessResponse(
        request_id=get_request_id(),
        file_id=artifact.file_id,
        file_name=file_name,
        file_url=_file_url(request, settings, artifact.file_id, limits.ttl_seconds),
        expires_at=_iso_utc(artifact.expires_at),
        page_count=rendered.page_count,
        size_bytes=artifact.byte_size,
        duration_ms=rendered.duration_ms,
        engine=rendered.engine,
    )
    return JSONResponse(content=body.model_dump(), headers=headers)


@router.post(
    "/render",
    summary="旧版渲染接口（已弃用）",
    description=(
        "行为等价于 `POST /v1/render` 且 `response_mode=url`、页面参数取默认值。"
        "成功响应保留旧字段并追加 `request_id`、`page_count`、`duration_ms`。"
        "新接入请直接使用 `POST /v1/render`。"
    ),
    deprecated=True,
    openapi_extra={
        "requestBody": _render_request_body(
            json_schema=_inline_schema(LegacyRenderRequest), form_schema=FORM_BODY_SCHEMA
        )
    },
)
async def render_pdf_legacy(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[RenderService, Depends(get_render_service)],
    store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> JSONResponse:
    raw = await read_render_payload(request)
    legacy = parse_model(LegacyRenderRequest, raw)
    # 旧接口只接受 html/file_name，页面参数与响应形态一律取服务默认值。
    payload = RenderRequest(html=legacy.html, file_name=legacy.file_name, response_mode="url")

    html_bytes = _assert_html_size(payload.html, settings)
    limits = _resolve_limits(payload, settings)
    file_name = sanitize_file_name(payload.file_name)
    rendered = await service.render(payload.html, _build_options(payload, settings, limits))
    _log_success(
        page_count=rendered.page_count,
        duration_ms=rendered.duration_ms,
        engine=rendered.engine,
        html_sha256=rendered.html_sha256,
        html_bytes=html_bytes,
        blocked_requests=rendered.blocked_requests,
        response_mode="url",
    )
    artifact = await store.put(
        rendered.content, file_name=file_name, ttl_seconds=limits.ttl_seconds
    )
    body = LegacyRenderSuccessResponse(
        file_name=file_name,
        file_url=_file_url(request, settings, artifact.file_id, limits.ttl_seconds),
        request_id=get_request_id(),
        page_count=rendered.page_count,
        duration_ms=rendered.duration_ms,
    )
    headers = _metric_headers(rendered.page_count, rendered.duration_ms, rendered.engine)
    headers["Deprecation"] = "true"
    headers["Link"] = LEGACY_SUCCESSOR_LINK
    return JSONResponse(content=body.model_dump(), headers=headers)
