"""渲染编排测试：使用假引擎覆盖准入、超时、限额与临时目录清理。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tests.factories import make_settings

from resume_pdf.core.errors import (
    ErrorCode,
    OutputTooLargeError,
    RateLimitedError,
    RenderTimeoutError,
    ServiceBusyError,
    TooManyPagesError,
)
from resume_pdf.render.engine import RenderOptions, RenderResult
from resume_pdf.render.pagesize import MarginBox, ResolvedPage
from resume_pdf.render.service import RenderService


class FakeEngine:
    """可编程的渲染引擎替身，不依赖浏览器。"""

    name = "fake"

    def __init__(
        self,
        *,
        delay_seconds: float = 0.0,
        page_count: int = 1,
        byte_size: int = 64,
        work_dir_tracker: list[Path] | None = None,
    ) -> None:
        self.delay_seconds = delay_seconds
        self.page_count = page_count
        self.byte_size = byte_size
        self.work_dir_tracker = work_dir_tracker if work_dir_tracker is not None else []
        self.restart_count = 0
        self.peak_concurrency = 0
        self._active = 0

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def healthy(self) -> bool:
        return True

    async def render(self, html: str, options: RenderOptions, *, output_dir: Path) -> RenderResult:
        self._active += 1
        self.peak_concurrency = max(self.peak_concurrency, self._active)
        try:
            if self.delay_seconds:
                await asyncio.sleep(self.delay_seconds)
            self.work_dir_tracker.append(output_dir)
            payload = b"%PDF-1.4\n" + b"0" * self.byte_size
            pdf_path = output_dir / "output.pdf"
            pdf_path.write_bytes(payload)
            return RenderResult(
                pdf_path=pdf_path,
                page_count=self.page_count,
                byte_size=len(payload),
                duration_ms=int(self.delay_seconds * 1000),
                engine=self.name,
                html_sha256="0" * 64,
                blocked_requests=0,
            )
        finally:
            self._active -= 1


def make_options(**overrides: object) -> RenderOptions:
    page = ResolvedPage(
        width_mm=210.0,
        height_mm=297.0,
        margin=MarginBox(),
        prefer_css_page_size=True,
        fallback_format="A4",
    )
    params: dict[str, object] = {
        "page": page,
        "timeout_ms": 2000,
        "max_pages": 5,
        "max_pdf_bytes": 1024,
    }
    params.update(overrides)
    return RenderOptions(**params)  # type: ignore[arg-type]


async def test_render_returns_content_and_cleans_temp_dir(tmp_path: Path) -> None:
    engine = FakeEngine()
    service = RenderService(engine=engine, settings=make_settings(render_tmp_dir=tmp_path))

    result = await service.render("<h1>hi</h1>", make_options())

    assert result.content.startswith(b"%PDF-1.4")
    assert result.page_count == 1
    assert engine.work_dir_tracker and not engine.work_dir_tracker[0].exists()
    assert list(tmp_path.iterdir()) == []


async def test_temp_dir_is_cleaned_even_when_render_fails(tmp_path: Path) -> None:
    class FailingEngine(FakeEngine):
        async def render(
            self, html: str, options: RenderOptions, *, output_dir: Path
        ) -> RenderResult:
            output_dir.joinpath("partial.tmp").write_bytes(b"x")
            raise TooManyPagesError("boom")

    service = RenderService(engine=FailingEngine(), settings=make_settings(render_tmp_dir=tmp_path))

    with pytest.raises(TooManyPagesError):
        await service.render("<h1>hi</h1>", make_options())

    assert list(tmp_path.iterdir()) == []


async def test_page_limit_is_enforced(tmp_path: Path) -> None:
    service = RenderService(
        engine=FakeEngine(page_count=9), settings=make_settings(render_tmp_dir=tmp_path)
    )

    with pytest.raises(TooManyPagesError) as exc_info:
        await service.render("<h1>hi</h1>", make_options(max_pages=5))

    assert exc_info.value.code == ErrorCode.PDF_TOO_MANY_PAGES
    assert exc_info.value.status == 422


async def test_pdf_size_limit_is_enforced(tmp_path: Path) -> None:
    service = RenderService(
        engine=FakeEngine(byte_size=4096), settings=make_settings(render_tmp_dir=tmp_path)
    )

    with pytest.raises(OutputTooLargeError):
        await service.render("<h1>hi</h1>", make_options(max_pdf_bytes=1024))


async def test_render_timeout_maps_to_504(tmp_path: Path) -> None:
    service = RenderService(
        engine=FakeEngine(delay_seconds=0.5),
        settings=make_settings(render_tmp_dir=tmp_path),
    )

    with pytest.raises(RenderTimeoutError) as exc_info:
        await service.render("<h1>hi</h1>", make_options(timeout_ms=200))

    assert exc_info.value.code == ErrorCode.RENDER_TIMEOUT
    assert exc_info.value.status == 504
    assert list(tmp_path.iterdir()) == []


async def test_queue_full_fails_fast_with_429(tmp_path: Path) -> None:
    service = RenderService(
        engine=FakeEngine(delay_seconds=0.3),
        settings=make_settings(render_tmp_dir=tmp_path, max_concurrent_renders=1, max_queue_size=0),
    )
    first = asyncio.create_task(service.render("<h1>1</h1>", make_options(timeout_ms=3000)))
    await asyncio.sleep(0.05)

    with pytest.raises(RateLimitedError) as exc_info:
        await service.render("<h1>2</h1>", make_options(timeout_ms=3000))

    assert exc_info.value.retry_after == 1
    await first


async def test_queue_full_returns_503_when_configured(tmp_path: Path) -> None:
    service = RenderService(
        engine=FakeEngine(delay_seconds=0.3),
        settings=make_settings(
            render_tmp_dir=tmp_path,
            max_concurrent_renders=1,
            max_queue_size=0,
            queue_full_strategy="503",
        ),
    )
    first = asyncio.create_task(service.render("<h1>1</h1>", make_options(timeout_ms=3000)))
    await asyncio.sleep(0.05)

    with pytest.raises(ServiceBusyError):
        await service.render("<h1>2</h1>", make_options(timeout_ms=3000))

    await first


async def test_concurrency_gate_serializes_renders(tmp_path: Path) -> None:
    engine = FakeEngine(delay_seconds=0.1)
    service = RenderService(
        engine=engine,
        settings=make_settings(render_tmp_dir=tmp_path, max_concurrent_renders=1, max_queue_size=4),
    )

    await asyncio.gather(
        service.render("<h1>1</h1>", make_options(timeout_ms=5000)),
        service.render("<h1>2</h1>", make_options(timeout_ms=5000)),
        service.render("<h1>3</h1>", make_options(timeout_ms=5000)),
    )

    assert engine.peak_concurrency == 1
    assert service.inflight == 0
