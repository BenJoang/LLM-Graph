from typing import Annotated
from typing_extensions import TypedDict, NotRequired
import logging
from pathlib import Path
import asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.tools import registry

from src.context.message_context import(
    get_next_turn_id,
    make_initial_state,
    build_turn_aware_tool_node,
    mark_ai_message,
)
from src.context.context_compression import MessageManage, CompressionSession
from src.context.context_builder import build_system_context
from src.context.invoke_retry import invoke_with_retry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DB = PROJECT_ROOT / "outputs" / "checkpoints" / "tool_agent.sqlite"

#logging.basicConfig(level=logging.INFO)

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn_id: int
    compression_session: NotRequired[CompressionSession]

AGENT_TOOLS = ["read_file", "get_file", "imageread", "agenttool", "python_tool", "skill_tool"]
SKILLS = ["wuxiwaterskill"]


def build_graph(
        profile_name: str = 'qwen3.6',
        working_dir: str | None = None,
        checkpointer = None,
        context_window_tokens: int = 32768,
):
    profile = load_profile(profile_name)
    prompt = load_prompt("tool_agent")
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
        (messages_for_query, compressed, compression_session,) = message_manage.prepare_messages_for_query(
            state["messages"],
            state.get("compression_session")
        )
        
        context_system = build_system_context(
            working_dir=working_dir,
            skill_names=SKILLS,
            working_dir_need=True,
            instruction_need=True
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

        response = llm_with_tools.invoke(messages)

        response = mark_ai_message(
            response,
            turn_id=state["turn_id"],
        )
        #response = 
        logging.info(response)
        #print(response.content)

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
            "compression_session": compression_session,
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

    return builder.compile(checkpointer=checkpointer)

def run_tool_agent(
    question: str, 
    thread_id: str,
    profile_name: str = "qwen3.6",
    recursion_limit: int = 200,
    working_dir: str | None = None,
    context_window_tokens: int = 32768,
) -> str:
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:

        graph = build_graph(
            profile_name=profile_name,
            working_dir=working_dir,
            checkpointer=checkpointer,
            context_window_tokens=context_window_tokens,
            )
        config = {
                "configurable": {
                    "thread_id": thread_id,
                },
                "recursion_limit": recursion_limit,
            }
        
        snapshot = graph.get_state(config)
        old_messages = snapshot.values.get("messages", []) if snapshot.values else []

        turn_id = get_next_turn_id(old_messages)

        return graph.invoke(
            make_initial_state(question, turn_id=turn_id), 
            config=config
        )

async def arun_tool_agent(
    question: str,
    thread_id: str,
    profile_name: str = "qwen3.6",
    recursion_limit: int = 200,
    working_dir: str | None = None,
    context_window_tokens: int = 32768,
):
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:
        graph = build_graph(
            profile_name=profile_name,
            working_dir=working_dir,
            checkpointer=checkpointer,
            context_window_tokens=context_window_tokens,
        )

        config = {
            "configurable": {
                "thread_id": thread_id,
            },
            "recursion_limit": recursion_limit,
        }
        snapshot = await graph.aget_state(config)
        old_messages = snapshot.values.get("messages", []) if snapshot.values else []

        turn_id = get_next_turn_id(old_messages)

        return await graph.ainvoke(
            make_initial_state(question, turn_id=turn_id),
            config=config,
        )
