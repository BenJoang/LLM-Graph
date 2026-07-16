from typing import Annotated
from typing_extensions import TypedDict, NotRequired

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from src.context.context_compression import (
    MessageManage,
    CompressionSession,
)

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.tools import registry
from src.context.message_context import(
    make_initial_state,
    build_turn_aware_tool_node,
)
from src.context.invoke_retry import invoke_with_retry
from src.context.compression_retry_adapter import CompressionRetryAdapter


class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn_id: int
    compression_session: NotRequired[CompressionSession]


def build_graph(profile_name: str = "qwen3.6", context_window_tokens: int = 32768):
    profile = load_profile(profile_name)
    prompt = load_prompt("subagent")
    collapse_prompt = load_prompt("collapse_compact")

    tools = registry.get_subagent_langchain_tools()

    llm = build_chat_model(profile, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

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

    def assistant_node(state: ToolAgentState) -> dict:

        messages_for_query, compressed, compression_session = message_manage.prepare_messages_for_query(
            state["messages"],
            state.get("compression_session")
        )

        messages = [
            {"role": "system", "content": prompt["system"]},
            *messages_for_query,
        ]

        retry_adapter = CompressionRetryAdapter(
            message_manage=message_manage,
            compression_session=compression_session,
            current_turn_id=state["turn_id"],
        )

        response = invoke_with_retry(
            invoke_fn=llm_with_tools.invoke,
            messages=messages,
            original_messages=messages,
            compress_fn=retry_adapter,
            turn_id=state["turn_id"],
            max_context_retries=3,
        )

        save_langchain_message_md(
            response,
            question=state["messages"][0].content,
            messages=messages,
            tools=tools,
            request_options={
                "model": profile["model"],
                "temperature": 0,
                "base_url": profile["base_url"],
            },
            filename="subagent_steps.md",
        )
        return {
            "messages": [response],
            "compression_session": retry_adapter.compression_session,
        }

    builder = StateGraph(ToolAgentState)

    builder.add_node("assistant", assistant_node)
    builder.add_node("tools", build_turn_aware_tool_node(tools))

    builder.add_edge(START, "assistant")

    builder.add_conditional_edges(
        "assistant",
        tools_condition,
        {
            "tools": "tools",
            "__end__": END,
        }
    )

    builder.add_edge("tools", "assistant")

    return builder.compile()

