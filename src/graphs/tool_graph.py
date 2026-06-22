from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

from src.client.mymodel_client import build_client, load_profile, save_response_json

class ToolState(TypedDict):
    question: str
    tool_name: str
    tool_args: dict
    tool_result: str
    answer: str