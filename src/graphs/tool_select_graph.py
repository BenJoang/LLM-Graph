from typing_extensions import TypedDict
from typing import Annotated
from pathlib import Path



from src.client.mymodel_client import build_client, load_profile, load_prompt, save_response_json, save_response_md
from src.client.mymodel_client import build_chat_model
from src.client.mymodel_client import save_langchain_message_md
from src.tools import registry
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages




class ToolSelectState(TypedDict):
    question: str
    selected_tool_name: str
    selected_tool_args: dict
    raw_model_output: str

def select_tool_node(state: ToolSelectState) -> dict:
    profile = load_profile("qwen3.6")
    prompt = load_prompt("select_tool")
    llm = build_chat_model(profile, temperature=0)

    tools = registry.get_langchain_tools()

    llm_with_tools = llm.bind_tools(tools)

    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": state["question"]},
    ]

    response = llm_with_tools.invoke(messages)

    request_options = {
        "model": profile["model"],
        "temperature": 0,
        "base_url": profile["base_url"],
        "extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": False,
            }
        },
    }


    save_langchain_message_md(
        response,
        question=state["question"],
        messages=messages,
        tools=tools,
        request_options=request_options,
        filename="tool_select_langchain.md"
    )


    tool_calls = response.tool_calls

    if not tool_calls:
        return {
            "selected_tool_name": "",
            "selected_tool_args": {},
            "raw_model_output": response.model_dump_json(indent=2),
        }

    tool_call = tool_calls[0]

    return {
        "selected_tool_name": tool_call["name"],
        "selected_tool_args": tool_call["args"],
        "raw_model_output": response.model_dump_json(indent=2),
    }

def build_graph():
    builder = StateGraph(ToolSelectState)

    builder.add_node("select_tool", select_tool_node)

    builder.add_edge(START, "select_tool")
    builder.add_edge("select_tool", END)

    return builder.compile()

def make_initial_state(question: str) -> ToolSelectState:
    return {
        "question": question,
        "selected_tool_name": "",
        "selected_tool_args": {},
        "raw_model_output": "",
    }
    
graph = build_graph()
