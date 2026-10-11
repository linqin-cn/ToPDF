"""接口层依赖：配置、渲染服务、产物存储、限流与鉴权。

全部依赖都从 `app.state` 取进程内单例，路由函数因此保持纯粹、可测试。
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request

from resume_pdf.core.config import Settings
from resume_pdf.core.errors import ErrorDetail, RateLimitedError, UnauthorizedError
from resume_pdf.core.logging import get_logger
from resume_pdf.core.ratelimit import TokenBucketLimiter
from resume_pdf.render.service import RenderService
from resume_pdf.storage.base import ArtifactStore

logger = get_logger("resume_pdf.api")

API_KEY_HEADER = "X-API-Key"
BEARER_PREFIX = "bearer "
UNKNOWN_CLIENT = "unknown"


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_render_service(request: Request) -> RenderService:
    service: RenderService = request.app.state.render_service
    return service


def get_artifact_store(request: Request) -> ArtifactStore:
    store: ArtifactStore = request.app.state.artifact_store
    return store


def get_rate_limiter(request: Request) -> TokenBucketLimiter:
    limiter: TokenBucketLimiter = request.app.state.rate_limiter
    return limiter


def client_ip(request: Request) -> str:
    """客户端 IP：仅信任 ASGI Server 解析后的结果，代理场景由 uvicorn 的
    `--proxy-headers` 负责还原（见 docs/05-deployment-ops.md）。"""
    client = request.client
    return client.host if client is not None else UNKNOWN_CLIENT


def public_base_url(request: Request, settings: Settings) -> str:
    """对外基地址：优先 `PUBLIC_BASE_URL`，未配置时按当前请求推导（便于本地与预发）。"""
    configured = settings.public_base_url.strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")


def extract_api_key(request: Request) -> str | None:
    """按 `X-API-Key` 优先、`Authorization: Bearer` 兜底的顺序解析密钥。"""
    header_key = request.headers.get(API_KEY_HEADER)
    if header_key and header_key.strip():
        return header_key.strip()
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith(BEARER_PREFIX):
        token = authorization[len(BEARER_PREFIX) :].strip()
        return token or None
    return None


def require_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> str | None:
    """鉴权依赖：返回已校验的密钥；`AUTH_ENABLED=false` 时返回 None。"""
    if not settings.auth_enabled:
        return None

    presented = extract_api_key(request)
    if presented is None:
        raise UnauthorizedError(
            "缺少 API Key",
            details=[ErrorDetail(reason=f"缺少请求头 {API_KEY_HEADER}", field=API_KEY_HEADER)],
        )

    matched = False
    for candidate in settings.api_key_set:
        # 逐项比较且不短路，避免通过响应时间区分命中的是哪一个密钥。
        matched |= secrets.compare_digest(presented, candidate)
    if not matched:
        raise UnauthorizedError("API Key 无效")
    return presented


def enforce_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limiter: Annotated[TokenBucketLimiter, Depends(get_rate_limiter)],
    api_key: Annotated[str | None, Depends(require_api_key)],
) -> None:
    """令牌桶限流：已鉴权按 Key 维度，未鉴权按客户端 IP 维度。"""
    identity = f"key:{api_key}" if api_key else f"ip:{client_ip(request)}"
    retry_after = limiter.check(identity)
    if retry_after is None:
        return

    logger.warning("rate_limited", extra={"identity_kind": identity.split(":", 1)[0]})
    raise RateLimitedError("请求频率超过配额", retry_after=retry_after)
