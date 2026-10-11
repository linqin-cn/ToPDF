"""下载文件名净化。

调用方提供的文件名只用于 Content-Disposition，必须剔除路径分隔符、控制字符与
可能破坏头部结构的字符，避免路径穿越与响应头注入。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

DEFAULT_FILE_NAME = "resume.pdf"
MAX_FILE_NAME_LENGTH = 128
PDF_EXTENSION = ".pdf"

_UNSAFE_PATTERN = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")


def sanitize_file_name(value: str | None) -> str:
    """返回可安全使用的 .pdf 文件名，净化后为空时回落到默认值。"""
    candidate = Path(str(value or DEFAULT_FILE_NAME)).name
    candidate = _UNSAFE_PATTERN.sub("_", candidate)
    # 先摘掉扩展名，再按「总长度不超过上限」截断主干，最后统一补 .pdf。
    stem = (
        candidate[: -len(PDF_EXTENSION)] if candidate.lower().endswith(PDF_EXTENSION) else candidate
    )
    stem = stem[: MAX_FILE_NAME_LENGTH - len(PDF_EXTENSION)].strip("._")
    if not stem:
        return DEFAULT_FILE_NAME
    return f"{stem}{PDF_EXTENSION}"


def content_disposition(file_name: str) -> str:
    """按 RFC 5987 生成同时兼容旧客户端的 Content-Disposition。"""
    ascii_fallback = file_name.encode("ascii", "ignore").decode("ascii").strip("._")
    if not ascii_fallback:
        ascii_fallback = DEFAULT_FILE_NAME
    quoted = ascii_fallback.replace('"', "_").replace("\\", "_")
    return f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quote(file_name, safe='')}"
