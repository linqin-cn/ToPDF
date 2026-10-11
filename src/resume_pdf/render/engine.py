"""渲染引擎端口。

端口刻意不提供任何可以写入或改写 HTML 的参数（没有 extra_css、没有脚本、
没有 DOM 变换回调），从类型层面保证「原样渲染」这一核心约束。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from resume_pdf.core.config import Settings
from resume_pdf.render.pagesize import MediaType, ResolvedPage


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """一次渲染的全部可用参数。"""

    page: ResolvedPage
    media: MediaType = "print"
    print_background: bool = True
    timeout_ms: int = 20_000
    max_pages: int = 5
    max_pdf_bytes: int = 20 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RenderResult:
    """渲染产物与度量信息。"""

    pdf_path: Path
    page_count: int
    byte_size: int
    duration_ms: int
    engine: str
    html_sha256: str
    blocked_requests: int


@runtime_checkable
class RenderEngine(Protocol):
    """渲染引擎契约。实现可以是 Chromium，也可以是 WeasyPrint。"""

    name: str

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def healthy(self) -> bool: ...

    async def render(
        self, html: str, options: RenderOptions, *, output_dir: Path
    ) -> RenderResult: ...

    @property
    def restart_count(self) -> int: ...


def create_engine(settings: Settings) -> RenderEngine:
    """按配置创建引擎实例。v1 只提供 Chromium 实现，端口保持不变以便后续替换。"""
    from resume_pdf.render.chromium import ChromiumEngine

    return ChromiumEngine(settings)
