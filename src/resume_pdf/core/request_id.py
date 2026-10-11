"""链路标识：生成、校验与上下文传递。"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from uuid import uuid4

REQUEST_ID_HEADER = "X-Request-Id"
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UNKNOWN = "-"

_request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    """生成服务端链路 ID。"""
    return uuid4().hex


def normalize_request_id(value: str | None) -> str | None:
    """校验调用方传入的链路 ID；不合法时返回 None，由服务端生成。"""
    if value and REQUEST_ID_PATTERN.match(value):
        return value
    return None


def set_request_id(value: str) -> Token[str | None]:
    return _request_id_var.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_var.reset(token)


def get_request_id() -> str:
    """当前上下文中的链路 ID，缺失时返回占位符，保证日志字段形状稳定。"""
    return _request_id_var.get() or _UNKNOWN
