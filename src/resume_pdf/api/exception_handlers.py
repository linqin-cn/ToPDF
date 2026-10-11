"""全局异常处理：把内部异常统一映射为稳定的错误码响应。

约束：响应体只含错误码、安全措辞、链路 ID 与字段级细节；堆栈、文件路径、
库版本一律只落日志。
"""

from __future__ import annotations

from http import HTTPStatus
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from resume_pdf.core.errors import AppError, ErrorCode, ErrorDetail, InternalError
from resume_pdf.core.logging import get_logger
from resume_pdf.core.request_id import get_request_id

logger = get_logger("resume_pdf.error")

JSON_MEDIA_TYPE = "application/json"


def _response(exc: AppError | None, *, status: int, body: dict[str, object]) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc is not None and exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(status_code=status, content=body, headers=headers or None)


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    error = cast(AppError, exc)
    request_id = get_request_id()
    # 4xx 属调用方问题，用 warning 记录即可；5xx 需要 stack 级别排查。
    log = logger.warning if error.status < HTTPStatus.INTERNAL_SERVER_ERROR else logger.error
    log(
        "app_error",
        extra={"code": str(error.code), "status": error.status, "request_id": request_id},
    )
    return _response(
        error,
        status=error.status,
        body=error.to_response_body(request_id),
    )


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    request_id = get_request_id()
    details = [
        ErrorDetail(
            reason=str(error.get("msg", "参数不合法")),
            field=".".join(str(part) for part in error.get("loc", ())[1:]) or None,
        )
        for error in validation_exc.errors()
    ]
    validation_error = AppError(
        "请求参数校验失败",
        code=ErrorCode.INVALID_ARGUMENT,
        status=HTTPStatus.BAD_REQUEST,
        details=details,
    )
    logger.warning(
        "validation_error",
        extra={"code": str(validation_error.code), "request_id": request_id},
    )
    return _response(
        validation_error,
        status=validation_error.status,
        body=validation_error.to_response_body(request_id),
    )


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    http_error = cast(StarletteHTTPException, exc)
    request_id = get_request_id()
    code = ErrorCode.NOT_FOUND if http_error.status_code == 404 else ErrorCode.INVALID_ARGUMENT
    message = "请求的资源不存在" if http_error.status_code == 404 else str(http_error.detail)
    error = AppError(message, code=code, status=http_error.status_code)
    return _response(error, status=http_error.status_code, body=error.to_response_body(request_id))


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    request_id = get_request_id()
    logger.error(
        "unhandled_error",
        extra={"request_id": request_id, "error_type": type(exc).__name__},
        exc_info=exc,
    )
    internal = InternalError("服务内部错误")
    return _response(
        internal,
        status=internal.status,
        body=internal.to_response_body(request_id),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
