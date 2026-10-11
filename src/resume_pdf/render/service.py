"""渲染编排：准入控制、并发闸门、超时、产物限额与临时目录生命周期。"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import anyio.to_thread

from resume_pdf.core.config import Settings
from resume_pdf.core.errors import (
    OutputTooLargeError,
    RateLimitedError,
    RenderTimeoutError,
    ServiceBusyError,
    TooManyPagesError,
)
from resume_pdf.core.logging import get_logger
from resume_pdf.render.engine import RenderEngine, RenderOptions

logger = get_logger("resume_pdf.render")

RETRY_AFTER_SECONDS = 1


@dataclass(frozen=True, slots=True)
class RenderedPdf:
    """渲染结果：产物字节与度量信息。"""

    content: bytes
    page_count: int
    duration_ms: int
    engine: str
    html_sha256: str
    blocked_requests: int


class RenderService:
    """把一次渲染请求收敛为「准入 → 排队 → 渲染 → 限额校验 → 清理」。"""

    def __init__(self, *, engine: RenderEngine, settings: Settings) -> None:
        self._engine = engine
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_renders)
        self._queued = 0
        self._inflight = 0

    @property
    def engine(self) -> RenderEngine:
        return self._engine

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def queue_depth(self) -> int:
        return self._queued

    async def render(self, html: str, options: RenderOptions) -> RenderedPdf:
        budget_seconds = options.timeout_ms / 1000
        started = time.perf_counter()

        await self._acquire_slot(options.timeout_ms)
        self._inflight += 1
        try:
            remaining = budget_seconds - (time.perf_counter() - started)
            if remaining <= 0:
                raise RenderTimeoutError("渲染超过超时上限")
            return await self._render_in_temp_dir(html, options, remaining)
        finally:
            self._inflight -= 1
            self._semaphore.release()

    async def _acquire_slot(self, timeout_ms: int) -> None:
        budget_seconds = timeout_ms / 1000
        # 只有在确实需要排队时才计入队列深度：空闲时立即准入，不受队列上限影响。
        needs_wait = self._inflight >= self._settings.max_concurrent_renders
        if needs_wait:
            if self._queued >= self._settings.max_queue_size:
                raise self._busy_error("渲染队列已满")
            self._queued += 1
        try:
            async with asyncio.timeout(budget_seconds):
                await self._semaphore.acquire()
        except TimeoutError as exc:
            raise self._busy_error("排队等待超过超时上限") from exc
        finally:
            if needs_wait:
                self._queued -= 1

    def _busy_error(self, message: str) -> Exception:
        if self._settings.queue_full_strategy == "503":
            return ServiceBusyError(message, retry_after=RETRY_AFTER_SECONDS)
        return RateLimitedError(message, retry_after=RETRY_AFTER_SECONDS)

    async def _render_in_temp_dir(
        self, html: str, options: RenderOptions, remaining_seconds: float
    ) -> RenderedPdf:
        with self._temporary_dir() as work_dir:
            try:
                async with asyncio.timeout(remaining_seconds):
                    result = await self._engine.render(html, options, output_dir=work_dir)
            except TimeoutError as exc:
                raise RenderTimeoutError("渲染超过超时上限") from exc

            self._assert_within_limits(result.page_count, result.byte_size, options)
            content = await anyio.to_thread.run_sync(result.pdf_path.read_bytes)

        return RenderedPdf(
            content=content,
            page_count=result.page_count,
            duration_ms=result.duration_ms,
            engine=result.engine,
            html_sha256=result.html_sha256,
            blocked_requests=result.blocked_requests,
        )

    def _assert_within_limits(
        self, page_count: int, byte_size: int, options: RenderOptions
    ) -> None:
        if page_count > options.max_pages:
            raise TooManyPagesError(f"输出页数 {page_count} 超过上限 {options.max_pages}")
        if byte_size > options.max_pdf_bytes:
            raise OutputTooLargeError(
                f"输出体积 {byte_size} 字节超过上限 {options.max_pdf_bytes} 字节"
            )

    @contextlib.contextmanager
    def _temporary_dir(self) -> Iterator[Path]:
        """请求级临时目录，退出时无条件删除（浏览器崩溃不影响清理）。"""
        work_dir = self._settings.render_tmp_dir / uuid4().hex
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            yield work_dir
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
            if work_dir.exists():  # pragma: no cover - 仅在文件系统异常时触发
                logger.warning("temp_dir_cleanup_failed", extra={"path": str(work_dir)})
