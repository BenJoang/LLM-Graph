from copy import deepcopy
from langchain_core.messages import HumanMessage, AIMessage
from collections.abc import Callable
from typing_extensions import TypedDict
from uuid import uuid4
import json
from src.context.message_context import (
    get_message_turn_id,
    mark_ai_message,
)
from src.context.message_context import set_context_state


class CompressionSession(TypedDict):
    version: int
    collapse_commits: list[dict]
    collapse_message_ids: list[str]

def make_empty_compression_session() -> CompressionSession:
    return {
        "version": 1,
        "collapse_commits": [],
        "collapse_message_ids": [],
    }

def load_compression_session(session: CompressionSession | None) -> dict:
    session = session or make_empty_compression_session()

    return {
        "collapse_commits": deepcopy(
            session.get("collapse_commits", [])
        ),
        "collapse_message_ids": set(
            session.get("collapse_message_ids", [])
        ),
    }


def dump_compression_session(session: dict) -> CompressionSession:
    return {
        "version": 1,
        "collapse_commits": deepcopy(
            session["collapse_commits"]
        ),
        "collapse_message_ids": sorted(
            session["collapse_message_ids"]
        ),
    }

class MessageManage:
    def __init__(
            self, 
            max_tokens: int = 32768,
            summarize_fn: Callable[[str], str] | None = None,
            collapse_batch_size: int = 6,
        ):

        if collapse_batch_size < 2:
            raise ValueError("collapse_batch_size 必须大于等于 2")
        
        self.max_tokens = max_tokens
        self.summarize_fn = summarize_fn
        self.collapse_batch_size = collapse_batch_size

        self._snip_tokens = int(max_tokens * 0.50)
        self._summarize_tokens = int(max_tokens * 0.70)
        self.retry_target_tokens = int(max_tokens * 0.85)

        self.summarize_start = 2
        self.summarize_end = 4

        self.max_tool_chars = 4000
        self.snip_cut_head = 3
        self.snip_cut_tail = 3

        self.cutoff = 2

    def prepare_messages_for_query(self, messages: list, compression_session: CompressionSession | None = None) -> tuple[list, bool, CompressionSession]:
        compressed = False
        current_tokens = self.estimate_tokens(messages)

        session = load_compression_session(compression_session)

        #if current_tokens <= self._snip_tokens:
        #    return messages, compressed
        
        messages_for_query = deepcopy(messages)

        messages_for_query = self._apply_context_collapse(messages_for_query, session)
        current_tokens = self.estimate_tokens(messages_for_query)

        if current_tokens > self._snip_tokens:
            if self._history_snip(messages_for_query):
                compressed = True
                current_tokens = self.estimate_tokens(messages_for_query)

        if current_tokens > self._summarize_tokens:
            if self._create_context_collapse(messages_for_query, session):
                compressed = True
                current_tokens = self.estimate_tokens(messages_for_query)

        return (
            messages_for_query, 
            compressed,
            dump_compression_session(session),
        )

    def estimate_tokens(self, messages: list) -> int:
        chars = 0
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content")
            else:
                content = getattr(msg, "content", "")

            if content:
                chars += len(str(content))
        return chars // 2
    
    def compress_for_retry(
            self, 
            messages: list,  
            original_messages: list, 
            level: int,
            compression_session: CompressionSession | None,
            current_turn_id: int,
    ) -> tuple[list, CompressionSession, bool]:
        
        retry_messages = deepcopy(messages)
        session = load_compression_session(compression_session)
        changed = False
        '''待完成'''
        if level == 1:
            changed = self._retry_merge_summary_messages(
                retry_messages,
                session,
            )

        elif level == 2:
            changed = self._retry_snip_all_tool_messages(
                retry_messages,
            )

        elif level == 3:
            changed = self._retry_collapse_old_turns(
                messages=retry_messages,
                session=session,
                current_turn_id=current_turn_id,
            )
        else:
            raise ValueError(
                f"不支持的压缩重试等级：{level}，"
                "只允许 1、2、3"
            )
        return (
            retry_messages,
            dump_compression_session(session),
            changed,
        )

    def _history_snip(self, messages: list) -> bool:
        changed = False
        cutoff = max(0, len(messages) - self.cutoff)

        for index, msg in enumerate(messages):
            if index >= cutoff:
                continue

            if not self._is_tool_message(msg):
                continue
            content = self._get_content(msg)
            if not isinstance(content, str):
                continue

            if len(content) <= self.max_tool_chars:
                continue

            snipped_content, content_changed = (
                self._snip_tool_content(content)
            )

            if not content_changed:
                continue

            self._set_content(msg, snipped_content)
            changed = True
        return changed
    
    def _snip_tool_content(
        self,
        content: str,
    ) -> tuple[str, bool]:
        lines = content.splitlines()

        # 第一步：按行裁剪。
        if len(lines) > self.snip_cut_head + self.snip_cut_tail:
            removed_lines = (
                len(lines)
                - self.snip_cut_head
                - self.snip_cut_tail
            )

            snipped = "\n".join([
                *lines[:self.snip_cut_head],
                f"[tool output snipped: {removed_lines} lines removed]",
                *lines[-self.snip_cut_tail:],
            ])
        else:
            # 总行数不超过 6 行时，没有中间行可以删除。
            snipped = content

        # 第二步：按字符数硬截断。
        if len(snipped) > self.max_tool_chars:
            snipped = snipped[:self.max_tool_chars]

        return snipped, snipped != content
    
    def _apply_context_collapse(self, messages: list, session: dict) -> list:
        result = messages

        for commit in session["collapse_commits"]:
            source_ids = commit.get("source_message_ids", [])

            if not source_ids:
                # 旧版 commit 没有 source_message_ids。
                # 可以选择保留旧逻辑，或者清理旧 checkpoint。
                continue

            result_ids = [
                self._get_message_id(msg)
                for msg in result
            ]

            source_count = len(source_ids)
            matched_start = None

            for start in range(
                0,
                len(result_ids) - source_count + 1,
            ):
                if result_ids[start:start + source_count] == source_ids:
                    matched_start = start
                    break

            if matched_start is None:
                continue

            output_message_type = commit.get(
                "output_message_type",
                "ai",
            )

            if output_message_type == "human":
                summary_message = (
                    self._make_compressed_turn_message(
                        content=commit["summary_content"],
                        collapse_id=commit["collapse_id"],
                        turn_id=commit["turn_id"],
                        message_id=commit["summary_id"],
                    )
                )
            else:
                summary_message = self._make_summary_message(
                    summary_content=commit[
                        "summary_content"
                    ],
                    collapse_id=commit["collapse_id"],
                    summary_id=commit["summary_id"],
                    turn_id=commit.get("turn_id"),
                    summary_kind=commit.get(
                        "kind",
                        "normal",
                    ),
                )

            result[
                matched_start:matched_start + source_count
            ] = [summary_message]

        return result

    
    def _create_context_collapse(
            self, 
            messages: list, 
            session: dict
    ) -> bool:
        changed = False
        first_compression = True

        while (
            first_compression
            or self.estimate_tokens(messages) > self.max_tokens
        ):
            first_compression = False

            candidate = self._find_next_collapse_batch(
                messages=messages,
                session=session,
            )

            if candidate is None:
                break

            start_index, end_index, anchor_human = candidate
            source_messages = messages[start_index:end_index]
            turn_id = get_message_turn_id(anchor_human)

            # 压缩一条消息通常没有意义。
            if len(source_messages) <= 1:
                break

            source_ids = [
                self._get_message_id(msg)
                for msg in source_messages
            ]

            # 当前 commit 的恢复逻辑依赖 ID。
            if any(msg_id is None for msg_id in source_ids):
                break

            before_tokens = self.estimate_tokens(messages)

            # HumanMessage 只作为摘要背景，不会被替换。
            summary = self._build_simple_summary([
                anchor_human,
                *source_messages,
            ])

            if not summary:
                break

            collapse_id = str(uuid4())
            summary_content = f"[历史上下文摘要]:{summary}"

            summary_message = self._make_summary_message(
                summary_content=summary_content,
                collapse_id=collapse_id,
                turn_id=turn_id,
                summary_kind="normal",
            )

            # 先临时替换，检查摘要是否真的减少 token。
            messages[start_index:end_index] = [summary_message]
            after_tokens = self.estimate_tokens(messages)

            if after_tokens >= before_tokens:
                messages[start_index:start_index + 1] = source_messages
                break

            commit = {
                "collapse_id": collapse_id,
                "kind": "normal",
                "turn_id": turn_id,
                "summary_id": summary_message.id,
                "source_message_ids": source_ids,
                "first_archived_id": source_ids[0],
                "last_archived_id": source_ids[-1],
                "summary": summary,
                "summary_content": summary_message.content,
            }

            session["collapse_commits"].append(commit)
            session["collapse_message_ids"].update(source_ids)

            changed = True

            # 第一次压缩后，只有仍超过 max_tokens 才继续。
            if after_tokens <= self.max_tokens:
                break

        return changed
    
    def _retry_merge_summary_messages(
        self,
        messages: list,
        session: dict,
    ) -> bool:
        # 必须先冻结计划。
        plans = self._plan_retry_summary_merges(
            messages,
            session,
        )

        if not plans:
            return False

        changed = False

        for plan in plans:
            if self._execute_retry_summary_merge(
                messages,
                session,
                plan,
            ):
                changed = True

        return changed
    
    def _retry_snip_all_tool_messages(
        self,
        messages: list,
    ) -> bool:
        changed = False

        for message in messages:
            if not self._is_tool_message(message):
                continue

            content = self._get_content(message)

            if not isinstance(content, str):
                continue

            snipped_content, content_changed = (
                self._snip_tool_content(content)
            )

            if not content_changed:
                continue

            self._set_content(
                message,
                snipped_content,
            )
            changed = True

        return changed
    
    def _retry_collapse_old_turns(
        self,
        messages: list,
        session: dict,
        current_turn_id: int,
    ) -> bool:
        plans = self._plan_retry_turn_collapses(
            messages=messages,
            current_turn_id=current_turn_id,
        )

        if not plans:
            return False

        changed = False
        first_success = False

        for plan in plans:
            # 第一次成功压缩后，如果已经达到目标，
            # 就不再损失更多历史信息。
            if (
                first_success
                and self.estimate_tokens(messages)
                <= self.retry_target_tokens
            ):
                break

            success = (
                self._execute_retry_turn_collapse(
                    messages=messages,
                    session=session,
                    plan=plan,
                )
            )

            if not success:
                # 某轮摘要没有变短或结构失效，
                # 继续尝试下一轮，不能直接 break。
                continue

            first_success = True
            changed = True

        return changed
    
    def _find_next_collapse_batch(
        self,
        messages: list,
        session: dict,
    ) -> tuple[int, int, object] | None:
        protected_end = len(messages) - self.summarize_end

        if protected_end <= self.summarize_start:
            return None

        human_indexes = [
            index
            for index, msg in enumerate(messages)
            if self._is_human_message(msg)
        ]

        if not human_indexes:
            return None

        collapsed_ids = session["collapse_message_ids"]

        for position, human_index in enumerate(human_indexes):
            next_human_index = (
                human_indexes[position + 1]
                if position + 1 < len(human_indexes)
                else len(messages)
            )

            body_start = max(
                human_index + 1,
                self.summarize_start,
            )
            body_end = min(
                next_human_index,
                protected_end,
            )

            if body_end - body_start <= 1:
                continue

            # 摘要消息和已压缩消息都会成为分隔点，
            # 防止新批次跨过旧摘要。
            block_start = None

            for index in range(body_start, body_end + 1):
                reached_end = index == body_end

                if not reached_end:
                    msg = messages[index]
                    msg_id = self._get_message_id(msg)

                    compressible = (
                        not self._is_human_message(msg)
                        and not self._is_summary_message(msg, session)
                        and msg_id not in collapsed_ids
                    )
                else:
                    compressible = False

                if compressible and block_start is None:
                    block_start = index
                    continue

                if compressible:
                    continue

                if block_start is None:
                    continue

                block_end = index
                block_length = block_end - block_start

                if block_length > 1:
                    batch_end = min(
                        block_start + self.collapse_batch_size,
                        block_end,
                    )

                    batch_end = self._adjust_batch_for_tool_calls(
                        messages=messages,
                        start=block_start,
                        end=batch_end,
                        hard_end=block_end,
                    )

                    if batch_end is not None and batch_end - block_start > 1:
                        return (
                            block_start,
                            batch_end,
                            messages[human_index],
                        )

                block_start = None

        return None
    def _find_contiguous_summary_groups(
        self,
        messages: list,
        start_index: int,
        end_index: int,
        session: dict,
    ) -> list[list[str]]:
        groups = []
        current_group = []

        for message in messages[start_index:end_index]:
            if self._is_summary_message(message, session):
                message_id = self._get_message_id(message)

                if message_id is not None:
                    current_group.append(message_id)

                continue

            if len(current_group) > 1:
                groups.append(current_group)

            current_group = []

        if len(current_group) > 1:
            groups.append(current_group)

        return groups
    
    def _find_contiguous_message_ids(
        self,
        messages: list,
        source_ids: list[str],
    ) -> tuple[int, int] | None:
        message_ids = [
            self._get_message_id(message)
            for message in messages
        ]

        source_count = len(source_ids)

        for start in range(
            len(message_ids) - source_count + 1
        ):
            end = start + source_count

            if message_ids[start:end] == source_ids:
                return start, end

        return None
    
    def _plan_retry_summary_merges(
        self,
        messages: list,
        session: dict,
    ) -> list[dict]:
        human_indexes = [
            index
            for index, message in enumerate(messages)
            if self._is_human_message(message)
        ]

        if not human_indexes:
            return []

        plans = []

        for position, human_index in enumerate(human_indexes):
            next_human_index = (
                human_indexes[position + 1]
                if position + 1 < len(human_indexes)
                else len(messages)
            )

            anchor_human = messages[human_index]
            anchor_human_id = self._get_message_id(anchor_human)
            turn_id = get_message_turn_id(anchor_human)

            body_start = human_index + 1
            body_end = next_human_index

            # 在当前 HumanMessage 和下一个 HumanMessage 之间，
            # 查找包含至少两个摘要的连续摘要块。
            summary_groups = self._find_contiguous_summary_groups(
                messages=messages,
                start_index=body_start,
                end_index=body_end,
                session=session,
            )

            for source_ids in summary_groups:
                plans.append({
                    "turn_id": turn_id,
                    "anchor_human_id": anchor_human_id,
                    "source_message_ids": source_ids,
                })

        return plans
    
    def _plan_retry_turn_collapses(
        self,
        messages: list,
        current_turn_id: int,
    ) -> list[dict]:
        human_indexes = [
            index
            for index, message in enumerate(messages)
            if self._is_human_message(message)
        ]

        plans = []

        for position, human_index in enumerate(
            human_indexes
        ):
            human_message = messages[human_index]
            turn_id = get_message_turn_id(
                human_message
            )

            # 当前轮绝对不能处理。
            if turn_id == current_turn_id:
                continue

            # 如果你要求只处理明确的旧轮次，
            # 缺少 turn_id 的 HumanMessage 也跳过。
            if turn_id is None:
                continue

            # 正常情况下旧轮 turn_id 应小于当前轮。
            if turn_id >= current_turn_id:
                continue

            if self._is_compressed_turn_message(
                human_message
            ):
                continue

            next_human_index = (
                human_indexes[position + 1]
                if position + 1 < len(human_indexes)
                else len(messages)
            )

            turn_body = messages[
                human_index + 1:next_human_index
            ]

            # 必须恰好只有一个对应 AIMessage。
            if len(turn_body) != 1:
                continue

            ai_message = turn_body[0]

            if not self._is_ai_message(ai_message):
                continue

            human_id = self._get_message_id(
                human_message
            )
            ai_id = self._get_message_id(
                ai_message
            )

            if human_id is None or ai_id is None:
                continue

            plans.append({
                "turn_id": turn_id,
                "human_id": human_id,
                "ai_id": ai_id,
                "source_message_ids": [
                    human_id,
                    ai_id,
                ],
            })

        # human_indexes 本身是时间顺序，
        # 所以 plans 默认从最旧轮次开始。
        return plans
    
    def _adjust_batch_for_tool_calls(
        self,
        messages: list,
        start: int,
        end: int,
        hard_end: int,
    ) -> int | None:
        # 批次不能以 ToolMessage 开头，否则可能留下孤立工具结果。
        if self._is_tool_message(messages[start]):
            return None

        adjusted_end = end

        # 如果最后选择的是带 tool_calls 的 AIMessage，
        # 把它后面的 ToolMessage 一并包含进来。
        if self._has_tool_calls(messages[adjusted_end - 1]):
            while (
                adjusted_end < hard_end
                and self._is_tool_message(messages[adjusted_end])
            ):
                adjusted_end += 1

        # 如果批次已经包含一个 ToolMessage，
        # 将紧随其后的并行 ToolMessage 一起包含。
        while (
            adjusted_end < hard_end
            and self._is_tool_message(messages[adjusted_end - 1])
            and self._is_tool_message(messages[adjusted_end])
        ):
            adjusted_end += 1

        # 最终仍以带 tool_calls 的消息结尾，说明对应结果在保护区外。
        if self._has_tool_calls(messages[adjusted_end - 1]):
            return None

        # 下一条还是 ToolMessage，说明并行工具结果没有收完整。
        if (
            adjusted_end < len(messages)
            and self._is_tool_message(messages[adjusted_end - 1])
            and self._is_tool_message(messages[adjusted_end])
        ):
            return None

        return adjusted_end


    def _build_simple_summary(self, messages:list) -> str:
        text = self._format_messages_for_summary(messages)

        if not text:
            return ""

        if self.summarize_fn is None:
            raise RuntimeError("MessageManage 没有配置摘要模型")

        return self.summarize_fn(text)
    
    def _format_messages_for_summary(self, messages: list) -> str:
        parts = []

        for msg in messages:
            content = self._get_content(msg)
            if not content:
                continue

            role = self._message_role(msg)
            parts.append(
                {
                    "role": role,
                    "content": str(content)
                }
            )

        return json.dumps(parts, ensure_ascii=False, indent=2)



    def _make_summary_message(
        self,
        summary_content: str,
        collapse_id: str,
        summary_id: str | None = None,
        turn_id: int | None = None,
        summary_kind: str = "normal",
    ) -> AIMessage:
        
        message = AIMessage(
            content=summary_content,
            id=summary_id or str(uuid4()),
            response_metadata={
                "context_compression": {
                    "is_summary": True,
                    "collapse_id": collapse_id,
                    "summary_kind": summary_kind,
                }
            },
        )

        if turn_id is not None:
            mark_ai_message(message, turn_id=turn_id)

        return message
    
    def _make_compressed_turn_message(
        self,
        content: str,
        collapse_id: str,
        turn_id: int,
        message_id: str | None = None,
    ) -> HumanMessage:
        message = HumanMessage(
            content=content,
            id=message_id or str(uuid4()),
            response_metadata={
                "context_compression": {
                    "is_compressed_turn": True,
                    "collapse_id": collapse_id,
                    "summary_kind": "retry_turn_collapse",
                }
            },
        )

        set_context_state(
            message,
            turn_id=turn_id,
        )

        return message
    
    def _execute_retry_summary_merge(
        self,
        messages: list,
        session: dict,
        plan: dict,
    ) -> bool:
        source_ids = plan["source_message_ids"]

        if len(source_ids) <= 1:
            return False

        matched = self._find_contiguous_message_ids(
            messages,
            source_ids,
        )

        if matched is None:
            return False

        start_index, end_index = matched
        source_messages = messages[start_index:end_index]

        if not all(
            self._is_summary_message(message, session)
            for message in source_messages
        ):
            return False

        before_tokens = self.estimate_tokens(messages)

        summary = self._build_simple_summary(
            source_messages
        )

        if not summary:
            return False

        collapse_id = str(uuid4())
        summary_content = (
            f"[历史上下文合并摘要]:{summary}"
        )

        summary_message = self._make_summary_message(
            summary_content=summary_content,
            collapse_id=collapse_id,
            turn_id=plan["turn_id"],
            summary_kind="retry_merge",
        )

        messages[start_index:end_index] = [
            summary_message
        ]

        after_tokens = self.estimate_tokens(messages)

        # 摘要没有变短，恢复原消息。
        if after_tokens >= before_tokens:
            messages[
                start_index:start_index + 1
            ] = source_messages

            return False

        commit = {
            "collapse_id": collapse_id,
            "kind": "retry_merge",
            "turn_id": plan["turn_id"],
            "anchor_human_id": plan["anchor_human_id"],

            # Level 1 的 source 就是旧摘要的 ID。
            "source_message_ids": source_ids,
            "source_summary_ids": source_ids.copy(),

            "summary_id": summary_message.id,
            "summary": summary,
            "summary_content": summary_message.content,
        }

        session["collapse_commits"].append(commit)

        # 旧摘要已被新摘要消费。
        session["collapse_message_ids"].update(
            source_ids
        )

        # 不要将 summary_message.id 加入这里。
        # 它以后仍可再次参与合并。

        return True
    def _execute_retry_turn_collapse(
        self,
        messages: list,
        session: dict,
        plan: dict,
    ) -> bool:
        source_ids = plan["source_message_ids"]

        matched = self._find_contiguous_message_ids(
            messages,
            source_ids,
        )

        if matched is None:
            return False

        start_index, end_index = matched
        source_messages = messages[start_index:end_index]

        if len(source_messages) != 2:
            return False

        human_message, ai_message = source_messages

        if not self._is_human_message(human_message):
            return False

        if not self._is_ai_message(ai_message):
            return False

        if self._is_compressed_turn_message(human_message):
            return False

        before_tokens = self.estimate_tokens(messages)
        summary = self._build_simple_summary(source_messages)

        if not summary:
            return False

        collapse_id = str(uuid4())

        compressed_content = (
            "[历史轮次压缩摘要]\n"
            f"{summary}"
        )

        compressed_human = (
            self._make_compressed_turn_message(
                content=compressed_content,
                collapse_id=collapse_id,
                turn_id=plan["turn_id"],
            )
        )

        messages[start_index:end_index] = [compressed_human]
        after_tokens = self.estimate_tokens(messages)

        if after_tokens >= before_tokens:
            messages[
                start_index:start_index + 1
            ] = source_messages

            return False

        commit = {
            "collapse_id": collapse_id,
            "kind": "retry_turn_collapse",
            "turn_id": plan["turn_id"],
            "source_message_ids": source_ids,
            "summary_id": compressed_human.id,
            "summary": summary,
            "summary_content": (compressed_human.content),
            "output_message_type": "human",
        }

        session["collapse_commits"].append(commit)

        session["collapse_message_ids"].update(source_ids)

        # compressed_human.id 不加入 consumed IDs。
        # 是否禁止重复压缩由 is_compressed_turn 标签负责。

        return True
    @staticmethod
    def _message_role(msg):
        if isinstance(msg, dict):
            return msg.get("role", "unknown")

        name = msg.__class__.__name__
        if name == "HumanMessage":
            return "user"
        if name == "AIMessage":
            return "assistant"
        if name == "ToolMessage":
            return "tool"
        if name == "SystemMessage":
            return "system"

        return name
    @staticmethod
    def _get_message_id(msg):
        if isinstance(msg, dict):
            return msg.get("id")
        return getattr(msg, "id", None)
    
    @staticmethod
    def _is_tool_message(msg) -> bool:
        if isinstance(msg, dict):
            return msg.get("role") == "tool"
        return msg.__class__.__name__ == "ToolMessage"
    
    @staticmethod
    def _is_human_message(msg) -> bool:
        if isinstance(msg, dict):
            return msg.get("role") in {"user", "human"}

        return msg.__class__.__name__ == "HumanMessage"
    
    @staticmethod
    def _is_ai_message(message) -> bool:
        if isinstance(message, dict):
            return message.get("role") in {
                "assistant",
                "ai",
            }

        return (
            message.__class__.__name__
            == "AIMessage"
        )
    
    @staticmethod
    def _is_summary_message(msg, session: dict) -> bool:
        msg_id = MessageManage._get_message_id(msg)

        summary_ids = {
            commit.get("summary_id")
            for commit in session["collapse_commits"]
        }

        if msg_id in summary_ids:
            return True

        if isinstance(msg, dict):
            metadata = msg.get("response_metadata", {}) or {}
        else:
            metadata = getattr(msg, "response_metadata", {}) or {}

        compression = metadata.get("context_compression", {}) or {}
        return compression.get("is_summary") is True
    
    @staticmethod
    def _is_compressed_turn_message(message) -> bool:
        if isinstance(message, dict):
            metadata = message.get(
                "response_metadata",
                {},
            ) or {}
        else:
            metadata = getattr(
                message,
                "response_metadata",
                {},
            ) or {}

        compression = metadata.get(
            "context_compression",
            {},
        ) or {}

        return (
            compression.get("is_compressed_turn")
            is True
        )
    
    @staticmethod
    def _has_tool_calls(msg) -> bool:
        if isinstance(msg, dict):
            return bool(msg.get("tool_calls"))

        return bool(getattr(msg, "tool_calls", None))
    
    @staticmethod
    def _get_content(msg):
        if isinstance(msg, dict):
            return msg.get("content")
        return getattr(msg, "content", None)
    
    @staticmethod
    def _set_content(msg, content: str):
        if isinstance(msg, dict):
            msg["content"] = content
            return
        msg.content = content

