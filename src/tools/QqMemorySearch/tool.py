from pathlib import Path
import json
from pydantic import BaseModel, Field
import base64
import mmap


TOOL_NAME = "qq_memory_search"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent

BASE_DIR = Path(__file__).resolve().parents[3]
QQ_MEMORY_DIR = BASE_DIR / "memory" / "qq_memory" / "groups"

MAX_SCAN_LINES = 1000

class InputSchema(BaseModel):
    group_index: str = Field(
        pattern=r"^\d+$",
        description="需要进行搜索的QQ群号",
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="返回的消息数量，默认为20，最大为100",
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "不透明分页游标。第一次调用留空；"
            "继续翻页时必须原样传回 next_cursor"
        ),
    )

class QqMessageResult(BaseModel):
    datetime: str = Field(description="消息的日期时间，格式为 'YYYY-MM-DD HH:MM:SS'")
    user_id: int | None = Field(default=None, description="发送消息的用户ID")
    display_name: str | None = Field(default=None, description="发送消息的用户显示名称")
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
    next_cursor: str | None = Field(default=None, description="下一次搜索时使用的cursor值，如果has_more为True则不为None")
    scanned_count: int = Field(description="本次检索链累计向前扫描了多少条")
    start_cursor: str | None = Field(default=None, description="本次读取开始位置")
    file_size: int = Field(description="读取开始时的jsonl文件字节数")


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
    if not group_index.isdigit():
        raise ValueError("QQ群号必须是纯数字")

    root = QQ_MEMORY_DIR.resolve()
    path = (
        root
        / group_index
        / "dialog"
        / "messages.jsonl"
    ).resolve()

    if root not in path.parents:
        raise ValueError("群聊记录路径越界")

    return path

def encode_cursor(
    group_index: str,
    offset: int,
    scanned_count: int,
) -> str:
    raw = f"{group_index}:{offset}:{scanned_count}"
    return base64.urlsafe_b64encode(
        raw.encode("utf-8")
    ).decode("ascii")


def decode_cursor(
    cursor: str | None,
    *,
    group_index: str,
    file_size: int,
) -> tuple[int, int]:
    if cursor is None:
        return file_size, 0

    try:
        raw = base64.urlsafe_b64decode(
            cursor.encode("ascii")
        ).decode("utf-8")

        cursor_group, offset_text, scanned_text = (
            raw.split(":", 2)
        )

        offset = int(offset_text)
        scanned_count = int(scanned_text)
    except Exception as exc:
        raise ValueError("cursor 格式不正确") from exc

    if cursor_group != group_index:
        raise ValueError("cursor 不属于当前 QQ 群")

    if not 0 <= offset <= file_size:
        raise ValueError("cursor 字节位置已经失效")

    if not 0 <= scanned_count <= MAX_SCAN_LINES:
        raise ValueError("cursor 扫描数量不正确")

    return offset, scanned_count

def read_reverse_page(
    file_path: Path,
    *,
    group_index: str,
    cursor: str | None,
    limit: int,
) -> tuple[
    list[QqMessageResult],
    str | None,
    bool,
    int,
    int,
]:
    if not 1 <= limit <= 100:
        raise ValueError("limit 必须在 1 到 100 之间")

    with file_path.open("rb") as file:
        file.seek(0, 2)
        file_size = file.tell()

        if file_size == 0:
            return [], None, False, 0, 0

        position, already_scanned = decode_cursor(
            cursor,
            group_index=group_index,
            file_size=file_size,
        )

        remaining = MAX_SCAN_LINES - already_scanned

        if remaining <= 0:
            return [], None, False, already_scanned, file_size

        page_limit = min(limit, remaining)

        results: list[QqMessageResult] = []
        scanned_this_page = 0

        with mmap.mmap(
            file.fileno(),
            length=0,
            access=mmap.ACCESS_READ,
        ) as data:
            while (
                position > 0
                and len(results) < page_limit
                and scanned_this_page < remaining
            ):
                line_end = position

                # 跳过当前行之前的 CR/LF
                while (
                    line_end > 0
                    and data[line_end - 1] in (10, 13)
                ):
                    line_end -= 1

                if line_end == 0:
                    position = 0
                    break

                newline_position = data.rfind(
                    b"\n",
                    0,
                    line_end,
                )
                line_start = newline_position + 1

                raw_line = data[line_start:line_end]
                position = line_start
                scanned_this_page += 1

                if not raw_line.strip():
                    continue

                try:
                    record = json.loads(
                        raw_line.decode("utf-8")
                    )
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ):
                    continue

                results.append(
                    build_message_result(record)
                )

    # 反向读取时是“新到旧”，返回给模型前恢复成“旧到新”
    results.reverse()

    scanned_count = (
        already_scanned + scanned_this_page
    )

    has_more = (
        position > 0
        and scanned_count < MAX_SCAN_LINES
    )

    next_cursor = (
        encode_cursor(
            group_index,
            position,
            scanned_count,
        )
        if has_more
        else None
    )

    return (
        results,
        next_cursor,
        has_more,
        scanned_count,
        file_size,
    )


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

        (
            messages,
            next_cursor,
            has_more,
            scanned_count,
            file_size,
        ) = read_reverse_page(
            group_file,
            group_index=input_data.group_index,
            cursor=input_data.cursor,
            limit=input_data.limit,
        )

        return OutputSchema(
            ok=True,
            error="",
            data=SearchResult(
                group_index=input_data.group_index,
                messages=messages,
                count=len(messages),
                has_more=has_more,
                next_cursor=next_cursor,
                scanned_count=scanned_count,
                start_cursor=input_data.cursor,
                file_size=file_size,
            ),
        ).model_dump()

    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            data=None,
        ).model_dump()

def build_message_result(
    record: dict,
) -> QqMessageResult:
    return QqMessageResult(
        datetime=record.get("datetime", ""),
        user_id=record.get("user_id"),
        display_name=(
            record.get("display_name")
            or record.get("nickname")
            or record.get("card")
        ),
        summary=record.get("summary", ""),
        image_urls=extract_image_urls(record),
        group_name=record.get("group_name"),
        reply=record.get("reply"),
        message_id=record.get("message_id"),
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
    
    lines = [
        f"本轮返回群 {data.group_index} 的 {data.count} 条记录。",
        f"本次检索链累计向前扫描了 {data.scanned_count} 条记录。",
        f"has_more: {data.has_more}",
        f"next_cursor: {data.next_cursor}",
        "消息记录：",
    ]

    for msg in data.messages:
        text = msg.summary
        

        lines.append(
            f"messageid: {msg.message_id}  发送时间：{msg.datetime} 发送用户与消息总结：{msg.display_name}: {text} "
        )
        lines.append(
            f"reply:{msg.reply}"
        )

        if msg.image_urls:
            lines.append("image_urls:")
            for url in msg.image_urls:
                lines.append(f"- {url}")

    return "\n".join(lines)
