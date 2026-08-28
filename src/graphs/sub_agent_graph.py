from typing import Annotated, Literal
from typing_extensions import TypedDict, NotRequired

from langgraph.managed import RemainingSteps
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
from src.context.message_context import(
    make_initial_state,
    build_turn_aware_tool_node,
)
from src.context.context_builder import build_system_context
from src.context.invoke_retry import ainvoke_with_retry
from src.context.compression_retry_adapter import CompressionRetryAdapter


class ToolAgentState(TypedDict):
    messages: Annotated[list, add_messages]
    turn_id: int
    remaining_steps: RemainingSteps
    status: NotRequired[Literal["running", "completed", "partial"]]
    stop_reason: NotRequired[str]
    compression_session: NotRequired[CompressionSession]


def build_graph(
        profile_name: str = "qwen3.6",
        vision_profile_name: str = "qwen3-vl",
        working_dir: str | None = None,
        context_window_tokens: int = 32768,
        tool_names: list[str] | None = None,
    ):
    profile = load_profile(profile_name)
    prompt = load_prompt("subagent")
    collapse_prompt = load_prompt("collapse_compact")
    context_system = build_system_context(
        working_dir=working_dir,
        skill_names=[],
        working_dir_need=True,
        instruction_need=True,
    )

    tools = registry.get_subagent_langchain_tools(
        parent_tool_names=tool_names,
        injected_by_tool={
            "imageread": {
                "_profile_name": vision_profile_name,
            },
        },
    )

    llm = build_chat_model(profile, temperature=0)
    llm_with_tools = llm.bind_tools(tools)

    async def summarize_with_main_model(text: str) -> str:
        response = await llm.ainvoke([
            SystemMessage(
                content=collapse_prompt["system"]
            ),
            HumanMessage(content=text),
        ])

        return str(response.content)
    
    message_manage = MessageManage(
        max_tokens=context_window_tokens,
        asummarize_fn=summarize_with_main_model,
    )

    async def assistant_node(state: ToolAgentState) -> dict:
        (
            messages_for_query,
            compressed,
            compression_session,
        ) = await message_manage.aprepare_messages_for_query(
            state["messages"],
            state.get("compression_session"),
        )

        remaining_steps = state["remaining_steps"]
        should_finalize = remaining_steps <= 3

        system_parts = [
            prompt["system"],
            context_system,
        ]

        if should_finalize:
            system_parts.append(
                """
    执行预算即将耗尽。

    禁止继续调用任何工具。
    请立即根据已经获得的信息返回阶段性调查结果。

    必须包含：
    1. 当前能够确认的结论
    2. 支持结论的文件路径、工具结果或其他证据
    3. 尚未完成或无法确认的部分
    4. 如果继续调查，建议下一步做什么

    不要因为任务没有完全完成而返回空结果。
    """
            )

        system_content = "\n\n".join(
            part for part in system_parts if part
        )

        messages = [
            {
                "role": "system",
                "content": system_content,
            },
            *messages_for_query,
        ]

        retry_adapter = CompressionRetryAdapter(
            message_manage=message_manage,
            compression_session=compression_session,
            current_turn_id=state["turn_id"],
        )

        # 接近限制时使用未绑定工具的模型，确保它不能继续调用工具
        invoke_model = (
            llm.ainvoke
            if should_finalize
            else llm_with_tools.ainvoke
        )

        response = await ainvoke_with_retry(
            invoke_fn=invoke_model,
            messages=messages,
            original_messages=messages,
            compress_fn=retry_adapter.acall,
            turn_id=state["turn_id"],
            max_context_retries=3,
        )

        if should_finalize:
            status = "partial"
            stop_reason = "step_limit"
        elif getattr(response, "tool_calls", None):
            status = "running"
            stop_reason = ""
        else:
            status = "completed"
            stop_reason = ""

        save_langchain_message_md(
            response,
            question=state["messages"][0].content,
            messages=messages,
            tools=tools,
            request_options={
                "model": profile["model"],
                "temperature": 0,
                "base_url": profile["base_url"],
                "remaining_steps": remaining_steps,
                "status": status,
            },
            filename="subagent_steps.md",
        )

        return {
            "messages": [response],
            "status": status,
            "stop_reason": stop_reason,
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
        }
    )

    builder.add_edge("tools", "assistant")

    return builder.compile()

