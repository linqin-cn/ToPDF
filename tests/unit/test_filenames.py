"""下载文件名净化与 Content-Disposition 测试。"""

from __future__ import annotations

import pytest

from resume_pdf.core.filenames import (
    DEFAULT_FILE_NAME,
    MAX_FILE_NAME_LENGTH,
    content_disposition,
    sanitize_file_name,
)


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "..\\..\\windows\\system32\\config", "/etc/passwd", "../../"],
)
def test_path_traversal_is_stripped(raw: str) -> None:
    result = sanitize_file_name(raw)

    assert "/" not in result
    assert "\\" not in result
    assert not result.startswith(".")


def test_control_characters_are_removed() -> None:
    result = sanitize_file_name("张三\r\ninjected.pdf")

    assert "\r" not in result
    assert "\n" not in result


def test_chinese_name_is_preserved_and_extension_appended() -> None:
    assert sanitize_file_name("张三-简历") == "张三-简历.pdf"


def test_non_pdf_extension_is_kept_and_pdf_appended() -> None:
    assert sanitize_file_name("resume.txt") == "resume.txt.pdf"


def test_blank_name_falls_back_to_default() -> None:
    assert sanitize_file_name(None) == DEFAULT_FILE_NAME
    assert sanitize_file_name("   ") == DEFAULT_FILE_NAME
    assert sanitize_file_name("...") == DEFAULT_FILE_NAME


def test_long_name_is_truncated() -> None:
    result = sanitize_file_name("a" * 400)

    assert len(result) <= MAX_FILE_NAME_LENGTH
    assert result.endswith(".pdf")


def test_content_disposition_encodes_rfc5987() -> None:
    header = content_disposition("张三.pdf")

    assert header.startswith("attachment;")
    assert 'filename="' in header
    assert "filename*=UTF-8''%E5%BC%A0%E4%B8%89.pdf" in header
    assert "\r" not in header
    assert "\n" not in header
