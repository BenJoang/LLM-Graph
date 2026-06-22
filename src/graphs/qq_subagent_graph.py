from typing import Annotated
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from src.client.mymodel_client import build_chat_model, load_profile, load_prompt, save_langchain_message_md
from src.client.mymodel_client import save_agent_trace_md
from src.tools import registry

class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]

profile = load_profile("qwen3.6")
prompt = load_prompt("youyouzi-subagent")

tools = registry.get_qq_memory_subagent_langchain_tools()

llm = build_chat_model(profile, temperature=0)
llm_with_tools = llm.bind_tools(tools)

def assistant_node(state: ToolAgentState) -> dict:

    messages = [
        {"role": "system", "content": prompt["system"]},
        *state["messages"],
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
        filename="qq_subagent_steps.md",
    )
    return {
        "messages": [response]
    }

def build_graph():
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
        }
    )

    builder.add_edge("tools", "assistant")

    return builder.compile()

graph = build_graph()

