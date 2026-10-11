"""产物下载端点 `GET /v1/files/{file_id}`。

访问控制有两种模式（由 `DOWNLOAD_URL_MODE` 决定）：

- `key`（默认）：需要 `X-API-Key`，与渲染接口一致；
- `signed`：需要查询参数 `exp`（Unix 秒）与 `sig`（HMAC-SHA256），适合把链接给
  不持有密钥的调用方，同时用过期时间限定有效期。
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from resume_pdf.api.deps import get_artifact_store, get_settings_dep, require_api_key
from resume_pdf.core.config import Settings
from resume_pdf.core.errors import (
    ArtifactExpiredError,
    ArtifactNotFoundError,
    ErrorDetail,
    UnauthorizedError,
)
from resume_pdf.core.filenames import content_disposition
from resume_pdf.core.logging import get_logger
from resume_pdf.core.signing import verify_download
from resume_pdf.storage.base import ArtifactStore

logger = get_logger("resume_pdf.files")

ETAG_LENGTH = 16
CACHE_CONTROL = "private, max-age=0, must-revalidate"

router = APIRouter(tags=["files"])


def etag_of(sha256: str) -> str:
    """ETag 取内容摘要前 16 位，足够区分产物且不暴露完整校验和。"""
    return f'"{sha256[:ETAG_LENGTH]}"'


def _require_signature(request: Request, file_id: str, settings: Settings) -> None:
    raw_exp = request.query_params.get("exp", "")
    signature = request.query_params.get("sig", "")
    if not raw_exp.isdigit() or not signature:
        raise UnauthorizedError(
            "缺少下载签名",
            details=[ErrorDetail(reason="需要 exp 与 sig 查询参数", field="sig")],
        )
    expires_at = int(raw_exp)
    if expires_at < int(time.time()):
        raise ArtifactExpiredError("下载链接已过期")
    if not verify_download(file_id, expires_at, signature, settings.signing_secret):
        raise UnauthorizedError("下载签名无效")


async def authorize_download(
    request: Request,
    file_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """下载鉴权：签名模式校验签名，其余模式复用接口密钥校验。"""
    if settings.download_url_mode == "signed":
        _require_signature(request, file_id, settings)
        return
    require_api_key(request, settings)


@router.get(
    "/v1/files/{file_id}",
    summary="下载已生成的 PDF",
    description=(
        "按产物 ID 下载 PDF。支持 `If-None-Match`（命中返回 304）；"
        "产物不存在返回 404 `FILE_NOT_FOUND`，超过 TTL 返回 410 `FILE_EXPIRED`。"
    ),
)
async def download_file(
    request: Request,
    file_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    store: Annotated[ArtifactStore, Depends(get_artifact_store)],
    _: Annotated[None, Depends(authorize_download)],
) -> Response:
    ref = await store.stat(file_id)
    if ref is None:
        raise ArtifactNotFoundError("产物不存在")
    if store.is_expired(ref):
        raise ArtifactExpiredError(
            "产物已过期", details=[ErrorDetail(reason="超过 TTL", field="file_id")]
        )

    etag = etag_of(ref.sha256)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})

    content = await store.read(ref)
    logger.info(
        "artifact_downloaded",
        extra={"file_id": file_id, "byte_size": ref.byte_size, "mode": settings.download_url_mode},
    )
    return Response(
        content=content,
        media_type=ref.content_type,
        headers={
            "ETag": etag,
            "Cache-Control": CACHE_CONTROL,
            "Content-Disposition": content_disposition(ref.file_name),
        },
    )
