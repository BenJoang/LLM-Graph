from typing import Annotated
from typing_extensions import TypedDict, NotRequired
import logging
from pathlib import Path

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

from src.context.context_compression import MessageManage
from src.context.context_builder import build_system_context
from src.context.message_context import(
    make_initial_state,
    build_turn_aware_tool_node,
)
from src.context.invoke_retry import invoke_with_retry
from src.context.compression_retry_adapter import CompressionRetryAdapter

logging.basicConfig(level=logging.INFO)

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn_id: int
    compression_session: NotRequired[CompressionSession]

AGENT_TOOLS = ["read_file", "get_file", "imageread", "agenttool", "python_tool_weaker", "skill_tool"]
SKILLS = ["wuxiwaterskill"]


def build_graph(
        profile_name: str = 'qwen3.6', 
        working_dir: str | None = None, 
        context_window_tokens: int = 32768,
    ):
    profile = load_profile(profile_name)
    prompt = load_prompt("wuxi_agent")
    collapse_prompt = load_prompt("collapse_compact")
    tools = registry.get_langchain_tools_by_names(
        AGENT_TOOLS,
        injected_by_tool={
            "agenttool": {
                "_profile_name": profile_name,
                "_context_window_tokens": context_window_tokens
            }
        },
    )

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
        
        context_system = build_system_context(
            working_dir=working_dir,
            skill_names=SKILLS
        )

        system_content = "\n\n".join(
            part for part in [
                prompt["system"],
                context_system,
            ]
            if part
        )
        

        messages = [
            {"role": "system", "content": system_content},
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
            filename="new_tool_agent_steps.md",
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
        },
    )

    builder.add_edge("tools", "assistant")

    return builder.compile()

def run_tool_agent(
    question: str, 
    profile_name: str = "qwen3.6",
    recursion_limit: int = 200,
    working_dir: str | None = None,
    context_window_tokens: int = 32768
) -> str:
    graph = build_graph(
        profile_name=profile_name,
        working_dir=working_dir,
        context_window_tokens=context_window_tokens,
        )

    return graph.invoke(
        make_initial_state(
            question,
            turn_id=1,
        ),
        config={"recursion_limit": recursion_limit},
    )
