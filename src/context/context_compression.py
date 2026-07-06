from copy import deepcopy
from langchain_core.messages import HumanMessage
from uuid import uuid4
import json

from src.client.mymodel_client import (
    build_client,
    load_profile,
    load_prompt,
    chat_once_nothinking,
)
profile = load_profile("qwen3.6")
client = build_client(profile)
prompt = load_prompt("collapse_compact")

def summarize_context(text: str) -> str:
    return chat_once_nothinking(
        client=client,
        profile=profile,
        prompt=prompt,
        question=text,
        temperature=0,
    )

class MessageManage:
    def __init__(self, max_tokens: int = 32768):
        self.max_tokens = max_tokens

        self._snip_tokens = int(max_tokens * 0.50)
        self._summarize_tokens = int(max_tokens * 0.70)

        self.sessions = {}

        self.summarize_start = 2
        self.summarize_end = 4

        #self.max_tool_chars = 1500
        self.snip_cut_head = 3
        self.snip_cut_tail = 3

        self.cutoff = 2

    def prepare_messages_for_query(self, messages: list, session_key: str = "default") -> tuple[list, bool]:
        compressed = False
        current_tokens = self.estimate_tokens(messages)

        session = self._get_session(session_key)

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

        return messages_for_query, compressed

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
    
    def compress_for_retry(self, messages: list, level: int, original_messages: list) -> list:
        '''待完成'''
        if level == 1:
            return self._retry_snip_tool_messages(messages)

        if level == 2:
            return self._retry_snip_old_messages(messages)

        if level == 3:
            return self._retry_hard_trim(messages)

        return messages

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

            #if len(content) <= self.max_tool_chars:
            #    continue

            lines = content.splitlines()

            if len(lines) <= self.snip_cut_head + self.snip_cut_tail:
                continue

            removed = len(lines) - self.snip_cut_head - self.snip_cut_tail

            snipped = "\n".join(
                [
                    *lines[:self.snip_cut_head],
                    f"tool output snipped: {removed} lines removed",
                    *lines[-self.snip_cut_tail:]
                ]
            )

            self._set_content(msg, snipped)
            changed = True
        return changed
    
    def _apply_context_collapse(self, messages: list, session: dict) -> list:
        commits = session["collapse_commits"]
        if not commits:
            return messages
        
        commits_by_start = {
            commit["first_archived_id"]: commit
            for commit in commits
        }

        result = []
        i = 0

        while i < len(messages):
            msg_id = self._get_message_id(messages[i])

            if msg_id in commits_by_start:
                commit = commits_by_start[msg_id]
                append_message = HumanMessage(content=commit["summary_content"], id=commit["summary_id"])
                result.append(append_message)

                while i < len(messages):
                    current_id = self._get_message_id(messages[i])
                    if current_id == commit["last_archived_id"]:
                        i += 1
                        break
                    i += 1

                continue

            result.append(messages[i])
            i += 1

        return result

    
    def _create_context_collapse(self, messages: list, session: dict) -> bool:
        start_index = self.summarize_start
        end_index = len(messages) - self.summarize_end

        if end_index <= start_index:
            return False

        span = messages[start_index:end_index]

        summary_ids = {
            commit["summary_id"]
            for commit in session["collapse_commits"]
        }

        summary_messages = []
        summary_span = []

        for msg in span:
            msg_id = self._get_message_id(msg)

            if msg_id in summary_ids:
                summary_messages.append(msg)
            else:
                summary_span.append(msg)

        span_ids = [
            self._get_message_id(msg)
            for msg in summary_span
            if self._get_message_id(msg)
        ]

        if not span_ids:
            return False
        
        if any(msg_id in session["collapse_message_ids"] for msg_id in span_ids):
            return False
        
        summary = self._build_simple_summary(summary_span)
        if not summary:
            return False    
        
        summary_content = f"[历史上下文摘要]:{summary}"



        summary_msg = self._make_summary_message(summary_content)

        commit = {
            "collapse_id": str(uuid4()),
            "summary_id": summary_msg.id,
            "first_archived_id": span_ids[0],
            "last_archived_id": span_ids[-1],
            "summary": summary,
            "summary_content": summary_msg.content,
        }

        session["collapse_commits"].append(commit)
        session["collapse_message_ids"].update(span_ids)

        messages[start_index:end_index] = [*summary_messages, summary_msg]

        return True


    def _build_simple_summary(self, messages:list) -> str:
        text = self._format_messages_for_summary(messages)

        if not text:
            return ""
        
        return summarize_context(text)
    
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

    def _get_session(self, session_key: str):
        if session_key not in self.sessions:
            self.sessions[session_key] = {
                "collapse_commits":[],
                "collapse_message_ids": set()
            }
        return self.sessions[session_key]
    


    



    def _make_summary_message(self, summary_contene: str) -> HumanMessage:
        msg = HumanMessage(content = summary_contene)

        if not getattr(msg, "id", None):
            msg.id = str(uuid4())
        return msg
    
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

