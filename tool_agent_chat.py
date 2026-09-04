from __future__ import annotations

import asyncio
import sys
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.observability import setup_phoenix_tracing

setup_phoenix_tracing()

from src.client.mymodel_client import serialize_message
from src.graphs.tool_agent_graph import arun_tool_agent


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PROFILE = "deepseekv4-flash"
DEFAULT_VISION_PROFILE = "qwen3.8"
DEFAULT_CONTEXT_WINDOW = 32768
DEFAULT_RECURSION_LIMIT = 200


def extract_answer(result: dict[str, Any]) -> str:
    """Extract the final assistant text from a graph result."""
    messages = result.get("messages") or []
    if not messages:
        return ""

    last_message = messages[-1]
    if isinstance(last_message, dict):
        return str(last_message.get("content", ""))
    return str(getattr(last_message, "content", ""))


def make_thread_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"cli-{timestamp}-{uuid4().hex[:8]}"


def save_snapshot(
    result: dict[str, Any],
    thread_id: str,
    output_path: Path,
) -> None:
    data = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "thread_id": thread_id,
        "messages": [
            serialize_message(message)
            for message in result.get("messages") or []
        ],
        "compression_session": result.get("compression_session"),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, default=str)


def print_help() -> None:
    print(
        """
可用命令：
  /help             显示帮助
  /thread           显示当前 thread_id
  /new              创建一个新的空白会话
  /save             将当前会话导出为 JSON
  /exit 或 /quit    退出程序
"""
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在终端中与 tool_agent_graph 进行多轮对话",
    )
    parser.add_argument(
        "--thread-id",
        help="会话 ID；传入已有 ID 可从 checkpoint 继续对话",
    )
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help=f"主模型 profile（默认：{DEFAULT_PROFILE}）",
    )
    parser.add_argument(
        "--vision-profile",
        default=DEFAULT_VISION_PROFILE,
        help=f"图像理解模型 profile（默认：{DEFAULT_VISION_PROFILE}）",
    )
    parser.add_argument(
        "--working-dir",
        default=str(PROJECT_ROOT),
        help="Agent 的工作目录（默认：项目根目录）",
    )
    parser.add_argument(
        "--context-window",
        type=int,
        default=DEFAULT_CONTEXT_WINDOW,
        help=f"上下文窗口 token 数（默认：{DEFAULT_CONTEXT_WINDOW}）",
    )
    parser.add_argument(
        "--recursion-limit",
        type=int,
        default=DEFAULT_RECURSION_LIMIT,
        help=f"LangGraph 递归上限（默认：{DEFAULT_RECURSION_LIMIT}）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "cli_session_snapshot.json",
        help="执行 /save 时的 JSON 输出路径",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    thread_id = args.thread_id or make_thread_id()
    latest_result: dict[str, Any] | None = None

    print("LLM-Graph 命令行对话")
    print(f"主模型：{args.profile}")
    print(f"视觉模型：{args.vision_profile}")
    print(f"thread_id：{thread_id}")
    print(f"工作目录：{Path(args.working_dir).resolve()}")
    print("输入 /help 查看命令。")

    while True:
        try:
            question = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n对话已结束。")
            break

        if not question:
            continue

        command = question.lower()
        if command in {"/exit", "/quit"}:
            print("对话已结束。")
            break
        if command == "/help":
            print_help()
            continue
        if command == "/thread":
            print(f"当前 thread_id：{thread_id}")
            continue
        if command == "/new":
            thread_id = make_thread_id()
            latest_result = None
            print(f"已创建新会话：{thread_id}")
            continue
        if command == "/save":
            if latest_result is None:
                print("当前还没有可导出的对话。")
                continue
            save_snapshot(latest_result, thread_id, args.output)
            print(f"已导出到：{args.output.resolve()}")
            continue

        try:
            latest_result = await arun_tool_agent(
                question=question,
                thread_id=thread_id,
                profile_name=args.profile,
                vision_profile_name=args.vision_profile,
                recursion_limit=args.recursion_limit,
                working_dir=args.working_dir,
                context_window_tokens=args.context_window,
            )
            print(f"\nAgent> {extract_answer(latest_result)}")
        except Exception as error:
            logging.exception("Agent 调用失败")
            print(f"\nAgent 调用失败：{type(error).__name__}: {error}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=asyncio.SelectorEventLoop,
        )
    else:
        asyncio.run(main())
