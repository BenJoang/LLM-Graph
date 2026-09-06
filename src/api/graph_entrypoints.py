from __future__ import annotations

import re

from src.services.tool_agent_runner import (
    MANAGED_GRAPH_BUILDERS,
    TOOL_AGENT_ENTRYPOINT,
    load_graph_builder,
)


DEFAULT_GRAPH_ENTRYPOINT = TOOL_AGENT_ENTRYPOINT
GRAPH_ENTRYPOINT_PATTERN = re.compile(
    r"^src\.graphs(?:\.[A-Za-z_]\w*)+:[A-Za-z_]\w*$"
)


class GraphEntrypointError(ValueError):
    pass


def normalize_graph_entrypoint(value: str) -> str:
    entrypoint = value.strip()
    if not GRAPH_ENTRYPOINT_PATTERN.fullmatch(entrypoint):
        raise GraphEntrypointError(
            "Graph 入口必须使用 src.graphs.<模块>:<异步函数> 格式"
        )
    return entrypoint


def resolve_graph_entrypoint(value: str):
    entrypoint = normalize_graph_entrypoint(value)
    if entrypoint not in MANAGED_GRAPH_BUILDERS:
        raise GraphEntrypointError(
            f"Graph 未注册或源码不可用：{entrypoint}"
        )
    try:
        return load_graph_builder(entrypoint)
    except Exception as error:
        raise GraphEntrypointError(str(error)) from error


def validate_graph_entrypoint(value: str) -> str:
    entrypoint = normalize_graph_entrypoint(value)
    resolve_graph_entrypoint(entrypoint)
    return entrypoint
