"""产物 TTL 清理后台任务。"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from resume_pdf.core.logging import get_logger
from resume_pdf.storage.base import ArtifactStore

logger = get_logger("resume_pdf.storage")


def start_cleanup_task(store: ArtifactStore, interval_seconds: int) -> asyncio.Task[None]:
    """启动周期清理任务：先立即清一次历史遗留，之后按间隔执行。"""
    return asyncio.create_task(_cleanup_loop(store, interval_seconds))


async def _cleanup_loop(store: ArtifactStore, interval_seconds: int) -> None:
    while True:
        try:
            removed = await store.sweep()
            if removed:
                logger.info("artifacts_swept", extra={"removed": removed})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # 清理失败不影响服务，记录后等下一轮重试。
            logger.error("artifacts_sweep_failed", extra={"error_type": type(exc).__name__})
        await asyncio.sleep(interval_seconds)


@contextlib.asynccontextmanager
async def cleanup_task(store: ArtifactStore, interval_seconds: int) -> AsyncIterator[None]:
    """在应用生命周期内运行清理任务，退出时取消并等待收尾。"""
    task = start_cleanup_task(store, interval_seconds)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
