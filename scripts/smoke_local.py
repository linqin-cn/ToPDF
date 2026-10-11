"""本地冒烟：健康检查 → 真实渲染 → 下载校验。

用法（服务需已在运行）：
    python scripts/smoke_local.py [base_url]

默认访问 http://127.0.0.1:8000，密钥取环境变量 `RPR_SMOKE_API_KEY`（默认 smoke-key）。
"""

from __future__ import annotations

import os
import sys
from io import BytesIO

import httpx
from pypdf import PdfReader

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
SMOKE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<style>
  @page { size: A4; margin: 12mm; }
  body { font-family: "Noto Sans CJK SC", sans-serif; margin: 0; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .meta { color: #555; font-size: 12px; }
  ul { margin: 10px 0 0 18px; font-size: 13px; }
</style></head>
<body>
  <h1>张三</h1>
  <p class="meta">Java 后端实习生 · 138-0000-0000 · zhangsan@example.com</p>
  <ul>
    <li>熟悉 Spring Boot / MySQL，参与订单服务重构</li>
    <li>负责接口联调与压测，P95 时延下降 40%</li>
  </ul>
</body></html>"""


def main(argv: list[str]) -> int:
    base_url = (argv[1] if len(argv) > 1 else DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("RPR_SMOKE_API_KEY", "smoke-key")
    headers = {"X-API-Key": api_key}

    with httpx.Client(base_url=base_url, timeout=60.0) as client:
        health = client.get("/healthz")
        _check(health.status_code == 200, f"/healthz -> {health.status_code} {health.text}")

        ready = client.get("/readyz")
        _check(ready.status_code == 200, f"/readyz -> {ready.status_code} {ready.text}")

        rendered = client.post(
            "/v1/render",
            json={"html": SMOKE_HTML, "file_name": "张三-简历.pdf", "page": {"margin": "12mm"}},
            headers=headers,
        )
        _check(rendered.status_code == 200, f"/v1/render -> {rendered.status_code} {rendered.text}")
        body = rendered.json()
        print(
            f"  渲染成功：{body['page_count']} 页 / "
            f"{body['size_bytes']} 字节 / {body['duration_ms']}ms"
        )

        download = client.get(body["file_url"], headers=headers)
        _check(download.status_code == 200, f"下载 -> {download.status_code} {download.text[:200]}")
        _check(download.content.startswith(b"%PDF-"), "下载内容不是 PDF")

        pages = len(PdfReader(BytesIO(download.content)).pages)
        print(f"  下载校验：{len(download.content)} 字节，{pages} 页")
        print(f"  Content-Disposition: {download.headers.get('content-disposition')}")

    print("冒烟通过")
    return 0


def _check(condition: bool, message: str) -> None:
    if not condition:
        print(f"冒烟失败：{message}")
        raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
