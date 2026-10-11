"""集成测试夹具。

浏览器实例与事件循环绑定，因此按用例创建与关闭，避免跨事件循环复用。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from tests.factories import make_settings

from resume_pdf.core.errors import EngineUnavailableError
from resume_pdf.render.chromium import ChromiumEngine


@pytest.fixture
async def browser_engine(tmp_path: Path) -> AsyncIterator[ChromiumEngine]:
    """真实浏览器引擎；未安装 Chromium 时跳过用例。"""
    engine = ChromiumEngine(make_settings(render_tmp_dir=tmp_path / "tmp"))
    try:
        await engine.start()
    except EngineUnavailableError as exc:
        pytest.skip(f"未安装 Chromium，跳过集成测试：{exc}")
    try:
        yield engine
    finally:
        await engine.close()
