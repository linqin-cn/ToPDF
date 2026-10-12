"""从完整契约生成面向星辰智能体平台的精简契约。

平台按 Schema 生成工具调用，操作越多、结构越复杂，模型选错工具与平台解析
失败的概率越高。因此精简契约只保留渲染与下载两个操作，并裁剪未被引用的
组件，保证两份文档由同一来源派生、不会各自漂移。

用法：
    python scripts/build_agent_spec.py            # 生成 docs/04-openapi.agent.yaml
    python scripts/build_agent_spec.py --check    # 仅校验磁盘上的文件是否为最新
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "docs" / "04-openapi.yaml"
TARGET_PATH = ROOT / "docs" / "04-openapi.agent.yaml"

AGENT_PATHS = ("/v1/render",)
GENERATED_HEADER = (
    "# 由 scripts/build_agent_spec.py 从 docs/04-openapi.yaml 生成，请勿手工编辑。\n"
    "# 该文档供星辰智能体平台导入使用，只包含渲染操作。\n"
)


def _collect_refs(node: Any, refs: set[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/"):
                refs.add(value)
            _collect_refs(value, refs)
    elif isinstance(node, list):
        for item in node:
            _collect_refs(item, refs)


def _resolve(spec: dict[str, Any], ref: str) -> Any:
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def build(spec: dict[str, Any]) -> dict[str, Any]:
    """裁剪出平台契约，仅保留可触达的路径与组件。"""
    paths = {path: spec["paths"][path] for path in AGENT_PATHS}

    reachable: set[str] = set()
    pending: set[str] = set()
    _collect_refs(paths, pending)
    # 安全方案通过名字引用，不经 $ref，需要显式保留。
    used_schemes = {name for requirement in spec.get("security", []) for name in requirement}

    while pending:
        ref = pending.pop()
        if ref in reachable:
            continue
        reachable.add(ref)
        _collect_refs(_resolve(spec, ref), pending)

    components: dict[str, Any] = {}
    for ref in sorted(reachable):
        _, _, section, name = ref.split("/", 3)
        components.setdefault(section, {})[name] = spec["components"][section][name]

    if used_schemes:
        components["securitySchemes"] = {
            name: scheme
            for name, scheme in spec["components"]["securitySchemes"].items()
            if name in used_schemes
        }

    info = dict(spec["info"])
    info["description"] = (
        f"{info['description'].strip()}\n\n"
        "本文件由 scripts/build_agent_spec.py 从完整契约生成，只包含渲染与下载两个操作，"
        "供智能体平台导入。servers 中的地址在服务端按 PUBLIC_BASE_URL 运行时注入。"
    )

    return {
        "openapi": spec["openapi"],
        "info": info,
        "servers": spec["servers"],
        "security": spec.get("security", []),
        "tags": [tag for tag in spec.get("tags", []) if tag["name"] in {"render", "files"}],
        "paths": paths,
        "components": components,
    }


def render(agent_spec: dict[str, Any]) -> str:
    body = yaml.safe_dump(
        agent_spec,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    return GENERATED_HEADER + body


def main(argv: list[str]) -> int:
    spec = yaml.safe_load(SOURCE_PATH.read_text(encoding="utf-8"))
    generated = render(build(spec))

    if "--check" in argv:
        current = TARGET_PATH.read_text(encoding="utf-8") if TARGET_PATH.exists() else ""
        if current != generated:
            print(
                f"{TARGET_PATH.relative_to(ROOT)} 与契约不一致，请执行 "
                "python scripts/build_agent_spec.py 后重新提交。"
            )
            return 1
        print(f"{TARGET_PATH.relative_to(ROOT)} 与契约一致")
        return 0

    TARGET_PATH.write_text(generated, encoding="utf-8", newline="\n")
    print(f"已生成 {TARGET_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
