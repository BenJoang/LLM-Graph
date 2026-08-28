from typing import Annotated
from typing_extensions import TypedDict, NotRequired
import logging

from src.persistence.checkpoints import open_async_checkpointer
from langchain_core.messages import (
    HumanMessage,
    SystemMessage,
)

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import tools_condition

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.tools import registry

from src.context.message_context import(
    get_next_turn_id,
    make_initial_state,
    build_turn_aware_tool_node,
)
from src.context.context_compression import MessageManage, CompressionSession
from src.context.context_builder import build_system_context
from src.context.invoke_retry import ainvoke_with_retry
from src.context.compression_retry_adapter import (
    CompressionRetryAdapter,
)

#logging.basicConfig(level=logging.INFO)

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn_id: int
    compression_session: NotRequired[CompressionSession]

AGENT_TOOLS = ["read_file", "get_file", "grep", "imageread", "agenttool", "python_tool", "skill_tool"]
SKILLS = ["wuxiwaterskill"]


def build_graph(
        profile_name: str = 'qwen3.6',
        vision_profile_name: str = "qwen3-vl",
        working_dir: str | None = None,
        checkpointer = None,
        context_window_tokens: int = 32768,
):
    profile = load_profile(profile_name)
    prompt = load_prompt("tool_agent")
    collapse_prompt = load_prompt("collapse_compact")
    subagent_tool_names = [
        name
        for name in AGENT_TOOLS
        if name != "agenttool"
    ]
    tools = registry.get_langchain_tools_by_names(
        AGENT_TOOLS,
        injected_by_tool={
            "agenttool": {
                "_profile_name": profile_name,
                "_vision_profile_name": vision_profile_name,
                "_working_dir": working_dir,
                "_context_window_tokens": context_window_tokens,
                "_recursion_limit": 200,
                "_tool_names": subagent_tool_names,
            },
            "imageread": {
                "_profile_name": vision_profile_name,
            },
        },
    )

    llm = build_chat_model(profile, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    async def summarize_with_main_model(text: str) -> str:
        response = await llm.ainvoke([
            SystemMessage(content=collapse_prompt["system"]),
            HumanMessage(content=text),
        ])

        return str(response.content)
    
    message_manage = MessageManage(
        max_tokens=context_window_tokens,
        asummarize_fn=summarize_with_main_model,
    )

    async def assistant_node(state: ToolAgentState) -> dict:
        (messages_for_query, compressed, compression_session,) = await message_manage.aprepare_messages_for_query(
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

        retry_adapter = CompressionRetryAdapter(
            message_manage=message_manage,
            compression_session=compression_session,
            current_turn_id=state["turn_id"],
        )

        response = await ainvoke_with_retry(
            invoke_fn=llm_with_tools.ainvoke,
            messages=messages,
            original_messages=messages,
            compress_fn=retry_adapter.acall,
            turn_id=state["turn_id"],
            max_context_retries=3,
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
            "compression_session": (
                retry_adapter.compression_session
            ),
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

async def astream_tool_agent(
    *,
    question: str,
    thread_id: str,
    profile_name: str,
    vision_profile_name: str,
    recursion_limit: int,
    working_dir: str | None,
    context_window_tokens: int,
):
    async with open_async_checkpointer() as checkpointer:
        graph = build_graph(
            profile_name=profile_name,
            vision_profile_name=vision_profile_name,
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
        old_messages = (
            snapshot.values.get("messages", [])
            if snapshot.values
            else []
        )
        turn_id = get_next_turn_id(old_messages)

        async for update in graph.astream(
            make_initial_state(question, turn_id=turn_id),
            config=config,
            stream_mode="updates",
        ):
            yield update

async def arun_tool_agent(
    question: str,
    thread_id: str,
    profile_name: str = "qwen3.6",
    vision_profile_name: str = "qwen3-vl",
    recursion_limit: int = 200,
    working_dir: str | None = None,
    context_window_tokens: int = 32768,
):
    async with open_async_checkpointer() as checkpointer:
        graph = build_graph(
            profile_name=profile_name,
            vision_profile_name=vision_profile_name,
            working_dir=working_dir,
            checkpointer=checkpointer,
            context_window_tokens=context_window_tokens,
        )

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit,
        }

        snapshot = await graph.aget_state(config)
        old_messages = (
            snapshot.values.get("messages", [])
            if snapshot.values
            else []
        )
        turn_id = get_next_turn_id(old_messages)

        return await graph.ainvoke(
            make_initial_state(question, turn_id=turn_id),
            config=config,
        )