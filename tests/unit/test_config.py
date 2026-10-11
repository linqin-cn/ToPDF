"""配置中心测试：默认值、别名兼容与跨字段约束。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from resume_pdf.core.config import Settings


def build(**overrides: object) -> Settings:
    base: dict[str, Any] = {"api_keys": "k1"}
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def test_defaults_are_safe_and_consistent() -> None:
    settings = build()

    assert settings.auth_enabled is True
    assert settings.max_concurrent_renders == 4
    assert settings.max_queue_size == 8
    assert settings.render_timeout_ms == 20_000
    assert settings.render_max_timeout_ms == 30_000
    assert settings.render_engine == "chromium"
    assert settings.max_pages == 5
    assert settings.response_mode == "url"
    assert settings.artifact_ttl_seconds == 3600


def test_api_key_set_ignores_blank_entries() -> None:
    settings = build(api_keys=" k1 , ,k2 ,")

    assert settings.api_key_set == frozenset({"k1", "k2"})


def test_legacy_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", "/legacy/output")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://legacy.example.com")

    settings = Settings(_env_file=None, api_keys="k1")  # type: ignore[call-arg]

    assert settings.artifact_dir == Path("/legacy/output")
    assert settings.public_base_url == "https://legacy.example.com"


def test_prefixed_env_wins_over_legacy_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_DIR", "/legacy/output")
    monkeypatch.setenv("RPR_ARTIFACT_DIR", "/new/output")

    settings = Settings(_env_file=None, api_keys="k1")  # type: ignore[call-arg]

    assert settings.artifact_dir == Path("/new/output")


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"max_html_bytes": 2048, "hard_max_html_bytes": 1024}, "HARD_MAX_HTML_BYTES"),
        ({"max_request_bytes": 1024, "max_html_bytes": 2048}, "MAX_REQUEST_BYTES"),
        ({"render_timeout_ms": 40_000, "render_max_timeout_ms": 30_000}, "RENDER_MAX_TIMEOUT_MS"),
        ({"max_pages": 20, "hard_max_pages": 10}, "HARD_MAX_PAGES"),
        ({"artifact_ttl_seconds": 200_000, "hard_max_ttl_seconds": 100_000}, "HARD_MAX_TTL"),
        ({"auth_enabled": True, "api_keys": ""}, "API_KEYS"),
        ({"download_url_mode": "signed", "signing_secret": "short"}, "SIGNING_SECRET"),
    ],
)
def test_invalid_combinations_fail_fast(overrides: dict[str, object], expected: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        build(**overrides)

    assert expected in str(exc_info.value)


def test_signed_mode_accepts_long_enough_secret() -> None:
    settings = build(download_url_mode="signed", signing_secret="x" * 32)

    assert settings.metrics_protected is False
