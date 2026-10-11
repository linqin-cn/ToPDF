"""渲染一次 HTML 文件并打印度量信息。

用于容器内冒烟与线上排障：不经过 HTTP 层，直接验证渲染引擎是否可用。

用法：
    python scripts/render_once.py tests/fixtures/html/minimal.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader

from resume_pdf.core.config import Settings
from resume_pdf.render.chromium import ChromiumEngine
from resume_pdf.render.engine import RenderOptions
from resume_pdf.render.pagesize import resolve_page


async def run(html_path: Path, output_dir: Path, size: str | None, *, extract: bool = False) -> int:
    settings = Settings(_env_file=None, api_keys="local-cli")  # type: ignore[call-arg]
    engine = ChromiumEngine(settings)
    await engine.start()
    try:
        page = resolve_page(
            size=size,
            width=None,
            height=None,
            unit=None,
            orientation="portrait",
            margin=None,
            default_size=settings.default_page_size,
        )
        result = await engine.render(
            html_path.read_text(encoding="utf-8"),
            RenderOptions(page=page, timeout_ms=settings.render_timeout_ms),
            output_dir=output_dir,
        )
        print(
            json.dumps(
                {
                    "engine": result.engine,
                    "page_count": result.page_count,
                    "byte_size": result.byte_size,
                    "duration_ms": result.duration_ms,
                    "blocked_requests": result.blocked_requests,
                    "pdf_path": str(result.pdf_path),
                    "healthy": await engine.healthy(),
                },
                ensure_ascii=False,
            )
        )
        if extract:
            text = PdfReader(BytesIO(result.pdf_path.read_bytes())).pages[0].extract_text() or ""
            print(json.dumps({"text": text.strip()[:400]}, ensure_ascii=False))
    finally:
        await engine.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="渲染一次 HTML 并打印度量信息")
    parser.add_argument("html_file", type=Path)
    parser.add_argument("--out", type=Path, default=Path("/tmp/render-once"))
    parser.add_argument("--size", default=None, help="A4/A3/Letter/Custom 等；缺省尊重 HTML @page")
    parser.add_argument(
        "--text", action="store_true", help="额外输出首页可提取文本，用于确认中文字形与编码"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.html_file, args.out, args.size, extract=args.text))


if __name__ == "__main__":
    raise SystemExit(main())
