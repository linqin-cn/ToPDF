"""应用骨架与存活探针测试。"""

from __future__ import annotations

import httpx

from resume_pdf import __version__


async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["uptime_s"] >= 0


async def test_unknown_path_returns_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/not-exists")

    assert response.status_code == 404
