"""校验 OpenAPI 文档的结构完整性与 $ref 可解析性。

只做轻量静态检查（不依赖 openapi-spec-validator），用于在 CI 与本地快速拦截
明显的契约错误：YAML 不合法、$ref 悬空、operationId 重复、错误码重复，
以及星辰平台解析器不支持的联合类型写法。

用法：
    python scripts/validate_openapi.py [契约文件路径]
默认校验 docs/04-openapi.yaml。
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC_PATH = ROOT / "docs" / "04-openapi.yaml"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
FORBIDDEN_KEYS = ("oneOf", "anyOf", "not")


def _iter_refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _resolve(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _find_forbidden(node: Any, path: str = "") -> list[str]:
    """找出平台解析器不支持的联合类型关键字。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in FORBIDDEN_KEYS:
                found.append(here)
            found.extend(_find_forbidden(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_find_forbidden(item, f"{path}[{index}]"))
    return found


def main(argv: list[str]) -> int:
    spec_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_SPEC_PATH
    try:
        display_path = spec_path.resolve().relative_to(ROOT)
    except ValueError:
        display_path = spec_path
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    problems: list[str] = []

    if spec.get("openapi") != "3.0.3":
        problems.append(f"openapi 版本必须为 3.0.3，实际 {spec.get('openapi')!r}")

    for path in _find_forbidden(spec):
        problems.append(f"契约中不允许出现联合类型关键字: {path}")

    for ref in sorted(set(_iter_refs(spec))):
        if _resolve(spec, ref) is None:
            problems.append(f"$ref 无法解析: {ref}")

    operation_ids = [
        item["operationId"]
        for item in spec.get("paths", {}).values()
        for method, item in item.items()
        if method in HTTP_METHODS and isinstance(item, dict) and "operationId" in item
    ]
    for op_id, count in Counter(operation_ids).items():
        if count > 1:
            problems.append(f"operationId 重复: {op_id} x{count}")

    error_codes = set(spec["components"]["schemas"]["ErrorCode"]["enum"])
    if len(error_codes) != len(spec["components"]["schemas"]["ErrorCode"]["enum"]):
        problems.append("ErrorCode 枚举存在重复项")

    if problems:
        print(f"OpenAPI 校验失败（{display_path}）：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"OpenAPI 校验通过（{display_path}）：{spec['openapi']}，"
        f"{len(spec['paths'])} 个路径，{len(operation_ids)} 个操作，"
        f"{len(error_codes)} 个错误码"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
