"""运维端点：存活、就绪与 Prometheus 指标。"""

from __future__ import annotations

import secrets
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from resume_pdf import __version__
from resume_pdf.api.deps import get_artifact_store, get_render_service, get_settings_dep
from resume_pdf.core.config import Settings
from resume_pdf.core.errors import NotFoundError, UnauthorizedError
from resume_pdf.render.service import RenderService
from resume_pdf.storage.base import ArtifactStore

# 这是请求头名字，不是密钥（bandit 按名字里含 token 判定，故显式忽略）。
METRICS_TOKEN_HEADER = "X-Metrics-Token"  # noqa: S105

RENDER_INFLIGHT = Gauge("rpr_render_inflight", "当前正在渲染的请求数")
RENDER_QUEUE_DEPTH = Gauge("rpr_render_queue_depth", "排队等待渲染的请求数")

router = APIRouter(tags=["ops"])


@router.get("/healthz", summary="存活探针", description="进程存活即返回 200。")
async def healthz(request: Request) -> JSONResponse:
    uptime_s = round(time.monotonic() - request.app.state.started_at, 3)
    return JSONResponse({"status": "ok", "version": __version__, "uptime_s": uptime_s})


@router.get(
    "/readyz",
    summary="就绪探针",
    description="渲染引擎可用且产物目录可写时返回 200，否则返回 503。",
)
async def readyz(
    engine_service: Annotated[RenderService, Depends(get_render_service)],
    store: Annotated[ArtifactStore, Depends(get_artifact_store)],
) -> JSONResponse:
    engine_ok = await engine_service.engine.healthy()
    storage_ok = await store.healthy()
    healthy = engine_ok and storage_ok
    checks = {"engine": "ok" if engine_ok else "error", "storage": "ok" if storage_ok else "error"}
    if healthy:
        return JSONResponse({"status": "ok", "checks": checks})
    return JSONResponse(
        {"status": "degraded", "checks": checks, "detail": "依赖不可用"}, status_code=503
    )


@router.get(
    "/metrics",
    summary="Prometheus 指标",
    description="`METRICS_ENABLED=false` 时返回 404；配置 `METRICS_TOKEN` 后需带同名请求头。",
)
async def metrics(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[RenderService, Depends(get_render_service)],
) -> Response:
    if not settings.metrics_enabled:
        raise NotFoundError("指标端点已关闭")
    if settings.metrics_protected:
        presented = request.headers.get(METRICS_TOKEN_HEADER, "")
        if not secrets.compare_digest(presented, settings.metrics_token):
            raise UnauthorizedError("指标端点令牌无效")

    RENDER_INFLIGHT.set(service.inflight)
    RENDER_QUEUE_DEPTH.set(service.queue_depth)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
