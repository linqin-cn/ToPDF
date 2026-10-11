"""对外提供 OpenAPI 契约文件。

`docs/04-openapi.yaml` 是权威契约，`docs/04-openapi.agent.yaml` 由脚本从它派生。
四个端点都在响应时注入 `servers[0].url`：域名分配后只需改 `PUBLIC_BASE_URL`
并重启，无需改动仓库里的 Schema 文件。

每种契约同时提供 YAML 与 JSON 两种表示：部分平台只接受 JSON（直接用 JSON.parse 解析
响应体），只给 YAML 会被判成「无效 Schema」。
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from resume_pdf.api.deps import get_settings_dep, public_base_url
from resume_pdf.core.config import Settings
from resume_pdf.core.errors import InternalError
from resume_pdf.core.logging import get_logger

logger = get_logger("resume_pdf.contract")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FULL_SPEC_NAME = "04-openapi.yaml"
AGENT_SPEC_NAME = "04-openapi.agent.yaml"
YAML_MEDIA_TYPE = "application/yaml"
MAX_CACHED_DOCUMENTS = 16

router = APIRouter(tags=["ops"])


def resolve_spec_dir(settings: Settings) -> Path:
    """解析契约文件目录：相对路径按仓库根目录优先，其次按当前工作目录。"""
    configured = settings.openapi_spec_dir
    if configured.is_absolute():
        return configured
    repo_relative = PROJECT_ROOT / configured
    if repo_relative.is_dir():
        return repo_relative
    return Path.cwd() / configured


@lru_cache(maxsize=4)
def _load_spec(path: Path) -> dict[str, Any]:
    """读取并缓存契约文件的解析结果；进程内只解析一次。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("openapi_spec_unreadable", extra={"path": str(path)})
        raise InternalError("OpenAPI 契约文件不可用") from exc
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise InternalError("OpenAPI 契约文件格式不合法")
    return loaded


_document_cache: dict[tuple[Path, str], dict[str, Any]] = {}


def _with_base_url(spec: dict[str, Any], base_url: str) -> dict[str, Any]:
    """复制契约并把对外基地址写入 `servers[0].url`。"""
    spec = copy.deepcopy(spec)
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        servers[0]["url"] = base_url
    return spec


def _document(path: Path, base_url: str) -> dict[str, Any]:
    """按 (文件, 基地址) 缓存注入基地址后的契约，避免每请求重复解析。"""
    cache_key = (path, base_url)
    cached = _document_cache.get(cache_key)
    if cached is None:
        cached = _with_base_url(_load_spec(path), base_url)
        if len(_document_cache) >= MAX_CACHED_DOCUMENTS:
            _document_cache.clear()
        _document_cache[cache_key] = cached
    return cached


def _yaml_text(path: Path, base_url: str) -> str:
    return yaml.safe_dump(
        _document(path, base_url),
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )


def clear_spec_cache() -> None:
    """测试用：清空契约缓存。"""
    _load_spec.cache_clear()
    _document_cache.clear()


@router.get(
    "/v1/openapi.yaml",
    summary="完整 OpenAPI 3.0.3 契约",
    description="权威契约文件，含渲染、下载、运维与已弃用接口。",
)
async def openapi_yaml(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    path = resolve_spec_dir(settings) / FULL_SPEC_NAME
    body = _yaml_text(path, public_base_url(request, settings))
    return Response(content=body, media_type=YAML_MEDIA_TYPE)


@router.get(
    "/v1/openapi.agent.yaml",
    summary="智能体平台导入用精简契约",
    description="由完整契约生成，仅包含渲染与下载两个操作，供智能体平台按 Schema 导入。",
)
async def openapi_agent_yaml(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    path = resolve_spec_dir(settings) / AGENT_SPEC_NAME
    body = _yaml_text(path, public_base_url(request, settings))
    return Response(content=body, media_type=YAML_MEDIA_TYPE)


@router.get(
    "/v1/openapi.json",
    summary="完整契约（JSON 表示）",
    description="与 `/v1/openapi.yaml` 内容相同，供只接受 JSON 的导入工具使用。",
)
async def openapi_json(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> JSONResponse:
    path = resolve_spec_dir(settings) / FULL_SPEC_NAME
    return JSONResponse(_document(path, public_base_url(request, settings)))


@router.get(
    "/v1/openapi.agent.json",
    summary="智能体平台导入用精简契约（JSON 表示）",
    description="与 `/v1/openapi.agent.yaml` 内容相同，平台导入优先用这个地址。",
)
async def openapi_agent_json(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> JSONResponse:
    path = resolve_spec_dir(settings) / AGENT_SPEC_NAME
    return JSONResponse(_document(path, public_base_url(request, settings)))
