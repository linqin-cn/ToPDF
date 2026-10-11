"""Chromium 渲染引擎的端到端行为（需要真实浏览器）。"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from tests.factories import read_fixture_html

from resume_pdf.render.chromium import ChromiumEngine
from resume_pdf.render.engine import RenderOptions
from resume_pdf.render.pagesize import MarginBox, ResolvedPage

pytestmark = pytest.mark.integration

MM_PER_POINT = 25.4 / 72
# Chromium 的 printToPDF 会按内部单位取整，页面尺寸存在约 0.3mm 的偏差。
SIZE_TOLERANCE_MM = 0.5


def page_options(
    *,
    prefer_css_page_size: bool = True,
    width_mm: float = 210.0,
    height_mm: float = 297.0,
    margin: MarginBox | None = None,
    print_background: bool = True,
) -> RenderOptions:
    page = ResolvedPage(
        width_mm=width_mm,
        height_mm=height_mm,
        margin=margin or MarginBox(),
        prefer_css_page_size=prefer_css_page_size,
        fallback_format="A4" if prefer_css_page_size else None,
    )
    return RenderOptions(page=page, print_background=print_background, timeout_ms=20_000)


def pdf_page_size_mm(content: bytes) -> tuple[float, float]:
    page = PdfReader(BytesIO(content)).pages[0]
    box = page.mediabox
    return float(box.width) * MM_PER_POINT, float(box.height) * MM_PER_POINT


def assert_page_size(content: bytes, expected_width_mm: float, expected_height_mm: float) -> None:
    width_mm, height_mm = pdf_page_size_mm(content)
    assert width_mm == pytest.approx(expected_width_mm, abs=SIZE_TOLERANCE_MM)
    assert height_mm == pytest.approx(expected_height_mm, abs=SIZE_TOLERANCE_MM)


def extract_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


async def test_renders_minimal_html_without_mutating_it(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    html = read_fixture_html("minimal.html")

    result = await browser_engine.render(html, page_options(), output_dir=tmp_path)

    assert result.pdf_path.read_bytes().startswith(b"%PDF-")
    assert result.page_count == 1
    assert result.engine == "chromium"
    # 渲染前后 HTML 字节一致，证明服务端没有改写输入
    assert result.html_sha256 == sha256(html.encode("utf-8")).hexdigest()
    assert "SAFE" in extract_text(result.pdf_path.read_bytes())


async def test_html_page_rule_wins_when_no_page_parameters(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    html = read_fixture_html("atpage.html")

    result = await browser_engine.render(html, page_options(), output_dir=tmp_path)

    assert_page_size(result.pdf_path.read_bytes(), 100.0, 200.0)


async def test_explicit_page_parameters_override_html_page_rule(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    html = read_fixture_html("atpage.html")

    result = await browser_engine.render(
        html,
        page_options(prefer_css_page_size=False, width_mm=210.0, height_mm=297.0),
        output_dir=tmp_path,
    )

    assert_page_size(result.pdf_path.read_bytes(), 210.0, 297.0)


async def test_margin_is_applied_via_engine_options(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    html = read_fixture_html("minimal.html")

    result = await browser_engine.render(
        html,
        page_options(prefer_css_page_size=False, margin=MarginBox(20.0, 20.0, 20.0, 20.0)),
        output_dir=tmp_path,
    )

    assert_page_size(result.pdf_path.read_bytes(), 210.0, 297.0)
    assert result.page_count == 1


async def test_print_background_changes_output(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    html = read_fixture_html("background.html")

    with_background = await browser_engine.render(
        html, page_options(print_background=True), output_dir=tmp_path / "with"
    )
    without_background = await browser_engine.render(
        html, page_options(print_background=False), output_dir=tmp_path / "without"
    )

    assert with_background.pdf_path.read_bytes() != without_background.pdf_path.read_bytes()


async def test_engine_reports_health(browser_engine: ChromiumEngine) -> None:
    assert await browser_engine.healthy() is True
