from __future__ import annotations

import importlib
import inspect
import re
from collections.abc import AsyncIterator, Callable
from typing import Any


DEFAULT_GRAPH_ENTRYPOINT = "src.graphs.tool_agent_graph:astream_tool_agent"
GRAPH_ENTRYPOINT_PATTERN = re.compile(
    r"^src\.graphs(?:\.[A-Za-z_]\w*)+:[A-Za-z_]\w*$"
)
GRAPH_ARGUMENTS = {
    "question",
    "thread_id",
    "profile_name",
    "vision_profile_name",
    "recursion_limit",
    "working_dir",
    "context_window_tokens",
}


class GraphEntrypointError(ValueError):
    pass


def normalize_graph_entrypoint(value: str) -> str:
    entrypoint = value.strip()
    if not GRAPH_ENTRYPOINT_PATTERN.fullmatch(entrypoint):
        raise GraphEntrypointError(
            "Graph 入口必须使用 src.graphs.<模块>:<异步函数> 格式"
        )
    return entrypoint


def resolve_graph_entrypoint(value: str) -> Callable[..., AsyncIterator[dict]]:
    entrypoint = normalize_graph_entrypoint(value)
    module_name, function_name = entrypoint.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except Exception as error:
        raise GraphEntrypointError(
            f"无法导入 Graph 模块 {module_name}：{type(error).__name__}: {error}"
        ) from error

    function: Any = getattr(module, function_name, None)
    if function is None or not callable(function):
        raise GraphEntrypointError(
            f"Graph 模块中不存在可调用函数：{function_name}"
        )
    if not inspect.isasyncgenfunction(function):
        raise GraphEntrypointError("Graph 入口必须是异步生成器函数")

    signature = inspect.signature(function)
    accepts_kwargs = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    missing = GRAPH_ARGUMENTS.difference(signature.parameters)
    if missing and not accepts_kwargs:
        raise GraphEntrypointError(
            "Graph 入口缺少参数：" + ", ".join(sorted(missing))
        )
    positional_only = {
        name
        for name in GRAPH_ARGUMENTS.intersection(signature.parameters)
        if signature.parameters[name].kind is inspect.Parameter.POSITIONAL_ONLY
    }
    if positional_only:
        raise GraphEntrypointError(
            "Graph 入口参数必须支持关键字调用："
            + ", ".join(sorted(positional_only))
        )
    return function


def validate_graph_entrypoint(value: str) -> str:
    entrypoint = normalize_graph_entrypoint(value)
    resolve_graph_entrypoint(entrypoint)
    return entrypoint
