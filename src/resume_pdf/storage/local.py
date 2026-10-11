"""本地目录产物存储实现。"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio.to_thread

from resume_pdf.core.logging import get_logger
from resume_pdf.storage.base import ArtifactRef

logger = get_logger("resume_pdf.storage")

PDF_MEDIA_TYPE = "application/pdf"
FILE_ID_BYTES = 16
# token_urlsafe(16) 固定产出 22 个字符，长度校验可挡掉明显的伪造 ID。
FILE_ID_LENGTH = 22


class LocalArtifactStore:
    """按日期分目录存放 PDF 与同名 JSON 元数据。"""

    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._root = root
        self._clock = clock

    @property
    def root(self) -> Path:
        return self._root

    async def put(self, content: bytes, *, file_name: str, ttl_seconds: int) -> ArtifactRef:
        file_id = secrets.token_urlsafe(FILE_ID_BYTES)
        now = self._now()
        expires_at = now + ttl_seconds
        directory = self._directory_for(now)
        pdf_path = directory / f"{file_id}.pdf"
        meta_path = directory / f"{file_id}.json"
        meta: dict[str, Any] = {
            "file_id": file_id,
            "file_name": file_name,
            "content_type": PDF_MEDIA_TYPE,
            "byte_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "created_at": now,
            "expires_at": expires_at,
        }
        await anyio.to_thread.run_sync(_write_artifact, pdf_path, meta_path, content, meta)
        return ArtifactRef(
            file_id=file_id,
            pdf_path=pdf_path,
            meta_path=meta_path,
            byte_size=len(content),
            expires_at=expires_at,
            file_name=file_name,
            content_type=PDF_MEDIA_TYPE,
            sha256=meta["sha256"],
        )

    async def stat(self, file_id: str) -> ArtifactRef | None:
        if not _is_valid_file_id(file_id):
            return None
        return await anyio.to_thread.run_sync(self._stat_sync, file_id)

    async def read(self, ref: ArtifactRef) -> bytes:
        return await anyio.to_thread.run_sync(ref.pdf_path.read_bytes)

    async def delete(self, ref: ArtifactRef) -> None:
        await anyio.to_thread.run_sync(_delete_artifact, ref.pdf_path, ref.meta_path)

    def is_expired(self, ref: ArtifactRef) -> bool:
        return ref.expires_at <= self._now()

    async def sweep(self) -> int:
        return await anyio.to_thread.run_sync(self._sweep_sync)

    async def healthy(self) -> bool:
        def _probe() -> bool:
            self._root.mkdir(parents=True, exist_ok=True)
            probe = self._root / ".health"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return True

        try:
            return await anyio.to_thread.run_sync(_probe)
        except OSError as exc:
            logger.error("storage_unhealthy", extra={"error": str(exc)})
            return False

    def _stat_sync(self, file_id: str) -> ArtifactRef | None:
        for directory in sorted(self._root.glob("*"), reverse=True):
            meta_path = directory / f"{file_id}.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("artifact_meta_unreadable", extra={"file_id": file_id})
                return None
            return _to_ref(directory, meta)
        return None

    def _sweep_sync(self) -> int:
        if not self._root.exists():
            return 0
        now = self._now()
        removed = 0
        for directory in sorted(self._root.glob("*")):
            if not directory.is_dir():
                continue
            for meta_path in directory.glob("*.json"):
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    meta_path.unlink(missing_ok=True)
                    continue
                if float(meta.get("expires_at", 0)) > now:
                    continue
                _delete_artifact(directory / f"{meta_path.stem}.pdf", meta_path)
                removed += 1
        return removed

    def _directory_for(self, timestamp: float) -> Path:
        return self._root / time.strftime("%Y%m%d", time.gmtime(timestamp))

    def _now(self) -> float:
        return float(self._clock())


def _to_ref(directory: Path, meta: dict[str, Any]) -> ArtifactRef:
    file_id = str(meta["file_id"])
    return ArtifactRef(
        file_id=file_id,
        pdf_path=directory / f"{file_id}.pdf",
        meta_path=directory / f"{file_id}.json",
        byte_size=int(meta.get("byte_size", 0)),
        expires_at=float(meta.get("expires_at", 0.0)),
        file_name=str(meta.get("file_name", "resume.pdf")),
        content_type=str(meta.get("content_type", PDF_MEDIA_TYPE)),
        sha256=str(meta.get("sha256", "")),
    )


def _write_artifact(pdf_path: Path, meta_path: Path, content: bytes, meta: dict[str, Any]) -> None:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(content)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _delete_artifact(pdf_path: Path, meta_path: Path) -> None:
    pdf_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def _is_valid_file_id(file_id: str) -> bool:
    return len(file_id) == FILE_ID_LENGTH and all(
        char.isalnum() or char in "-_" for char in file_id
    )
