from typing import Annotated
from typing_extensions import TypedDict
import logging

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.client.mymodel_client import save_agent_trace_md
from src.tools import registry

from src.graphs.context_compression import MessageManage

logging.basicConfig(level=logging.INFO)
message_manage = MessageManage()

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]

def make_initial_state(question: str) -> ToolAgentState:
    return {"messages": [{"role": "user", "content": question}]}

AGENT_TOOLS = ["read_file", "get_file", "imageread", "agenttool", "python_tool"]


def build_graph(profile_name: str = 'qwen3.6'):
    profile = load_profile(profile_name)
    prompt = load_prompt("tool_agent")
    tools = registry.get_langchain_tools_by_names(AGENT_TOOLS)

    llm = build_chat_model(profile, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    def assistant_node(state: ToolAgentState) -> dict:

        messages_for_query, compressed = message_manage.prepare_messages_for_query(
            state["messages"]
        )

        messages = [
            {"role": "system", "content": prompt["system"]},
            *messages_for_query,
        ]

        response = llm_with_tools.invoke(messages)

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

    return builder.compile()

def run_tool_agent(
    question: str, 
    profile_name: str = "qwen3.6",
    recursion_limit: int = 30
) -> str:
    graph = build_graph(profile_name=profile_name)

    return graph.invoke(
        make_initial_state(question), 
        config={"recursion_limit": recursion_limit}
    )
