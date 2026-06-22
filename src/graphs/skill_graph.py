from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

from src.client.mymodel_client import build_client, load_profile, save_response_json
from src.graphs.skill_store import read_skill

class SkillNode(TypedDict):
    question: str
    skill_name: str
    skill_content: str
    answer: str


def load_skill_node(state: SkillNode) -> dict :
    skill_content = read_skill(state["skill_name"])
    return {
        "skill_content": skill_content,
    }


def answer_node(state: SkillNode) -> dict:
    profile = load_profile("qwen3.6")
    client = build_client(profile)

    request_data = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": state["skill_content"]},
            {"role": "user","content": state["question"]},
        ],
        "extra_body":{
            "chat_template_kwargs":{
                "enable_thinking":True
                } 
             },
    }
    response = client.chat.completions.create(**request_data)
    save_response_json(response, state["question"], request_data)
    return {
        "answer": response.choices[0].message.content,
    }

def build_graph():
    builder = StateGraph(SkillNode)

    builder.add_node("load_skill", load_skill_node)
    builder.add_node("answer", answer_node)

    builder.add_edge(START, "load_skill")
    builder.add_edge("load_skill", "answer")
    builder.add_edge("answer", END)

    return builder.compile()

graph = build_graph()
