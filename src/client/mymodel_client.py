import json
from pathlib import Path
from dotenv import load_dotenv
import os

from openai import OpenAI
from datetime import datetime
from langchain_openai import ChatOpenAI

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "config"
OUTPUT_DIR = BASE_DIR / "outputs"

load_dotenv(BASE_DIR / ".env")

def load_profile(profile_name: str) -> dict:
    config_path = CONFIG_DIR / "user_config.json"
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    profile = dict(config["profiles"][profile_name])

    base_url_env = profile.pop("base_url_env", None)
    if base_url_env:
        base_url = os.getenv(base_url_env)
        if not base_url:
            raise ValueError(f"Missing environment variable: {base_url_env}")
        profile["base_url"] = base_url
    return profile

def load_prompt(prompt_name: str) -> dict:
    config_path = CONFIG_DIR / "prompt_config.json"
    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)
    prompt = dict(config["prompts"][prompt_name])

    system_file = prompt.get("system_file")
    if system_file:
        system_path = Path(system_file)
        if not system_path.is_absolute():
            system_path = CONFIG_DIR / system_path
        prompt["system"] = system_path.read_text(encoding="utf-8")

    return prompt

def build_client(profile: dict, timeout: int = 60) -> OpenAI:
    return OpenAI(
        base_url=profile["base_url"],
        api_key="EMPTY",
        timeout=timeout,
    )

def build_chat_model(profile: dict, temperature: float = 0):
    return ChatOpenAI(
        model=profile["model"],
        base_url=profile["base_url"],
        api_key="EMPTY",
        temperature=temperature,
        extra_body={
            "chat_template_kwargs": {
                "enable_thinking": False
            }
        },
    )

def test_connection(client: OpenAI) -> bool:
    try:
        models = client.models.list()
        return True
    except Exception as e:
        print("连接失败")
        print("错误类型:", type(e).__name__)
        print("错误信息:", e)
        return False
    
def append_record(record: dict, filename: str = "last_response.json") -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    if output_path.exists():
        with output_path.open("r", encoding="utf-8") as f:
            history = json.load(f)

        if isinstance(history, list):
            history = {
                "version": "1.0",
                "records": history
            }
        elif not isinstance(history, dict) or "records" not in history:
            history = {
                "version": "1.0",
                "records": [
                    {
                        "created_at": None,
                        "question": None,
                        "response_type": "legacy",
                        "response": history
                    }
                ]
            }
    else:
        history = {
            "version": "1.0",
            "records": []
        }

    history["records"].append(record)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


    
def save_response_json(response, 
                       question: str, 
                       request_data: dict | None = None, 
                       filename: str = "last_response.json"
                       ) -> None:
    data = response.model_dump()
    message = data["choices"][0]["message"]

    if request_data is not None:
        for msg in request_data.get("messages", []):
            content = msg.get("content")
            if isinstance(content, str):
                msg["content_lines"] = content.splitlines()

    content = message.get("content")
    reasoning = message.get("reasoning")

    if content is not None:
        message["content_lines"] = content.splitlines()

    if reasoning is not None:
        message["reasoning_lines"] = reasoning.splitlines()

    record = {
        "created_at" : datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "request_data": request_data,
        "response_type": "normal",
        "response": data
    }

    append_record(record, filename)

def save_response_md(
    response,
    question: str,
    request_data: dict | None = None,
    filename: str = "last_response.md",
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    data = response.model_dump()
    message = data["choices"][0]["message"]

    content = message.get("content")
    reasoning = message.get("reasoning")

    request_messages_text = ""
    if request_data is not None:
        for msg in request_data.get("messages", []):
            role = msg.get("role", "")
            msg_content = msg.get("content", "")
            msg_content = msg_content.replace("\\n", "\n")
            request_messages_text += f"""
### {role}

```text
{msg_content}
```

"""

    metadata = {
        "id": data.get("id"),
        "model": data.get("model"),
        "object": data.get("object"),
        "created": data.get("created"),
        "usage": data.get("usage"),
    }
    metadata_text = json.dumps(metadata, ensure_ascii=False, indent=2)

    parsed_content_text = ""
    parse_error = ""

    if content:
        try:
            parsed_content = json.loads(content)
            parsed_content_text = json.dumps(parsed_content, ensure_ascii=False, indent=2)
        except Exception as e:
            parse_error = f"{type(e).__name__}: {e}"

    record = f"""
# Response Record

## Created At
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
## Question
{question}
## Parse Error
```text
{parse_error}
```
## Assistant Reasoning
```text
{reasoning or ""}
```
## Request Messages
{request_messages_text}
## Metadata
```json
{metadata_text}
```
## Model Answer
```text
{content or ""}
```
## Parsed Assistant Content
```json
{parsed_content_text}
```
---

"""

    with output_path.open("a", encoding="utf-8") as f:
        f.write(record)

def save_langchain_message_md(
    message,
    question: str,
    messages: list | None = None,
    tools: list | None = None,
    request_options: dict | None = None,
    filename: str = "last_langchain_message.md",
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    content = getattr(message, "content", "")
    tool_calls = getattr(message, "tool_calls", [])
    additional_kwargs = getattr(message, "additional_kwargs", {})
    response_metadata = getattr(message, "response_metadata", {})

    input_messages_text = ""
    if messages is not None:
        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "")
                msg_content = msg.get("content", "")
            else:
                role = msg.__class__.__name__
                msg_content = getattr(msg, "content", "")

            input_messages_text += f"""
### {role}
```text
{msg_content}
```
"""
    tool_info = []
    if tools is not None:
        for tool in tools:
            args_schema = getattr(tool, "args_schema", None)
            if args_schema is not None and hasattr(args_schema, "model_json_schema"):
                args_schema_data = args_schema.model_json_schema()
            else:
                args_schema_data = None

            tool_info.append({
                "name": getattr(tool, "name", ""),
                "description": getattr(tool, "description", ""),
                "args_schema": args_schema_data,
            })

    tool_calls_text = json.dumps(tool_calls, ensure_ascii=False, indent=2)
    tool_info_text = json.dumps(tool_info, ensure_ascii=False, indent=2)
    additional_kwargs_text = json.dumps(additional_kwargs, ensure_ascii=False, indent=2)
    response_metadata_text = json.dumps(response_metadata, ensure_ascii=False, indent=2)
    request_view = {
        "messages": [serialize_message(msg) for msg in messages],
        "tools": tool_info,
    }
    if request_options is not None:
        request_view = {
            **request_options,
            **request_view,
        }
    request_view_text = json.dumps(request_view, ensure_ascii=False, indent=2, default=str)

    record = f"""
# LangChain Message Record
## Created At
{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
## Question
{question}
## Input Messages
{input_messages_text}
## Bound Tools
```json
{tool_info_text}
```
## Model Content
```text
{content}
```
## Tool Calls
```json
{tool_calls_text}
```
## Additional Kwargs
```json
{additional_kwargs_text}
```
## Response Metadata
```json
{response_metadata_text}
```
---

"""

    with output_path.open("a", encoding="utf-8") as f:
        f.write(record)

def shorten_text(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "\n...[日志显示已截断]"


def save_graph_mdv2(
    *,
    event_type: str,
    node_name: str,
    filename: str = "qq_main_graph_steps.md",
    question: str = "",
    group_id: str = "",
    part_history: str = "",
    response=None,
    tool_messages: list | None = None,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    sections = []

    if event_type == "run_start":
        sections.extend([
            "# New Request",
            f"## Created At\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"## Group ID\n{group_id}",
            f"## Question\n```text\n{question}\n```",
            f"## Part History\n```text\n{shorten_text(part_history)}\n```",
        ])

    elif event_type == "model":
        content = getattr(response, "content", "")
        tool_calls = getattr(response, "tool_calls", [])
        usage = getattr(response, "usage_metadata", None)

        sections.append(f"### {node_name}: Model Response")

        if content:
            sections.append(
                f"#### Content\n```text\n{shorten_text(content)}\n```"
            )

        if tool_calls:
            sections.append(
                "#### Tool Calls\n"
                f"```json\n"
                f"{json.dumps(tool_calls, ensure_ascii=False, indent=2)}\n"
                f"```"
            )

        if usage:
            sections.append(
                "#### Token Usage\n"
                f"```json\n"
                f"{json.dumps(usage, ensure_ascii=False, indent=2)}\n"
                f"```"
            )

    elif event_type == "tools":
        sections.append(f"### {node_name}: Tool Result")

        for message in tool_messages or []:
            data = serialize_message(message)

            metadata = {
                "name": data.get("name"),
                "tool_call_id": data.get("tool_call_id"),
            }

            sections.append(
                f"#### Tool Metadata\n"
                f"```json\n"
                f"{json.dumps(metadata, ensure_ascii=False, indent=2)}\n"
                f"```"
            )

            sections.append(
                f"#### Content\n"
                f"```text\n{shorten_text(data.get('content', ''))}\n```"
            )

    elif event_type == "run_end":
        sections.append("---")

    with output_path.open("a", encoding="utf-8") as f:
        f.write("\n\n".join(sections))
        f.write("\n\n")

def serialize_message(msg):
    if isinstance(msg, dict):
        return msg

    msg_type = msg.__class__.__name__
    content = getattr(msg, "content", "")

    if msg_type == "HumanMessage":
        return {"role": "user", "content": content}

    if msg_type == "AIMessage":
        data = {"role": "assistant", "content": content}
        tool_calls = getattr(msg, "tool_calls", None)
        response_metadata = getattr(msg, "response_metadata", None) or {}
        if tool_calls:
            data["tool_calls"] = tool_calls
        data['response_metadata'] = response_metadata
        return data

    if msg_type == "ToolMessage":
        return {
            "role": "tool",
            "content": content,
            "name": getattr(msg, "name", None),
            "tool_call_id": getattr(msg, "tool_call_id", None),
            "response_metadata": getattr(msg, "response_metadata", None)
        }

    if msg_type == "SystemMessage":
        return {"role": "system", "content": content}

    return {
        "role": msg_type,
        "content": content,
    }

def save_agent_trace_md(
    messages: list,
    filename: str = "tool_agent_trace.md",
    max_content_chars: int = 2000,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / filename

    sections = [
        "# Agent Trace",
        "",
        f"## Created At\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"## Message Count\n{len(messages)}",
        "",
    ]

    total_input_tokens = 0
    total_output_tokens = 0
    total_tokens = 0

    for index, msg in enumerate(messages, start=1):
        msg_type = msg.__class__.__name__
        content = getattr(msg, "content", "")
        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        tool_calls = getattr(msg, "tool_calls", None)
        response_metadata = getattr(msg, "response_metadata", {}) or {}
        usage_metadata = getattr(msg, "usage_metadata", {}) or {}

        if isinstance(content, str) and len(content) > max_content_chars:
            content_text = (
                content[:max_content_chars]
                + f"\n\n... [truncated {len(content) - max_content_chars} chars] ..."
            )
        else:
            content_text = content

        token_usage = response_metadata.get("token_usage", {})
        input_tokens = (
            token_usage.get("prompt_tokens")
            or usage_metadata.get("input_tokens")
            or 0
        )
        output_tokens = (
            token_usage.get("completion_tokens")
            or usage_metadata.get("output_tokens")
            or 0
        )
        msg_total_tokens = (
            token_usage.get("total_tokens")
            or usage_metadata.get("total_tokens")
            or 0
        )

        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_tokens += msg_total_tokens

        sections.extend([
            f"## Step {index}: {msg_type}",
            "",
        ])

        if name is not None:
            sections.extend([
                f"- name: `{name}`",
            ])

        if tool_call_id is not None:
            sections.extend([
                f"- tool_call_id: `{tool_call_id}`",
            ])

        if input_tokens or output_tokens or msg_total_tokens:
            sections.extend([
                f"- tokens: input={input_tokens}, output={output_tokens}, total={msg_total_tokens}",
            ])

        if tool_calls:
            tool_calls_text = json.dumps(tool_calls, ensure_ascii=False, indent=2)
            sections.extend([
                "",
                "### Tool Calls",
                "",
                "```json",
                tool_calls_text,
                "```",
            ])

        if content_text:
            sections.extend([
                "",
                "### Content",
                "",
                "```text",
                str(content_text),
                "```",
            ])

        sections.append("")

    sections.extend([
        "## Token Summary",
        "",
        f"- input_tokens: {total_input_tokens}",
        f"- output_tokens: {total_output_tokens}",
        f"- total_tokens: {total_tokens}",
        "",
        "---",
        "",
    ])

    with output_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(sections))

def save_stream_json(
    question: str,
    content: str,
    request_data: dict | None = None,
    usage: dict | None = None,
    filename: str = "last_response.json",
) -> None:
    record = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "request_data": request_data,
        "response_type": "stream",
        "response": {
            "object": "chat.completion.stream_result",
            "choices": [
                {
                    "index": 0,
                    "message": {
                    "role": "assistant",
                    "content": content,
                    "content_lines": content.splitlines(),
                    },
                    "finish_reason": "stop",
                },
            ],
            "usage": usage,
        },
    }

    append_record(record, filename)



def chat_once_thinking(client: OpenAI, profile: dict, prompt: dict, question: str) -> str:
    request_data = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": question},
        ],
        "temperature": 0.2,
    }
    response = client.chat.completions.create(**request_data)
    save_response_json(response, question, request_data)
    return response.choices[0].message.content

def chat_once_nothinking(client: OpenAI, profile: dict, prompt: dict, question: str, temperature: float = 1.5) -> str:
    request_data = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": question},
        ],
        "extra_body":{
            "chat_template_kwargs":{
                "enable_thinking":False
                } 
            },
        "temperature": temperature,
    }
    response = client.chat.completions.create(**request_data)
    save_response_json(response, question, request_data)
    return response.choices[0].message.content


def chat_stream_nothinking(client: OpenAI, profile: dict, prompt: dict, question: str, temperature: float = 1.5):
    request_data = {
        "model": profile["model"],
        "messages": [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": question},
        ],
        "extra_body":{
            "chat_template_kwargs":{
                "enable_thinking":False
                } 
            },
        "temperature": temperature,
        "stream_options": {"include_usage": True},
        "stream": True,
    }
    response = client.chat.completions.create(**request_data)
    content_parts = []
    usage = None

    for chunk in response:
        if chunk.choices:
            content = chunk.choices[0].delta.content or ""
            print(content, end="", flush=True)
            content_parts.append(content)
        elif chunk.usage:
            usage = chunk.usage.model_dump()

    full_response = "".join(content_parts)

    save_stream_json(
        question=question,
        content=full_response,
        usage=usage,
        request_data=request_data
    )

    return full_response
