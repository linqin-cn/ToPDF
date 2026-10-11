"""领域异常与对外错误码。

对外只暴露错误码枚举中的值，内部异常的类型、堆栈与原始消息一律不返回给调用方，
只落日志。错误码是封闭集合，新增必须同步 docs/03-api-contract.md 与 OpenAPI 契约。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar


class ErrorCode(StrEnum):
    """对外错误码，取值与契约保持一致。"""

    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_JSON = "INVALID_JSON"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_BUSY = "SERVICE_BUSY"
    RENDER_TIMEOUT = "RENDER_TIMEOUT"
    RENDER_FAILED = "RENDER_FAILED"
    PDF_TOO_MANY_PAGES = "PDF_TOO_MANY_PAGES"
    RENDER_OUTPUT_TOO_LARGE = "RENDER_OUTPUT_TOO_LARGE"
    NOT_FOUND = "NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_EXPIRED = "FILE_EXPIRED"
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    """字段级错误细节，用于说明参数为什么不合法。"""

    reason: str
    field: str | None = None

    def to_dict(self) -> dict[str, str]:
        detail = {"reason": self.reason}
        if self.field is not None:
            detail["field"] = self.field
        return detail


class AppError(Exception):
    """所有可预期错误的基类。

    子类通过类属性声明默认错误码与 HTTP 状态码，实例化时可按需覆盖。
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    default_status: ClassVar[int] = 500

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        status: int | None = None,
        details: list[ErrorDetail] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.status = status or self.default_status
        self.message = message
        self.details = details or []
        self.retry_after = retry_after

    def to_response_body(self, request_id: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "success": False,
            "code": str(self.code),
            "message": self.message,
            "request_id": request_id,
        }
        if self.details:
            body["details"] = [detail.to_dict() for detail in self.details]
        return body


class UnauthorizedError(AppError):
    default_code = ErrorCode.UNAUTHORIZED
    default_status = 401


class ValidationError(AppError):
    default_code = ErrorCode.INVALID_ARGUMENT
    default_status = 400


class InvalidJsonError(AppError):
    default_code = ErrorCode.INVALID_JSON
    default_status = 400


class UnsupportedMediaTypeError(AppError):
    default_code = ErrorCode.UNSUPPORTED_MEDIA_TYPE
    default_status = 415


class PayloadTooLargeError(AppError):
    default_code = ErrorCode.PAYLOAD_TOO_LARGE
    default_status = 413


class NotFoundError(AppError):
    default_code = ErrorCode.NOT_FOUND
    default_status = 404


class ArtifactNotFoundError(AppError):
    default_code = ErrorCode.FILE_NOT_FOUND
    default_status = 404


class ArtifactExpiredError(AppError):
    default_code = ErrorCode.FILE_EXPIRED
    default_status = 410


class RateLimitedError(AppError):
    default_code = ErrorCode.RATE_LIMITED
    default_status = 429


class ServiceBusyError(AppError):
    default_code = ErrorCode.SERVICE_BUSY
    default_status = 503


class EngineUnavailableError(AppError):
    default_code = ErrorCode.ENGINE_UNAVAILABLE
    default_status = 503


class RenderTimeoutError(AppError):
    default_code = ErrorCode.RENDER_TIMEOUT
    default_status = 504


class RenderFailedError(AppError):
    default_code = ErrorCode.RENDER_FAILED
    default_status = 500


class TooManyPagesError(AppError):
    default_code = ErrorCode.PDF_TOO_MANY_PAGES
    default_status = 422


class OutputTooLargeError(AppError):
    default_code = ErrorCode.RENDER_OUTPUT_TOO_LARGE
    default_status = 422


class InternalError(AppError):
    default_code = ErrorCode.INTERNAL_ERROR
    default_status = 500
