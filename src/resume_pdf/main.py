"""应用入口：构建 FastAPI 实例并管理进程生命周期。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from resume_pdf import __version__
from resume_pdf.api.exception_handlers import register_exception_handlers
from resume_pdf.api.middleware import RequestBodyLimitMiddleware, RequestContextMiddleware
from resume_pdf.api.routers import contract as contract_router
from resume_pdf.api.routers import render as render_router
from resume_pdf.core.config import Settings, get_settings
from resume_pdf.core.logging import configure_logging, get_logger
from resume_pdf.core.ratelimit import TokenBucketLimiter
from resume_pdf.render.engine import create_engine
from resume_pdf.render.service import RenderService
from resume_pdf.storage.local import LocalArtifactStore

APP_TITLE = "Resume PDF Renderer"
APP_DESCRIPTION = (
    "将智能体提交的完整 HTML（含内联 CSS）原样渲染为简历样式 PDF。"
    "服务不修改、补全、重写或美化输入 HTML 的结构、内容与样式。"
)

logger = get_logger("resume_pdf.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用实例：装配进程内单例、路由与生命周期。"""
    resolved = settings or get_settings()
    configure_logging(resolved)

    engine = create_engine(resolved)
    service = RenderService(engine=engine, settings=resolved)
    store = LocalArtifactStore(resolved.artifact_dir)
    limiter = TokenBucketLimiter(rpm=resolved.rate_limit_rpm, burst=resolved.rate_limit_burst)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved.workers > 1:
            logger.warning(
                "workers_gt_one",
                extra={
                    "workers": resolved.workers,
                    "hint": "并发闸门与渲染实例为进程内资源，多 worker 会成倍放大实际并发",
                },
            )
        try:
            await engine.start()
        except Exception as exc:
            # 引擎不可用时仍要提供健康检查与错误码，因此不阻断进程启动。
            # 启动失败不阻断进程：/readyz 会报告不健康，渲染请求返回 503 ENGINE_UNAVAILABLE。
            logger.error("engine_start_failed", extra={"error_type": type(exc).__name__})
        try:
            yield
        finally:
            await engine.close()

    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.state.engine = engine
    app.state.render_service = service
    app.state.artifact_store = store
    app.state.rate_limiter = limiter

    # 先添加的在内层：请求上下文（含访问日志）位于最外层，保证限流类拒绝也被记录。
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=resolved.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(render_router.router)
    app.include_router(contract_router.router)

    @app.get("/healthz", tags=["ops"], summary="存活探针")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


# 不使用模块级 app 实例：配置校验必须在启动时完成，而不是在导入时。
# 启动命令为 uvicorn resume_pdf.main:create_app --factory
