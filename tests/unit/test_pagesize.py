"""页面尺寸与边距解析测试。"""

from __future__ import annotations

import pytest

from resume_pdf.core.errors import ValidationError
from resume_pdf.render.pagesize import (
    MarginBox,
    ResolvedPage,
    parse_length_mm,
    parse_margin,
    resolve_page,
)


def resolve(**overrides: object) -> ResolvedPage:
    params: dict[str, object] = {
        "size": None,
        "width": None,
        "height": None,
        "unit": None,
        "orientation": "portrait",
        "margin": None,
        "default_size": "A4",
    }
    params.update(overrides)
    return resolve_page(**params)  # type: ignore[arg-type]


def test_default_defers_to_html_page_rule() -> None:
    page = resolve()

    assert page.prefer_css_page_size is True
    assert page.fallback_format == "A4"
    assert (page.width_mm, page.height_mm) == (210.0, 297.0)
    assert page.margin == MarginBox()


def test_explicit_size_disables_css_page_preference() -> None:
    page = resolve(size="A4")

    assert page.prefer_css_page_size is False
    assert page.fallback_format is None
    assert page.as_css_size() == ("210.0mm", "297.0mm")


def test_landscape_is_wider_than_tall() -> None:
    page = resolve(size="A4", orientation="landscape")

    assert (page.width_mm, page.height_mm) == (297.0, 210.0)


def test_custom_size_with_unit_conversion() -> None:
    page = resolve(size="Custom", width=8.5, height=11, unit="in")

    assert (round(page.width_mm, 1), round(page.height_mm, 1)) == (215.9, 279.4)


@pytest.mark.parametrize(
    "overrides",
    [
        {"size": "Custom"},
        {"size": "Custom", "width": 100},
        {"size": "A0"},
        {"size": "A4", "unit": "em"},
        {"size": "Custom", "width": 5, "height": 100},
        {"size": "Custom", "width": 100, "height": 5000},
    ],
)
def test_invalid_page_parameters_are_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        resolve(**overrides)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", MarginBox()),
        ("12mm", MarginBox(12.0, 12.0, 12.0, 12.0)),
        ("10mm 15mm", MarginBox(top=10.0, right=15.0, bottom=10.0, left=15.0)),
        ("5mm 10mm 6mm", MarginBox(top=5.0, right=10.0, bottom=6.0, left=10.0)),
        ("1mm 2mm 3mm 4mm", MarginBox(top=1.0, right=2.0, bottom=3.0, left=4.0)),
        ("10", MarginBox(10.0, 10.0, 10.0, 10.0)),
    ],
)
def test_margin_shorthand(raw: str, expected: MarginBox) -> None:
    assert parse_margin(raw) == expected


@pytest.mark.parametrize("raw", ["1mm 2mm 3mm 4mm 5mm", "60mm", "-3mm", "abc"])
def test_invalid_margin_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_margin(raw)


def test_length_unit_conversion() -> None:
    assert parse_length_mm("1in", field="x") == pytest.approx(25.4)
    assert parse_length_mm("96px", field="x") == pytest.approx(25.4)
    assert parse_length_mm("2cm", field="x") == pytest.approx(20.0)
    assert parse_length_mm("72pt", field="x") == pytest.approx(25.4)


def test_margin_css_output_uses_millimeters() -> None:
    margin = parse_margin("3mm 4mm 5mm 6mm")

    assert margin.as_css() == {"top": "3.0mm", "right": "4.0mm", "bottom": "5.0mm", "left": "6.0mm"}
    assert margin.is_zero is False
