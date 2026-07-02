from pathlib import Path

from src.tools.prompt_loader import read_tool_prompt
from src.tools.ReadFile import tool as read_file
from src.tools.GetFile import tool as get_file
from src.tools.Agenttool import tool as agenttool
from src.tools.Imageread import tool as imageread
from src.tools.QqMemorySearch import tool as qq_memory_search
from src.tools.MemorySearch import tool as memory_search
from src.tools.MemoryWrite import tool as memory_write
from src.tools.PythonTool import tool as python_tool
from src.tools.SkillTool import tool as skill_tool

from langchain_core.tools import StructuredTool


TOOL_ENTRIES = {
    read_file.TOOL_NAME: read_file,
    get_file.TOOL_NAME: get_file,
    imageread.TOOL_NAME: imageread,
    agenttool.TOOL_NAME: agenttool,
    qq_memory_search.TOOL_NAME: qq_memory_search,
    memory_search.TOOL_NAME: memory_search,
    memory_write.TOOL_NAME: memory_write,
    python_tool.TOOL_NAME: python_tool,
    skill_tool.TOOL_NAME: skill_tool,
}

TOOLS = [
    read_file,
    get_file,
    imageread,
    agenttool,
    memory_search,
    memory_write,
    python_tool,
    skill_tool,
]


def to_langchain_tool(tool_module, injected_kwargs: dict | None = None):
    injected_kwargs = injected_kwargs or {}

    def run_tool(**kwargs):
        result = tool_module.call(**kwargs, **injected_kwargs)
        return tool_module.render_result_for_llm(result)

    return StructuredTool.from_function(
        func=run_tool,
        name=tool_module.TOOL_NAME,
        description=read_tool_prompt(get_tool_dir(tool_module)),
        args_schema=tool_module.InputSchema,
    )

def get_langchain_tools() -> list:
    return [
        to_langchain_tool(tool)
        for tool in get_all_tools()
    ]

def get_subagent_tools() -> list:
    return [
        tool
        for tool in get_all_tools()
        if tool.TOOL_NAME != "agenttool"
    ]
def get_subagent_langchain_tools() -> list:
    return [
        to_langchain_tool(tool)
        for tool in get_subagent_tools()
    ]
def get_tool_dir(tool_module) -> Path:
    return Path(tool_module.__file__).resolve().parent

def get_all_tools() -> list:
    return TOOLS

def get_tool(tool_name: str):
    for tool in TOOLS:
        if tool.TOOL_NAME == tool_name:
            return tool
    raise KeyError(f"Tool not found: {tool_name}")

def get_read_only_tools() -> list:
    return [
        tool 
        for tool in TOOLS 
        if tool.IS_READ_ONLY]

def get_tool_modules_by_names(tool_names: list[str]) -> list:
    modules = []

    for name in tool_names:
        if name not in TOOL_ENTRIES:
            raise KeyError(f"Tool not found: {name}")

        modules.append(TOOL_ENTRIES[name])

    return modules

def get_langchain_tools_by_names(
        tool_names: list[str],
        injected_by_tool: dict[str, dict] | None = None,
        ) -> list:
    injected_by_tool = injected_by_tool or {}

    return [
        to_langchain_tool(
            tool_module,
            injected_kwargs=injected_by_tool.get(tool_module.TOOL_NAME),
            )
        for tool_module in get_tool_modules_by_names(tool_names)
    ]
