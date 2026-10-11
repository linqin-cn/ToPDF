"""请求体读取与形态归一。

`POST /v1/render` 需要在同一个路径上同时接受 JSON、表单与多部分请求，因此不能交给
框架的声明式请求体处理（那会锁定单一 Content-Type）。这里统一读成扁平字典后，
再交给 Pydantic 模型校验，保证两种入参走同一套规则。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request
from starlette.datastructures import UploadFile

from resume_pdf.core.errors import (
    ErrorDetail,
    InvalidJsonError,
    UnsupportedMediaTypeError,
    ValidationError,
)

JSON_MEDIA_TYPE = "application/json"
FORM_MEDIA_TYPES = frozenset({"application/x-www-form-urlencoded", "multipart/form-data"})

# 表单扁平键 → 嵌套 page 字段
FORM_PAGE_KEYS: dict[str, str] = {
    "page_size": "size",
    "page_width": "width",
    "page_height": "height",
    "page_unit": "unit",
    "page_orientation": "orientation",
    "page_margin": "margin",
    "page_media": "media",
    "page_print_background": "print_background",
}
FORM_TOP_LEVEL_KEYS = ("html", "file_name", "timeout_ms", "max_pages", "response_mode")


def content_type_of(request: Request) -> str:
    """返回小写的媒体类型（去掉 charset 等参数）；缺失时返回空串。"""
    raw = request.headers.get("content-type", "")
    return raw.split(";")[0].strip().lower()


async def read_render_payload(request: Request) -> dict[str, Any]:
    """按 Content-Type 读取请求体并归一为嵌套字典。"""
    media_type = content_type_of(request)
    if media_type == JSON_MEDIA_TYPE:
        return await _read_json(request)
    if media_type in FORM_MEDIA_TYPES:
        return await _read_form(request)
    shown = media_type or "未提供"
    raise UnsupportedMediaTypeError(
        f"不支持的 Content-Type：{shown}；请使用 application/json 或表单提交",
        details=[ErrorDetail(reason="supported=application/json,form", field="Content-Type")],
    )


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise InvalidJsonError(
            "请求体不是合法的 JSON",
            details=[ErrorDetail(reason="json_decode_failed", field="body")],
        ) from exc
    if not isinstance(payload, dict):
        raise InvalidJsonError(
            "请求体必须是 JSON 对象",
            details=[ErrorDetail(reason="expected_object", field="body")],
        )
    return payload


async def _read_form(request: Request) -> dict[str, Any]:
    try:
        form = await request.form()
    except Exception as exc:
        # 表单解析异常类型由框架决定，统一转成 400 契约错误。
        raise ValidationError(
            "表单请求体解析失败",
            details=[ErrorDetail(reason="form_parse_failed", field="body")],
        ) from exc

    flat: dict[str, str] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            raise ValidationError(
                f"字段 {key} 不支持文件上传",
                details=[ErrorDetail(reason="file_upload_unsupported", field=key)],
            )
        flat[key] = value
    return expand_flat_form(flat)


def expand_flat_form(flat: dict[str, str]) -> dict[str, Any]:
    """把表单扁平键还原为与 JSON 等价的嵌套结构，空串视为未提供。"""
    payload: dict[str, Any] = {}
    for key in FORM_TOP_LEVEL_KEYS:
        value = flat.get(key)
        if value is not None and value.strip() != "":
            payload[key] = value
    # 兼容旧调用方使用的 artifact_ttl_seconds 键
    ttl = flat.get("artifact_ttl_seconds")
    if ttl is not None and ttl.strip() != "":
        payload["artifact_ttl_seconds"] = ttl

    page: dict[str, str] = {}
    for flat_key, page_key in FORM_PAGE_KEYS.items():
        value = flat.get(flat_key)
        if value is not None and value.strip() != "":
            page[page_key] = value
    if page:
        payload["page"] = page
    return payload
