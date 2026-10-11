"""测试对象构造器。"""

from __future__ import annotations

from typing import Any

from resume_pdf.core.config import Settings


def make_settings(**overrides: object) -> Settings:
    """构造测试配置，避免读取本机 .env 与真实环境变量。"""
    base: dict[str, Any] = {"api_keys": "test-key"}
    base.update(overrides)
    # pydantic-settings 支持 _env_file=None 关闭 .env 读取，但类型存根未声明该参数。
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]
