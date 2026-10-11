"""测试替身：不依赖浏览器的确定性渲染引擎。"""

from __future__ import annotations

import asyncio
import hashlib
import time
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from pypdf import PdfWriter

from resume_pdf.core.config import Settings
from resume_pdf.main import create_app
from resume_pdf.render.engine import RenderOptions, RenderResult
from resume_pdf.render.service import RenderService

A4_WIDTH_PT = 595.276
A4_HEIGHT_PT = 841.89


def make_pdf(pages: int = 1) -> bytes:
    """生成真实可解析的 PDF 字节，页数可控。"""
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=A4_WIDTH_PT, height=A4_HEIGHT_PT)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class StubEngine:
    """满足 `RenderEngine` 契约的替身，用于接口层测试。"""

    name = "stub"

    def __init__(
        self,
        *,
        pages: int = 1,
        delay_seconds: float = 0.0,
        failure: Exception | None = None,
    ) -> None:
        self.pages = pages
        self.delay_seconds = delay_seconds
        self.failure = failure
        self.started = False
        self.render_calls = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def healthy(self) -> bool:
        return True

    @property
    def restart_count(self) -> int:
        return 0

    async def render(self, html: str, options: RenderOptions, *, output_dir: Path) -> RenderResult:
        started = time.perf_counter()
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.failure is not None:
            raise self.failure

        self.render_calls += 1
        content = make_pdf(self.pages)
        pdf_path = output_dir / "output.pdf"
        pdf_path.write_bytes(content)
        return RenderResult(
            pdf_path=pdf_path,
            page_count=self.pages,
            byte_size=len(content),
            duration_ms=round((time.perf_counter() - started) * 1000),
            engine=self.name,
            html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            blocked_requests=0,
        )


def build_app(settings: Settings, engine: StubEngine | None = None) -> FastAPI:
    """构建注入了替身引擎的应用实例（避免测试依赖真实 Chromium）。"""
    resolved_engine = engine or StubEngine()
    app = create_app(settings)
    app.state.engine = resolved_engine
    app.state.render_service = RenderService(engine=resolved_engine, settings=settings)
    return app
