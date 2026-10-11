"""配置中心。

全部配置来自环境变量（前缀 RPR_）与可选的 .env 文件，启动时一次性校验：
类型、取值范围与跨字段约束。非法配置直接启动失败，避免运行期才发现问题。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MAX_LOG_SAMPLE_RATE = 1.0
MIN_SIGNING_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """服务配置。字段与 docs/05-deployment-ops.md 的配置表一一对应。"""

    model_config = SettingsConfigDict(
        env_prefix="RPR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # 带 validation_alias 的字段也要允许按字段名传参（代码与测试更直观）。
        populate_by_name=True,
    )

    host: str = "0.0.0.0"  # noqa: S104 - 容器内需要监听所有网卡
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=32)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    log_sample_rate: float = Field(default=1.0, ge=0.0, le=MAX_LOG_SAMPLE_RATE)

    auth_enabled: bool = True
    api_keys: str = ""
    rate_limit_rpm: int = Field(default=60, ge=1)
    rate_limit_burst: int = Field(default=10, ge=1)
    max_concurrent_renders: int = Field(default=4, ge=1, le=64)
    max_queue_size: int = Field(default=8, ge=0, le=1024)
    queue_full_strategy: Literal["429", "503"] = "429"

    max_request_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    max_html_bytes: int = Field(default=2 * 1024 * 1024, ge=1024)
    hard_max_html_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)

    render_engine: Literal["chromium"] = "chromium"
    render_timeout_ms: int = Field(default=20_000, ge=1_000)
    render_max_timeout_ms: int = Field(default=30_000, ge=1_000)
    browser_recycle_after: int = Field(default=200, ge=1)
    render_tmp_dir: Path = Path("/tmp/rpr")  # noqa: S108 - 容器内固定临时目录
    default_page_size: str = "A4"
    max_pages: int = Field(default=5, ge=1)
    hard_max_pages: int = Field(default=100, ge=1)
    max_pdf_bytes: int = Field(default=20 * 1024 * 1024, ge=1024)

    artifact_dir: Path = Field(
        default=Path("/data/artifacts"),
        validation_alias=AliasChoices("RPR_ARTIFACT_DIR", "OUTPUT_DIR"),
    )
    artifact_ttl_seconds: int = Field(default=3600, ge=60)
    hard_max_ttl_seconds: int = Field(default=604_800, ge=60)
    cleanup_interval_seconds: int = Field(default=300, ge=10)
    cleanup_leader_only: bool = False

    public_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("RPR_PUBLIC_BASE_URL", "PUBLIC_BASE_URL"),
    )
    download_url_mode: Literal["key", "signed"] = "key"
    signing_secret: str = ""
    response_mode: Literal["url", "binary"] = "url"

    metrics_enabled: bool = True
    metrics_token: str = ""

    tracing_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "resume-pdf-renderer"

    # 契约文件目录：相对路径按仓库根目录解析，绝对路径原样使用。
    openapi_spec_dir: Path = Path("docs")

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.max_html_bytes > self.hard_max_html_bytes:
            raise ValueError("MAX_HTML_BYTES 不得超过 HARD_MAX_HTML_BYTES")
        if self.max_request_bytes < self.max_html_bytes:
            raise ValueError("MAX_REQUEST_BYTES 不得小于 MAX_HTML_BYTES")
        if self.render_timeout_ms > self.render_max_timeout_ms:
            raise ValueError("RENDER_TIMEOUT_MS 不得超过 RENDER_MAX_TIMEOUT_MS")
        if self.max_pages > self.hard_max_pages:
            raise ValueError("MAX_PAGES 不得超过 HARD_MAX_PAGES")
        if self.artifact_ttl_seconds > self.hard_max_ttl_seconds:
            raise ValueError("ARTIFACT_TTL_SECONDS 不得超过 HARD_MAX_TTL_SECONDS")
        if self.auth_enabled and not self.api_key_set:
            raise ValueError(
                "AUTH_ENABLED=true 时必须配置 API_KEYS（纯内网部署可显式设置 "
                "RPR_AUTH_ENABLED=false 关闭鉴权）"
            )
        secret_too_short = len(self.signing_secret) < MIN_SIGNING_SECRET_LENGTH
        if self.download_url_mode == "signed" and secret_too_short:
            raise ValueError(
                "DOWNLOAD_URL_MODE=signed 时 SIGNING_SECRET 至少需要 "
                f"{MIN_SIGNING_SECRET_LENGTH} 个字符"
            )
        return self

    @property
    def api_key_set(self) -> frozenset[str]:
        """解析逗号分隔的密钥列表，忽略空白项。"""
        return frozenset(key.strip() for key in self.api_keys.split(",") if key.strip())

    @property
    def metrics_protected(self) -> bool:
        return bool(self.metrics_token)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程内唯一的配置实例。测试可用 get_settings.cache_clear() 重置。"""
    return Settings()
