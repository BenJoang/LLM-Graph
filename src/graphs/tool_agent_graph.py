from typing import Annotated
from typing_extensions import TypedDict
import logging
from pathlib import Path
import asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.tools import registry

from src.context.context_compression import MessageManage
from src.context.context_builder import build_system_context

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DB = PROJECT_ROOT / "outputs" / "checkpoints" / "tool_agent.sqlite"

#logging.basicConfig(level=logging.INFO)
message_manage = MessageManage()

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]

def make_initial_state(question: str) -> ToolAgentState:
    return {"messages": [{"role": "user", "content": question}]}

AGENT_TOOLS = ["read_file", "get_file", "imageread", "agenttool", "python_tool", "skill_tool"]
SKILLS = ["wuxiwaterskill"]


def build_graph(
        profile_name: str = 'qwen3.6',
        working_dir: str | None = None,
        checkpointer = None,
):
    profile = load_profile(profile_name)
    prompt = load_prompt("tool_agent")
    tools = registry.get_langchain_tools_by_names(
        AGENT_TOOLS,
        injected_by_tool={
            "agenttool": {
                "_profile_name": profile_name,
            }
        },
    )

    llm = build_chat_model(profile, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    def assistant_node(state: ToolAgentState) -> dict:
        messages_for_query, compressed = message_manage.prepare_messages_for_query(
            state["messages"]
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
        #logging.info(response.content)
        print(response.content)

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
            "messages": [response]
        }
                
    builder = StateGraph(ToolAgentState)

    builder.add_node("assistant", assistant_node)
    builder.add_node("tools", ToolNode(tools))

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
) -> str:
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:
        graph = build_graph(
            profile_name=profile_name,
            working_dir=working_dir,
            checkpointer=checkpointer
            )
        config = {
                "configurable": {
                    "thread_id": thread_id,
                },
                "recursion_limit": recursion_limit,
            }

        return graph.invoke(
            make_initial_state(question), 
            config=config
        )

async def arun_tool_agent(
    question: str,
    thread_id: str,
    profile_name: str = "qwen3.6",
    recursion_limit: int = 200,
    working_dir: str | None = None,
):
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as checkpointer:
        graph = build_graph(
            profile_name=profile_name,
            working_dir=working_dir,
            checkpointer=checkpointer,
        )

        config = {
            "configurable": {
                "thread_id": thread_id,
            },
            "recursion_limit": recursion_limit,
        }

        return await graph.ainvoke(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
        )
