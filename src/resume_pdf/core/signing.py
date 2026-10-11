"""下载链接签名（`DOWNLOAD_URL_MODE=signed` 时启用）。

签名的载荷是「产物 ID + 过期时间戳」，用 HMAC-SHA256 加签，避免产物在没有
API Key 的场景下被无限期转发。默认的 `key` 模式不使用本模块。
"""

from __future__ import annotations

import hashlib
import hmac


def sign_download(file_id: str, expires_at: int, secret: str) -> str:
    """返回下载链接的签名（hex）。"""
    payload = f"{file_id}:{expires_at}".encode()
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_download(file_id: str, expires_at: int, signature: str, secret: str) -> bool:
    """校验签名，使用常量时间比较。"""
    return hmac.compare_digest(signature, sign_download(file_id, expires_at, secret))
