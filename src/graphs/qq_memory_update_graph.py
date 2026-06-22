from pathlib import Path
from typing import Annotated
from typing_extensions import TypedDict
from typing import Literal
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.client.mymodel_client import save_graph_mdv2
from src.tools import registry


class MemoryUpdateState(TypedDict):
    messages: Annotated[list, add_messages]
    group_id: str
    batch_records: str

UPDATE_TOOL_NAMES = [
    "memory_search",
    "memory_write",
    "qq_memory_search",
]

UPDATE_LOG_FILE = "qq_memory_update_steps.md"

profile = load_profile("qwen3.6")
prompt = load_prompt("qq_memory_update")

llm = build_chat_model(profile, temperature=0)

tools = registry.get_langchain_tools_by_names(UPDATE_TOOL_NAMES)
llm_with_tools = llm.bind_tools(tools)
tool_node = ToolNode(tools)

def make_initial_state(group_id: str, batch_records: str,) -> MemoryUpdateState:
    group_memory_dir = (
        Path(__file__).resolve().parents[2]
        / "memory"
        / "qq_memory"
        / "groups"
        / group_id
        / "memory"
    )

    group_file = group_memory_dir / "group.yaml"
    members_file = group_memory_dir / "members.yaml"

    content = (
        f"当前QQ群号：{group_id}\n\n"
        f"只允许修改以下文件：\n"
        f"- {group_file}\n"
        f"- {members_file}\n\n"
        f"本批新增记录：\n{batch_records}"
    )

    return {
        "messages": [{"role": "user", "content": content}],
        "group_id": group_id,
        "batch_records": batch_records,
    }

def updater_node(state: MemoryUpdateState) -> dict:
    messages = [
        {"role": "system", "content": prompt["system"]},
        *state["messages"],
    ]

    response = llm_with_tools.invoke(messages)

    save_graph_mdv2(
        event_type="model",
        node_name="memory_updater",
        response=response,
        filename=UPDATE_LOG_FILE,
    )
    return {
        "messages": [response],
    }

def route_updater(
    state: MemoryUpdateState,
) -> Literal["tools", "done"]:
    last_message = state["messages"][-1]

    if getattr(last_message, "tool_calls", None):
        return "tools"

    return "done"

def run_memory_tools_node(state: MemoryUpdateState) -> dict:
    update = tool_node.invoke(state)

    save_graph_mdv2(
        event_type="tools",
        node_name="memory_tools",
        tool_messages=update.get("messages", []),
        filename=UPDATE_LOG_FILE,
    )

    return update

def build_graph():
    builder = StateGraph(MemoryUpdateState)

    builder.add_node("updater", updater_node)
    builder.add_node("tools", run_memory_tools_node)

    builder.add_edge(START, "updater")

    builder.add_conditional_edges(
        "updater",
        route_updater,
        {
            "tools": "tools",
            "done": END,
        },
    )

    builder.add_edge("tools", "updater")

    return builder.compile()


graph = build_graph()

def run_qq_memory_update(
    group_id: str,
    batch_records: str,
    recursion_limit: int = 30,
):
    
    save_graph_mdv2(
        event_type="run_start",
        node_name="memory_update_start",
        group_id=group_id,
        question="更新QQ群长期记忆",
        part_history=batch_records,
        filename=UPDATE_LOG_FILE,
    )

    result = graph.invoke(
        make_initial_state(group_id, batch_records),
        config={"recursion_limit": recursion_limit},
    )

    save_graph_mdv2(
        event_type="run_end",
        node_name="memory_update_end",
        filename=UPDATE_LOG_FILE,
    )
