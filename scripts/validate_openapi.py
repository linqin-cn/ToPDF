"""校验 docs/04-openapi.yaml 的结构完整性与 $ref 可解析性。

只做轻量静态检查（不依赖 openapi-spec-validator），用于在 CI 与本地快速拦截
明显的契约错误：YAML 不合法、$ref 悬空、operationId 重复、错误码集合漂移。
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "docs" / "04-openapi.yaml"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def _iter_refs(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            yield from _iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_refs(item)


def _resolve(spec: dict, ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def main() -> int:
    spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    problems: list[str] = []

    if spec.get("openapi") != "3.0.3":
        problems.append(f"openapi 版本必须为 3.0.3，实际 {spec.get('openapi')!r}")

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
        print("OpenAPI 校验失败：")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"OpenAPI 校验通过：{spec['openapi']}，"
        f"{len(spec['paths'])} 个路径，{len(operation_ids)} 个操作，"
        f"{len(error_codes)} 个错误码"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
