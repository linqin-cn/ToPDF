"""页面尺寸与边距解析。

页面参数是唯一允许影响排版的外部输入，全部通过渲染引擎选项下达，
**不向 HTML 注入任何样式或节点**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from resume_pdf.core.errors import ErrorDetail, ValidationError

Orientation = Literal["portrait", "landscape"]
MediaType = Literal["print", "screen"]

# CSS 单位换算为毫米
UNIT_TO_MM: Final[dict[str, float]] = {
    "px": 25.4 / 96,
    "mm": 1.0,
    "cm": 10.0,
    "in": 25.4,
    "pt": 25.4 / 72,
}

# 命名纸张（毫米，长边为高）
NAMED_SIZES_MM: Final[dict[str, tuple[float, float]]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LEGAL": (215.9, 355.6),
    "TABLOID": (279.4, 431.8),
}
CUSTOM_SIZE: Final = "CUSTOM"

MIN_SIDE_MM: Final = 10.0
MAX_SIDE_MM: Final = 2000.0
MAX_MARGIN_MM: Final = 50.0

_LENGTH_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>px|mm|cm|in|pt)?$")


@dataclass(frozen=True, slots=True)
class MarginBox:
    """四边边距，单位毫米。"""

    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0

    @property
    def is_zero(self) -> bool:
        return not any((self.top, self.right, self.bottom, self.left))

    def as_css(self) -> dict[str, str]:
        return {
            "top": f"{self.top}mm",
            "right": f"{self.right}mm",
            "bottom": f"{self.bottom}mm",
            "left": f"{self.left}mm",
        }


@dataclass(frozen=True, slots=True)
class ResolvedPage:
    """解析后的页面设置。"""

    width_mm: float
    height_mm: float
    margin: MarginBox
    prefer_css_page_size: bool
    fallback_format: str | None

    def as_css_size(self) -> tuple[str, str]:
        return f"{self.width_mm}mm", f"{self.height_mm}mm"


def _fail(message: str, field: str) -> ValidationError:
    return ValidationError(message, details=[ErrorDetail(reason=message, field=field)])


def parse_length_mm(raw: str | float | int, *, unit: str = "mm", field: str) -> float:
    """把带单位或无单位的长度解析为毫米。"""
    if isinstance(raw, int | float):
        return float(raw) * UNIT_TO_MM[unit]

    text = raw.strip().lower()
    match = _LENGTH_PATTERN.match(text)
    if match is None:
        raise _fail(f"{field} 不是合法的长度值：{raw!r}", field)
    unit_name = match.group("unit") or unit
    return float(match.group("value")) * UNIT_TO_MM[unit_name]


def parse_margin(raw: str | None, *, unit: str = "mm", field: str = "page.margin") -> MarginBox:
    """解析 CSS 风格简写边距：0、12mm、10mm 15mm、5mm 10mm 5mm 10mm。"""
    if raw is None or not raw.strip():
        return MarginBox()

    parts = raw.split()
    if len(parts) > 4:
        raise _fail("page.margin 最多接受 4 个值（上/右/下/左）", field)

    values = [parse_length_mm(part, unit=unit, field=field) for part in parts]
    if len(values) == 1:
        top = right = bottom = left = values[0]
    elif len(values) == 2:
        top = bottom = values[0]
        right = left = values[1]
    elif len(values) == 3:
        top, right, bottom = values
        left = right
    else:
        top, right, bottom, left = values

    for value in (top, right, bottom, left):
        if value < 0 or value > MAX_MARGIN_MM:
            raise _fail("page.margin 单项必须在 0 到 50mm 之间", field)

    return MarginBox(top=top, right=right, bottom=bottom, left=left)


def _resolve_side(
    *,
    size: str | None,
    width: float | None,
    height: float | None,
    unit: str,
    orientation: Orientation,
) -> tuple[float, float]:
    if size is None or not size.strip():
        # 交给 HTML 的 @page 决定，缺省由 fallback_format 兜底。
        return 0.0, 0.0

    normalized = size.strip().upper()
    if normalized == CUSTOM_SIZE:
        if width is None or height is None:
            raise _fail("page.size=Custom 时必须同时提供 page.width 与 page.height", "page.size")
        resolved_width = parse_length_mm(width, unit=unit, field="page.width")
        resolved_height = parse_length_mm(height, unit=unit, field="page.height")
    elif normalized in NAMED_SIZES_MM:
        resolved_width, resolved_height = NAMED_SIZES_MM[normalized]
    else:
        allowed = "、".join(sorted(NAMED_SIZES_MM)) + "、Custom"
        raise _fail(f"page.size 取值不合法，可选：{allowed}", "page.size")

    for name, value in (("width", resolved_width), ("height", resolved_height)):
        if value < MIN_SIDE_MM or value > MAX_SIDE_MM:
            raise _fail(
                f"page.{name} 必须在 {MIN_SIDE_MM:g} 到 {MAX_SIDE_MM:g} 毫米之间",
                f"page.{name}",
            )

    # landscape 语义为「宽大于高」，对命名纸张与自定义尺寸一致生效。
    if orientation == "landscape":
        return max(resolved_width, resolved_height), min(resolved_width, resolved_height)
    return min(resolved_width, resolved_height), max(resolved_width, resolved_height)


def resolve_page(
    *,
    size: str | None,
    width: float | None,
    height: float | None,
    unit: str | None,
    orientation: Orientation,
    margin: str | None,
    default_size: str,
) -> ResolvedPage:
    """把请求参数解析为引擎可直接使用的页面设置。"""
    unit_name = (unit or "mm").lower()
    if unit_name not in UNIT_TO_MM:
        raise _fail(f"page.unit 取值不合法，可选：{'、'.join(sorted(UNIT_TO_MM))}", "page.unit")

    fallback_format = default_size.strip().upper()
    if fallback_format not in NAMED_SIZES_MM:
        fallback_format = "A4"

    if size is None or not size.strip():
        # 未传任何页面参数：让 HTML 的 @page 生效，缺省回退到 default_size。
        resolved_width, resolved_height = NAMED_SIZES_MM[fallback_format]
        margin_box = parse_margin(margin, unit=unit_name)
        return ResolvedPage(
            width_mm=resolved_width,
            height_mm=resolved_height,
            margin=margin_box,
            prefer_css_page_size=True,
            fallback_format=fallback_format,
        )

    resolved_width, resolved_height = _resolve_side(
        size=size,
        width=width,
        height=height,
        unit=unit_name,
        orientation=orientation,
    )
    return ResolvedPage(
        width_mm=resolved_width,
        height_mm=resolved_height,
        margin=parse_margin(margin, unit=unit_name),
        # 调用方显式指定了页面参数，则以参数为准，不再尊重 HTML 的 @page 尺寸。
        prefer_css_page_size=False,
        fallback_format=None,
    )
