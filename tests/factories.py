"""测试对象构造器。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from resume_pdf.core.config import Settings

FIXTURE_HTML_DIR = Path(__file__).resolve().parent / "fixtures" / "html"
TEST_API_KEY = "test-key"


def read_fixture_html(name: str) -> str:
    """读取黄金样例 HTML。"""
    return (FIXTURE_HTML_DIR / name).read_text(encoding="utf-8")


def make_settings(**overrides: object) -> Settings:
    """构造测试配置，避免读取本机 .env 与真实环境变量。"""
    base: dict[str, Any] = {"api_keys": TEST_API_KEY}
    base.update(overrides)
    # pydantic-settings 支持 _env_file=None 关闭 .env 读取，但类型存根未声明该参数。
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]
