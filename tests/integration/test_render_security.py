"""渲染期安全用例：零网络、禁读本地文件、不执行脚本。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader

from resume_pdf.render.chromium import ChromiumEngine
from resume_pdf.render.engine import RenderOptions
from resume_pdf.render.pagesize import MarginBox, ResolvedPage

pytestmark = pytest.mark.integration


def options() -> RenderOptions:
    return RenderOptions(
        page=ResolvedPage(
            width_mm=210.0,
            height_mm=297.0,
            margin=MarginBox(),
            prefer_css_page_size=True,
            fallback_format="A4",
        ),
        timeout_ms=20_000,
    )


def extract_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


@pytest.mark.parametrize(
    "resource",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/healthz",
        "https://fonts.googleapis.com/css2?family=Noto+Sans",
    ],
)
async def test_network_resources_are_blocked(
    browser_engine: ChromiumEngine, tmp_path: Path, resource: str
) -> None:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<link rel='stylesheet' href='{resource}'>"
        "</head><body>"
        f"<img src='{resource}' alt='x'>"
        f"<iframe src='{resource}'></iframe>"
        "<p>BLOCKED-TEST</p></body></html>"
    )

    result = await browser_engine.render(html, options(), output_dir=tmp_path)

    assert result.page_count >= 1
    assert result.blocked_requests >= 1
    assert "BLOCKED-TEST" in extract_text(result.pdf_path.read_bytes())


async def test_local_file_is_never_read(browser_engine: ChromiumEngine, tmp_path: Path) -> None:
    """本地文件引用必须无效：产物中不得出现被引用文件的内容。"""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-MARKER", encoding="utf-8")
    uri = secret.as_uri()
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<link rel='stylesheet' href='{uri}'>"
        "</head><body>"
        f"<img src='{uri}' alt='x'>"
        f"<iframe src='{uri}'></iframe>"
        f"<object data='{uri}'></object>"
        "<p>SAFE-TEXT</p></body></html>"
    )

    result = await browser_engine.render(html, options(), output_dir=tmp_path / "out")
    text = extract_text(result.pdf_path.read_bytes())

    assert result.page_count >= 1
    assert "TOP-SECRET-MARKER" not in text
    assert "SAFE-TEXT" in text


async def test_javascript_never_executes(browser_engine: ChromiumEngine, tmp_path: Path) -> None:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        "<p>SAFE-CONTENT</p>"
        "<script>document.body.innerHTML = 'HACKED-CONTENT';</script>"
        "</body></html>"
    )

    result = await browser_engine.render(html, options(), output_dir=tmp_path)
    text = extract_text(result.pdf_path.read_bytes())

    assert "SAFE-CONTENT" in text
    assert "HACKED-CONTENT" not in text


async def test_document_body_is_not_injected(
    browser_engine: ChromiumEngine, tmp_path: Path
) -> None:
    """服务端不得向文档注入任何节点：只用 route 返回原始字节。"""
    html = "<!doctype html><html><body><p>ORIGINAL</p></body></html>"

    result = await browser_engine.render(html, options(), output_dir=tmp_path)
    text = extract_text(result.pdf_path.read_bytes())

    assert "ORIGINAL" in text
    assert text.strip().count("ORIGINAL") == 1
