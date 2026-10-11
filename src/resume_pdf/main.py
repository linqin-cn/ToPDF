"""应用入口：构建 FastAPI 实例并管理进程生命周期。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from resume_pdf import __version__

APP_TITLE = "Resume PDF Renderer"
APP_DESCRIPTION = (
    "将智能体提交的完整 HTML（含内联 CSS）原样渲染为简历样式 PDF。"
    "服务不修改、补全、重写或美化输入 HTML 的结构、内容与样式。"
)


def create_app() -> FastAPI:
    """创建应用实例。渲染引擎与存储的生命周期将在后续阶段接入 lifespan。"""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/healthz", tags=["ops"], summary="存活探针")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
