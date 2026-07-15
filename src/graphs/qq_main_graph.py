from typing import Annotated
from typing_extensions import TypedDict
from typing import Literal

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)
from typing_extensions import NotRequired

from src.context.context_compression import (
    MessageManage,
    CompressionSession,
)

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt
from src.client.mymodel_client import save_graph_mdv2
from src.tools import registry

import json
from pathlib import Path

from src.client.mymodel_client import serialize_message

DEBUG_COMPRESS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "qq_compress_debug"
DEBUG_COMPRESS_DIR.mkdir(parents=True, exist_ok=True)

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    compression_session: NotRequired[CompressionSession]

    history_messages: Annotated[list, add_messages]
    history_result: str

    image_messages: Annotated[list, add_messages]
    image_result: str
    
    group_id: str
    current_message: str

def make_initial_state(group_id: str, question: str) -> ToolAgentState:
    content = (
        f"当前QQ群号：{group_id}\n"
        f"{question}"
    )
    return {
        "messages": [{"role": "user", "content": content}],
        "history_messages": [],
        "history_result": "",
        "image_messages": [],
        "image_result": "",
        "group_id": group_id,
        "current_message": question
    }


HISTORY_TOOL_NAMES = [
    "qq_memory_search",
    "memory_search"
]

IMAGE_TOOL_NAMES = [
    "qq_memory_search",
    "imageread",
]

VISION_PROFILE_NAME = "qwen3.6"



def route_history(state: ToolAgentState) -> Literal["tools", "done"]:
    last_message = state["history_messages"][-1]

    if getattr(last_message, "tool_calls", None):
        return "tools"
    
    return "done"

def route_image(state: ToolAgentState) -> Literal["tools", "done"]:
    last_message = state["image_messages"][-1]

    if getattr(last_message, "tool_calls", None):
        return "tools"
    
    return "done"



def build_graph(profile_name: str = "qwen3.6", context_window_tokens: int = 32768,):
    allow_image = profile_name == VISION_PROFILE_NAME

    profile = load_profile(profile_name)
    uuz_prompt = load_prompt("youyouzi")
    history_prompt = load_prompt("qq_memory_history")
    collapse_prompt = load_prompt("collapse_compact")
    

    llm = build_chat_model(profile, temperature=0)
    chat_llm = build_chat_model(profile, temperature=1.5)

    def summarize_with_main_model(text: str) -> str:
        response = llm.invoke([
            SystemMessage(
                content=collapse_prompt["system"]
            ),
            HumanMessage(content=text),
        ])

        return str(response.content)
    message_manage = MessageManage(
        max_tokens=context_window_tokens,
        summarize_fn=summarize_with_main_model,
    )

    history_tools = registry.get_langchain_tools_by_names(HISTORY_TOOL_NAMES)
    history_node_llm = llm.bind_tools(history_tools)
    history_tool_node = ToolNode(
        history_tools,
        messages_key="history_messages",
    )

    if allow_image:
        image_prompt = load_prompt("qq_memory_image")
        image_tools = registry.get_langchain_tools_by_names(IMAGE_TOOL_NAMES)
        image_node_llm = llm.bind_tools(image_tools)
        image_tool_node = ToolNode(
                image_tools,
                messages_key="image_messages",
            )
    
    def need_history_node(state: ToolAgentState) -> dict:

        raw_messages = [
            {"role": "system", "content": history_prompt["system"]},
            *state["messages"],
            *state["history_messages"],
        ]

        messages, compressed, compression_session = message_manage.prepare_messages_for_query(
            raw_messages,
            state.get("compression_session")
        )

        save_compress_debug("history", raw_messages, messages, compressed)

        response = history_node_llm.invoke(messages)
        
        save_graph_mdv2(
            event_type="model",
            node_name="history",
            response=response,
            filename="qq_main_graph_steps1.md",
        )

        return {
            "history_messages": [response],
            "compression_session": compression_session,
        }

    def image_node(state: ToolAgentState) -> dict:

        last_history = state["history_messages"][-1] if state["history_messages"] else None
        last_history_content = getattr(last_history, "content", "") if last_history else ""

        messages = [
            {"role": "system", "content": image_prompt["system"]},
            *state["messages"],
        ]

        messages.append({
            "role":"user",
            "content": f"历史工具检索结果: {last_history_content}",
        })

        messages.extend(state["image_messages"])

        response = image_node_llm.invoke(messages)

        save_graph_mdv2(
            event_type="model",
            node_name="image",
            response=response,
            filename="qq_main_graph_steps1.md",
        )

        return {
            "image_messages": [response]
        }

    def answer_node(state: ToolAgentState) -> dict:
        last_history = state["history_messages"][-1] if state["history_messages"] else None
        last_history_content = getattr(last_history, "content", "") if last_history else ""

        last_image = state["image_messages"][-1] if state["image_messages"] else None
        last_image_content = getattr(last_image, "content", "") if last_image else ""

        messages = [
            {"role": "system", "content": uuz_prompt["system"]},
            *state["messages"]
        ]

        if last_history_content:
            messages.append({"role": "user", "content": f"历史检索结果:{last_history_content}"})

        if last_image_content:
            messages.append({"role": "user", "content": f"图片识别结果:{last_image_content}"})

        response = chat_llm.invoke(messages)

        return {
            "messages": [response]
        }
    
    builder = StateGraph(ToolAgentState)

    builder.add_node("history", need_history_node)
    builder.add_node("history_tools", history_tool_node)

    if allow_image:
        builder.add_node("image", image_node)
        builder.add_node("image_tools", image_tool_node)
    
    builder.add_node("answer", answer_node)
    builder.add_edge(START, "history")
    builder.add_conditional_edges(
        "history",
        route_history,
        {
            "tools": "history_tools",
            "done": "image" if allow_image else "answer",  
        },
    )
    builder.add_edge("history_tools", "history")

    if allow_image:
        builder.add_conditional_edges(
            "image",
            route_image,
            {
                "tools": "image_tools",
                "done": "answer",  
            },
        )
        builder.add_edge("image_tools", "image")
    builder.add_edge("answer", END)

    return builder.compile()





def run_qq_main_agent(
        group_id: str, 
        question: str, 
        profile_name:str = "qwen3.6", 
        recursion_limit: int = 200,
        context_window_tokens: int = 32768,
    ) -> str:
    
    graph = build_graph(
        profile_name=profile_name,
        context_window_tokens=context_window_tokens,
    )

    save_graph_mdv2(
        event_type="run_start",
        node_name="start",
        group_id=group_id,
        question=question,
        filename="qq_main_graph_steps1.md",
    )
    
    result = graph.invoke(
        make_initial_state(group_id, question),
        config={"recursion_limit": recursion_limit}
    )

    save_graph_mdv2(
        event_type="run_end",
        node_name="end",
        filename="qq_main_graph_steps1.md",
    )

    final_message = result["messages"][-1]
    return final_message.content


def save_compress_debug(node_name: str, before_messages: list, after_messages: list, compressed: bool):
    if not compressed:
        return

    output_path = DEBUG_COMPRESS_DIR / f"{node_name}_compress_debug.json"

    data = {
        "node": node_name,
        "compressed": compressed,
        "before_count": len(before_messages),
        "after_count": len(after_messages),
        "before": [serialize_message(m) for m in before_messages],
        "after": [serialize_message(m) for m in after_messages],
    }

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)