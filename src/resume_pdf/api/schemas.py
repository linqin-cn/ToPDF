"""接口层数据模型。

模型只负责「形状与基础范围」校验：跨字段规则（Custom 尺寸、边距语义、与配置
相关的上限）统一在 `render/pagesize.py` 与路由层判定，避免同一规则出现两份实现。
所有模型都禁止未知字段，防止调用方误传参数被静默忽略。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError as PydanticValidationError

from resume_pdf.core.errors import ErrorDetail, ValidationError

OrientationName = Literal["portrait", "landscape"]
MediaName = Literal["print", "screen"]
ResponseMode = Literal["url", "binary"]

MAX_FILE_NAME_LENGTH = 128
MIN_TIMEOUT_MS = 1000
MIN_ARTIFACT_TTL_SECONDS = 60


class PageOptions(BaseModel):
    """页面参数：只作用于渲染引擎选项，不会注入或改动 HTML。"""

    model_config = ConfigDict(extra="forbid")

    size: str | None = Field(
        default=None,
        description="A4/A3/A5/Letter/Legal/Tabloid/Custom；缺省时由 HTML 的 @page 决定。",
    )
    width: float | None = Field(default=None, ge=10, le=2000)
    height: float | None = Field(default=None, ge=10, le=2000)
    unit: str | None = Field(default=None, description="px/mm/cm/in/pt，默认 mm。")
    orientation: OrientationName = "portrait"
    margin: str | None = Field(default=None, description="CSS 风格简写，如 12mm、10mm 15mm。")
    media: MediaName = "print"
    print_background: bool = True


class RenderRequest(BaseModel):
    """`POST /v1/render` 的 JSON 请求体。"""

    model_config = ConfigDict(extra="forbid")

    html: str = Field(description="完整 HTML 文档（含内联 CSS），服务端原样渲染。")
    file_name: str | None = Field(default=None, max_length=MAX_FILE_NAME_LENGTH)
    page: PageOptions = Field(default_factory=PageOptions)
    timeout_ms: int | None = Field(default=None, ge=MIN_TIMEOUT_MS)
    max_pages: int | None = Field(default=None, ge=1)
    response_mode: ResponseMode | None = None
    artifact_ttl_seconds: int | None = Field(default=None, ge=MIN_ARTIFACT_TTL_SECONDS)

    @field_validator("html")
    @classmethod
    def _html_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("html 不能为空")
        return value


class LegacyRenderRequest(BaseModel):
    """旧 `POST /render` 请求体。

    刻意放宽未知字段：老调用方可能额外传递参数，兼容层不应因此拒绝请求，
    但多余字段一律不参与渲染。
    """

    model_config = ConfigDict(extra="ignore")

    html: str
    file_name: str | None = Field(default=None, max_length=MAX_FILE_NAME_LENGTH)

    @field_validator("html")
    @classmethod
    def _html_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("html 不能为空")
        return value


class RenderSuccessResponse(BaseModel):
    """`response_mode=url` 时的成功响应。"""

    success: Literal[True] = True
    request_id: str
    file_id: str
    file_name: str
    file_url: str
    expires_at: str
    page_count: int
    size_bytes: int
    duration_ms: int
    engine: str


class LegacyRenderSuccessResponse(BaseModel):
    """旧接口成功响应：保留原有字段，追加可诊断字段。"""

    success: Literal[True] = True
    file_name: str
    file_url: str
    message: str = "PDF 已生成"
    request_id: str
    page_count: int
    duration_ms: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    uptime_s: float


class ReadyChecks(BaseModel):
    engine: Literal["ok", "error"]
    storage: Literal["ok", "error"]


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: ReadyChecks
    detail: str | None = None


def parse_model[ModelT: BaseModel](
    model: type[ModelT],
    raw: Mapping[str, Any],
    *,
    message: str = "请求参数校验失败",
) -> ModelT:
    """把 Pydantic 校验错误转成契约中的 `INVALID_ARGUMENT` 错误。"""
    try:
        return model.model_validate(raw)
    except PydanticValidationError as exc:
        details = [
            ErrorDetail(
                reason=str(item.get("msg", "参数不合法")),
                field=_field_path(item.get("loc")),
            )
            for item in exc.errors()
        ]
        raise ValidationError(message, details=details) from exc


def _field_path(location: object) -> str | None:
    if not isinstance(location, tuple | list):
        return None
    parts = [str(part) for part in location if part != "__root__"]
    return ".".join(parts) or None
