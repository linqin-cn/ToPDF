"""本地产物存储测试：写入、读取、过期判定与清理。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resume_pdf.storage.local import LocalArtifactStore

PDF_BYTES = b"%PDF-1.7\n%%EOF\n"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "artifacts", clock=clock)


async def test_put_writes_pdf_and_metadata(store: LocalArtifactStore, clock: FakeClock) -> None:
    ref = await store.put(PDF_BYTES, file_name="张三-简历.pdf", ttl_seconds=3600)

    assert ref.byte_size == len(PDF_BYTES)
    assert ref.expires_at == clock.now + 3600
    assert ref.pdf_path.read_bytes() == PDF_BYTES
    assert ref.pdf_path.parent == ref.meta_path.parent

    meta = json.loads(ref.meta_path.read_text(encoding="utf-8"))
    assert meta["file_name"] == "张三-简历.pdf"
    assert meta["content_type"] == "application/pdf"
    assert meta["sha256"] == ref.sha256


async def test_stat_read_and_delete(store: LocalArtifactStore) -> None:
    ref = await store.put(PDF_BYTES, file_name="resume.pdf", ttl_seconds=60)

    found = await store.stat(ref.file_id)
    assert found is not None
    assert found.file_name == "resume.pdf"
    assert await store.read(found) == PDF_BYTES

    await store.delete(found)
    assert await store.stat(ref.file_id) is None
    assert not ref.pdf_path.exists()
    assert not ref.meta_path.exists()


@pytest.mark.parametrize(
    "file_id",
    ["", "short", "x" * 23, "bad/id" + "a" * 16, "a" * 22 + "\n"],
)
async def test_stat_rejects_malformed_ids(store: LocalArtifactStore, file_id: str) -> None:
    assert await store.stat(file_id) is None


async def test_expiration_follows_injected_clock(
    store: LocalArtifactStore, clock: FakeClock
) -> None:
    ref = await store.put(PDF_BYTES, file_name="resume.pdf", ttl_seconds=60)

    assert store.is_expired(ref) is False
    clock.advance(59)
    assert store.is_expired(ref) is False
    clock.advance(61)
    assert store.is_expired(ref) is True


async def test_sweep_removes_only_expired_artifacts(
    store: LocalArtifactStore, clock: FakeClock
) -> None:
    fresh = await store.put(PDF_BYTES, file_name="fresh.pdf", ttl_seconds=3600)
    stale = await store.put(PDF_BYTES, file_name="stale.pdf", ttl_seconds=60)

    clock.advance(120)

    assert await store.sweep() == 1
    assert (await store.stat(fresh.file_id)) is not None
    assert await store.stat(stale.file_id) is None


async def test_sweep_on_missing_root_is_noop(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "not-created-yet")

    assert await store.sweep() == 0


async def test_healthy_probes_write_permission(store: LocalArtifactStore) -> None:
    assert await store.healthy() is True
    assert not (store.root / ".health").exists()


async def test_artifacts_are_grouped_by_utc_date(
    store: LocalArtifactStore, clock: FakeClock
) -> None:
    first = await store.put(PDF_BYTES, file_name="a.pdf", ttl_seconds=60)
    clock.advance(86_400)
    second = await store.put(PDF_BYTES, file_name="b.pdf", ttl_seconds=60)

    assert first.pdf_path.parent != second.pdf_path.parent
    assert first.pdf_path.parent.name != second.pdf_path.parent.name
