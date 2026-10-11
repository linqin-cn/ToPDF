"""对外提供 OpenAPI 契约文件。

`docs/04-openapi.yaml` 是权威契约，`docs/04-openapi.agent.yaml` 由脚本从它派生。
两个端点都在响应时注入 `servers[0].url`：域名分配后只需改 `PUBLIC_BASE_URL`
并重启，无需改动仓库里的 Schema 文件。
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, Request, Response

from resume_pdf.api.deps import get_settings_dep, public_base_url
from resume_pdf.core.config import Settings
from resume_pdf.core.errors import InternalError
from resume_pdf.core.logging import get_logger

logger = get_logger("resume_pdf.contract")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FULL_SPEC_NAME = "04-openapi.yaml"
AGENT_SPEC_NAME = "04-openapi.agent.yaml"
YAML_MEDIA_TYPE = "application/yaml"
MAX_CACHED_RENDERS = 16

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


_render_cache: dict[tuple[Path, str], str] = {}


def _render_spec(path: Path, base_url: str) -> str:
    """把契约渲染为文本，并按 (文件, 基地址) 缓存，避免每请求重复序列化。"""
    cache_key = (path, base_url)
    cached = _render_cache.get(cache_key)
    if cached is not None:
        return cached

    spec = copy.deepcopy(_load_spec(path))
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        servers[0]["url"] = base_url
    if len(_render_cache) >= MAX_CACHED_RENDERS:
        _render_cache.clear()
    rendered = yaml.safe_dump(
        spec, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120
    )
    _render_cache[cache_key] = rendered
    return rendered


def clear_spec_cache() -> None:
    """测试用：清空契约缓存。"""
    _load_spec.cache_clear()
    _render_cache.clear()


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
    body = _render_spec(path, public_base_url(request, settings))
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
    body = _render_spec(path, public_base_url(request, settings))
    return Response(content=body, media_type=YAML_MEDIA_TYPE)
