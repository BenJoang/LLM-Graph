from __future__ import annotations

import logging
import os
from typing import Literal, Annotated
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import NotRequired, TypedDict
from src.client.mymodel_client import save_graph_mdv2

from src.client.mymodel_client import (
    build_chat_model,
    load_profile,
    load_prompt
)

class ToolAgentState(TypedDict):
    url: str
    text: str
    vlmessages: Annotated[list, add_messages]
    llmmessages: Annotated[list, add_messages]
    result: bool

class ReviewResult(BaseModel):
    result: bool = Field(
        description="图片文字包含“科汇”或“Sibelius”时为 true，否则为 false"
    )

def make_initial_state(url: str, text: str) -> ToolAgentState:

    return {
        "url":url,
        "text": text,
        "llmmessages": [],
        "vlmessages": [],
        "result": False
    }


def build_graph(url: str, text:str, profile_name: str = "deepseekv4-flash",vision_profile_name: str = "qwen3.8"):
    vlprofile = load_profile(vision_profile_name)
    vlprompt = load_prompt("qq_image_review")

    llmprofile = load_profile(profile_name)
    llmprompt = load_prompt("qq_image_reviewllm")

    vlllm = build_chat_model(vlprofile, temperature=0)
    review_llm = build_chat_model(llmprofile, temperature=0)

    structured_review_llm = review_llm.with_structured_output(
        ReviewResult,
        method="function_calling",
        include_raw=True,
        extra_body={
            "thinking": {
                "type": "disabled"
            }
        },
    )

    async def image_read_node(state: ToolAgentState) -> dict:
        raw_messages = [
            {
                "role": "system",
                "content":vlprompt["system"]
            },
            {
                "role": "user",
                "content":[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url":state["url"]
                            },
                        "max_pixels": 1280 * 720,
                        "min_pixels": 640 * 360, 
                    },
                    {
                        "type": "text",
                        "text": state["text"]
                    }
                ]
            }
        ]
        response = await vlllm.ainvoke(raw_messages)
        save_graph_mdv2(
            event_type="model",
            node_name="vl",
            response=response,
            filename="qq_vl_graph_steps1.md",
        )

        return {
            "vlmessages": [response]
        }
    
    async def review_node(state: ToolAgentState) -> dict:
        raw_messages = [
            {
                "role": "system",
                "content":llmprompt["system"]
            },*state["vlmessages"],
            {
                "role": "user",
                "content":[
                    {
                    "type":"text",
                    "text":"如果这张图片中的文字包含'科汇'或者'Sibelius'的任意一个，result为true；不包含上述两者时result为false。"
                    }
                ]
            }
        ]   

        output = await structured_review_llm.ainvoke(raw_messages)
        parsed = output["parsed"]
        raw_response = output["raw"]

        save_graph_mdv2(
        event_type="model",
        node_name="review",
        response=raw_response,
        filename="qq_vl_graph_steps1.md",
        )

        return {
            "llmmessages": [raw_response],
            "result": parsed.result,
        }
    
    builder = StateGraph(ToolAgentState)
    builder.add_node("image", image_read_node)
    builder.add_node("review", review_node)

    builder.add_edge(START, "image")
    builder.add_edge("image", "review")
    builder.add_edge("review", END)

    return builder.compile()

async def run_qq_image_review_agent(url: str,
              text: str,
              profile_name: str = "deepseekv4-flash",
              vision_profile_name: str = "qwen3.8",
              recursion_limit: int = 30) -> str:
    
    graph = build_graph(
        url=url,
        text=text,
        profile_name=profile_name,
        vision_profile_name=vision_profile_name
    )
    result = await graph.ainvoke(
        make_initial_state(url=url, text=text),
        config={"recursion_limit": recursion_limit}
    )

    return result["result"]


