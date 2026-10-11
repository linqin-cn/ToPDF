"""产物存储端口。

产物是不可枚举的随机 ID 加元数据：ID 对调用方公开但不可猜测，元数据记录原始
文件名、内容类型与过期时间，供下载与清理使用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    file_id: str
    pdf_path: Path
    meta_path: Path
    byte_size: int
    expires_at: float
    file_name: str
    content_type: str
    sha256: str


@runtime_checkable
class ArtifactStore(Protocol):
    """产物读写契约。切换对象存储时只需替换实现。"""

    async def put(self, content: bytes, *, file_name: str, ttl_seconds: int) -> ArtifactRef: ...

    async def stat(self, file_id: str) -> ArtifactRef | None:
        """返回产物元数据；已过期也会返回，由调用方决定 404 还是 410。"""
        ...

    async def read(self, ref: ArtifactRef) -> bytes: ...

    async def delete(self, ref: ArtifactRef) -> None: ...

    def is_expired(self, ref: ArtifactRef) -> bool:
        """判定产物是否过期。时钟由实现持有，便于测试注入。"""
        ...

    async def sweep(self) -> int:
        """删除已过期产物，返回删除数量。"""
        ...

    async def healthy(self) -> bool: ...
