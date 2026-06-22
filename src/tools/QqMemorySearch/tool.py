from pathlib import Path
import json
from pydantic import BaseModel, Field


TOOL_NAME = "qq_memory_search"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent

BASE_DIR = Path(__file__).resolve().parents[3]
QQ_MEMORY_DIR = BASE_DIR / "memory" / "qq_memory" / "groups"

class InputSchema(BaseModel):
    group_index: str = Field(description="需要进行搜索的QQ群号")
    limit: int = Field(default=20, description="返回的消息数量，默认为20，最大为40")
    cursor: int | None = Field(default=None, description="从jsonl文件的第几行开始读取，默认为None表示从最新消息开始")

class QqMessageResult(BaseModel):
    datetime: str = Field(description="消息的日期时间，格式为 'YYYY-MM-DD HH:MM:SS'")
    line_index: int = Field(description="消息在jsonl文件中的行号")
    user_id: int | None = Field(default=None, description="发送消息的用户ID")
    display_name: str | None = Field(default=None, description="发送消息的用户显示名称")
    plain_text: str = Field(description="消息的纯文本内容")
    summary: str = Field(description="消息的摘要，格式为 '显示名称(用户ID): 消息内容'")
    image_urls: list[str] = Field(default_factory=list, description="消息中包含的图片URL列表")
    group_name: str | None = Field(default=None, description="消息所属的群名称")
    message_id: int | None = Field(default=None, description="消息ID")
    reply: dict | None = Field(default=None, description="如果该消息是回复消息，则包含被回复消息的相关信息，否则为None")

class SearchResult(BaseModel):
    group_index: str = Field(description="被搜索的QQ群号")
    messages: list[QqMessageResult] = Field(description="搜索到的消息列表")
    count: int = Field(description="搜索到的消息数量")
    has_more: bool = Field(description="是否还有更多消息可以搜索")
    next_cursor: int | None = Field(default=None, description="下一次搜索时使用的cursor值，如果has_more为True则不为None")
    total_lines: int = Field(description="jsonl文件中的总行数")
    start_cursor: int | None = Field(default=None, description="本次读取开始位置")


class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    data: SearchResult|None = Field(default=None, description="工具返回的结构化数据")



def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()
    
def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)

    return True, ""

def resolve_group_file(group_index: str) -> Path:
    return QQ_MEMORY_DIR / group_index / "dialog" / "messages.jsonl"


def call(**kwargs) -> dict:
    ok, error_message = validate_input(**kwargs)
    if not ok:
        return OutputSchema(
            ok=False,
            error=error_message,
            data=None,
        ).model_dump()

    try:
        input_data = InputSchema(**kwargs)

        group_file = resolve_group_file(input_data.group_index)

        if not group_file.exists():
            return OutputSchema(
                ok=False,
                error=f"QQ群 {input_data.group_index} 的消息记录不存在",
                data=None,
            ).model_dump()
        lines = group_file.read_text(encoding="utf-8").splitlines()
        total_lines = len(lines)

        if total_lines == 0:
            return OutputSchema(
                ok=True,
                error="",
                data=SearchResult(
                    group_index=input_data.group_index,
                    messages=[],
                    count=0,
                    has_more=False,
                    next_cursor=None,
                    total_lines=total_lines,
                    start_cursor=None,
                )
            ).model_dump()
        
        limit = input_data.limit
        if limit <= 0:
            limit = 20
        if limit > 40:
            limit = 40
        
        if input_data.cursor is None:
            start_index = total_lines - 1
        else:
            start_index = input_data.cursor - 2

        if start_index < 0:
            return OutputSchema(
                ok=True,
                error="",
                data=SearchResult(
                    group_index=input_data.group_index,
                    messages=[],
                    count=0,
                    has_more=False,
                    next_cursor=None,
                    total_lines=total_lines,
                    start_cursor=input_data.cursor,
                )
            ).model_dump()
        
        selected_items = []

        current_index = start_index

        while current_index >= 0 and len(selected_items) < limit:
            line = lines[current_index].strip() 
            
            if not line:
                current_index -= 1
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                current_index -= 1
                continue

            line_index = current_index + 1

            selected_items.append(
                build_message_result(record, line_index)
            )
            current_index -= 1
        
        oldest_line_index = selected_items[-1].line_index if selected_items else None
        has_more = current_index >= 0
        next_cursor = oldest_line_index if has_more else None
        selected_items.reverse()

        return OutputSchema(
            ok=True,
            error="",
            data=SearchResult(
                group_index=input_data.group_index,
                messages=selected_items,
                count=len(selected_items),
                has_more=has_more,
                next_cursor=next_cursor,
                total_lines=total_lines,
                start_cursor=input_data.cursor,
            )
        ).model_dump()
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

def build_message_result(record: dict, line_index: int) -> QqMessageResult:
    return QqMessageResult(
        datetime=record.get("datetime", ""),
        line_index=line_index,
        user_id=record.get("user_id"),
        display_name=record.get("display_name") or record.get("nickname") or record.get("card"),
        plain_text=record.get("plain_text", ""),
        summary=record.get("summary", ""),
        image_urls=extract_image_urls(record),
        group_name=record.get("group_name"),
        reply=record.get("reply"),
        message_id=record.get("message_id")
    )

def extract_image_urls(record: dict) -> list[str]:
    urls = []

    for seg in record.get("segments", []):
        if seg.get("type") == "image":
            data = seg.get("data", {})
            url = data.get("url")
            if url:
                urls.append(url)

    return urls

def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"工具执行失败：{output.error}"
    
    data = output.data
    if data is None:
        return "没有读取到群聊记录。"
    
    if data.start_cursor is None:
        total_count = data.count
    else:
        total_count = data.total_lines - data.start_cursor - data.count
    
    group_name = output.data.messages[0].group_name if output.data.messages else "未知群聊"

    lines = [
        f"到目前为止总共检索了{total_count}条记录\n"
        f"以下继续读取群 {data.group_index} 的 {data.count} 条记录。",
        f"total_lines: {data.total_lines}",
        f"start_cursor: {data.start_cursor}",
        f"has_more: {data.has_more}",
        f"next_cursor: {data.next_cursor}",
        f"群名: {group_name}",
        "消息记录：",
    ]

    for msg in data.messages:
        text = msg.plain_text if msg.plain_text else msg.summary
        

        lines.append(
            f"[第 {msg.line_index} 行] {msg.datetime} {msg.display_name}: {text}"
        )
        lines.append(
            f"messageid: {msg.message_id},reply:{msg.reply}"
        )

        if msg.image_urls:
            lines.append("image_urls:")
            for url in msg.image_urls:
                lines.append(f"- {url}")

        lines.append("")

    if data.has_more and data.next_cursor is not None:
        lines.append(
            f"如果当前记录不足以完成任务，请继续调用 qq_memory_search，并传入 cursor={data.next_cursor}。"
        )

    return "\n".join(lines)
