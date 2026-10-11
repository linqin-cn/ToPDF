"""基于 Chromium（Playwright）的渲染实现。

安全与保真设计：

1. 页面通过 goto 一个保留域名装载，全部请求经 route 拦截：只有该域名的文档请求
   返回调用方提交的 HTML（字节原样），其余请求一律 abort。这既保证 HTML 不被改写，
   又切断了网络出口。
2. 上下文关闭 JavaScript、阻断 Service Worker，并叠加 set_offline 双保险。
3. 页面尺寸、边距与媒体类型全部经 page.pdf 与 emulate_media 下达，不注入样式，
   也不执行任何 DOM 变更。
4. 渲染失败或超时若触及浏览器进程，标记为不可信并重建实例。
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import Any, Final

import anyio.to_thread
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pypdf import PdfReader

from resume_pdf.core.config import Settings
from resume_pdf.core.errors import (
    EngineUnavailableError,
    RenderFailedError,
    RenderTimeoutError,
)
from resume_pdf.core.logging import get_logger
from resume_pdf.render.engine import RenderOptions, RenderResult

logger = get_logger("resume_pdf.render")

RESERVED_ORIGIN: Final = "https://resume.local"
DOCUMENT_URL: Final = f"{RESERVED_ORIGIN}/document.html"

CHROMIUM_ARGS: Final[tuple[str, ...]] = (
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-sync",
    "--disable-translate",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--mute-audio",
    "--hide-scrollbars",
    "--font-render-hinting=none",
)


class ChromiumEngine:
    """常驻浏览器实例，每次渲染使用独立上下文。"""

    name = "chromium"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()
        self._render_count = 0
        self._restarts = 0

    @property
    def restart_count(self) -> int:
        return self._restarts

    async def start(self) -> None:
        async with self._lock:
            await self._ensure_browser_locked()

    async def close(self) -> None:
        async with self._lock:
            browser, self._browser = self._browser, None
            playwright, self._playwright = self._playwright, None
            if browser is not None:
                with suppress(Exception):
                    await browser.close()
            if playwright is not None:
                with suppress(Exception):
                    await playwright.stop()

    async def healthy(self) -> bool:
        browser = self._browser
        return browser is not None and browser.is_connected()

    async def render(self, html: str, options: RenderOptions, *, output_dir: Path) -> RenderResult:
        started = time.perf_counter()
        browser = await self._acquire_browser()
        context: BrowserContext | None = None
        blocked = 0

        try:
            context = await browser.new_context(
                java_script_enabled=False,
                service_workers="block",
                accept_downloads=False,
                offline=True,
                device_scale_factor=1,
                viewport={"width": 1280, "height": 1024},
            )
            blocked_counter = await self._install_route_guard(context, html)
            page = await context.new_page()
            page.set_default_timeout(options.timeout_ms)

            await page.goto(DOCUMENT_URL, wait_until="load", timeout=options.timeout_ms)
            await page.emulate_media(media=options.media)
            pdf_bytes = await self._print_to_pdf(page, options)
            blocked = blocked_counter[0]
        except PlaywrightTimeoutError as exc:
            await self._invalidate_browser(browser, cause="timeout")
            raise RenderTimeoutError("渲染超过超时上限") from exc
        except Exception as exc:
            await self._invalidate_browser(browser, cause="render_error")
            raise RenderFailedError("PDF 渲染失败") from exc
        finally:
            if context is not None:
                with suppress(Exception):
                    await context.close()
            self._render_count += 1

        page_count = await anyio.to_thread.run_sync(_read_page_count, pdf_bytes)

        pdf_path = output_dir / "output.pdf"
        await anyio.to_thread.run_sync(_write_pdf, pdf_path, pdf_bytes)

        return RenderResult(
            pdf_path=pdf_path,
            page_count=page_count,
            byte_size=len(pdf_bytes),
            duration_ms=round((time.perf_counter() - started) * 1000),
            engine=self.name,
            html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest(),
            blocked_requests=blocked,
        )

    async def _print_to_pdf(self, page: Page, options: RenderOptions) -> bytes:
        """按页面参数调用 printToPDF，这是唯一影响版面的入口，且不改动 HTML。"""
        print_options: dict[str, Any] = {
            "print_background": options.print_background,
            "prefer_css_page_size": options.page.prefer_css_page_size,
        }
        if options.page.prefer_css_page_size:
            # HTML 自带 @page 时以它为准；没有 @page 时回退到 default_page_size。
            print_options["format"] = options.page.fallback_format or "A4"
        else:
            width, height = options.page.as_css_size()
            print_options["width"] = width
            print_options["height"] = height
        if not options.page.margin.is_zero:
            print_options["margin"] = options.page.margin.as_css()

        return await page.pdf(**print_options)

    async def _install_route_guard(self, context: BrowserContext, html: str) -> list[int]:
        """拦截全部请求：只放行保留域名的文档请求，其余一律阻断。"""
        payload = html.encode("utf-8")
        counter = [0]

        async def _handler(route: Route) -> None:
            request = route.request
            is_document = request.resource_type == "document"
            if is_document and request.url.startswith(RESERVED_ORIGIN):
                await route.fulfill(
                    status=200,
                    headers={"content-type": "text/html; charset=utf-8"},
                    body=payload,
                )
                return
            counter[0] += 1
            await route.abort()

        await context.route("**/*", _handler)
        return counter

    async def _acquire_browser(self) -> Browser:
        async with self._lock:
            if self._render_count >= self._settings.browser_recycle_after:
                logger.info("browser_recycle", extra={"rendered": self._render_count})
                await self._restart_locked(cause="recycle")
            return await self._ensure_browser_locked()

    async def _ensure_browser_locked(self) -> Browser:
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        await self._restart_locked(cause="cold_start")
        if self._browser is None:  # pragma: no cover - 重建失败时上一行已抛错
            raise EngineUnavailableError("渲染引擎不可用")
        return self._browser

    async def _restart_locked(self, *, cause: str) -> None:
        browser, self._browser = self._browser, None
        if browser is not None:
            with suppress(Exception):
                await browser.close()

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.chromium.launch(
                args=list(CHROMIUM_ARGS),
                chromium_sandbox=False,
            )
        except Exception as exc:
            logger.error("engine_restart_failed", extra={"cause": cause}, exc_info=exc)
            self._browser = None
            raise EngineUnavailableError("渲染引擎启动失败") from exc

        self._render_count = 0
        self._restarts += 1
        logger.info("engine_restarted", extra={"cause": cause, "restarts": self._restarts})

    async def _invalidate_browser(self, browser: Browser, *, cause: str) -> None:
        async with self._lock:
            if self._browser is browser:
                logger.warning("engine_invalidated", extra={"cause": cause})
                self._browser = None
                with suppress(Exception):
                    await browser.close()


def _read_page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(BytesIO(pdf_bytes)).pages)


def _write_pdf(pdf_path: Path, pdf_bytes: bytes) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)
