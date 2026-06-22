from pathlib import Path

from pydantic import BaseModel, Field


TOOL_NAME = "get_file"
IS_READ_ONLY = True
TOOL_DIR = Path(__file__).resolve().parent


class InputSchema(BaseModel):
    path: str = Field(description="要查看的目录绝对路径")

class DirectoryItem(BaseModel):
    name: str = Field(description="文件夹名称")
    path: str = Field(description="文件夹绝对路径")


class FileItem(BaseModel):
    name: str = Field(description="文件名称")
    path: str = Field(description="文件绝对路径")
    size: int = Field(description="文件大小，单位 bytes")

class OutputSchema(BaseModel):
    ok: bool = Field(description="工具是否执行成功")
    error: str = Field(default="", description="错误信息，成功时为空字符串")
    path: str = Field(description="被查看的目录路径")
    directories: list[DirectoryItem] = Field(description="当前目录下的直接子文件夹")
    files: list[FileItem] = Field(description="当前目录下的直接文件")
    num_directories: int = Field(description="文件夹数量")
    num_files: int = Field(description="文件数量")


def get_input_schema() -> dict:
    return InputSchema.model_json_schema()

def get_output_schema() -> dict:
    return OutputSchema.model_json_schema()


def validate_input(**kwargs) -> tuple[bool, str]:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return False, str(e)

    path = Path(input_data.path)

    if not path.exists():
        return False, f"路径不存在: {input_data.path}"

    if not path.is_dir():
        return False, f"路径不是目录: {input_data.path}"
    return True, ""


def call(**kwargs) -> dict:
    try:
        input_data = InputSchema(**kwargs)
    except Exception as e:
        return OutputSchema(
            ok=False,
            error=str(e),
            path="",
            directories=[],
            files=[],
            num_directories=0,
            num_files=0,
        ).model_dump()

    target = Path(input_data.path)

    if not target.exists():
        return OutputSchema(
            ok=False,
            error=f"路径不存在: {input_data.path}",
            path=input_data.path,
            directories=[],
            files=[],
            num_directories=0,
            num_files=0,
        ).model_dump()

    if not target.is_dir():
        return OutputSchema(
            ok=False,
            error=f"路径不是目录: {input_data.path}",
            path=input_data.path,
            directories=[],
            files=[],
            num_directories=0,
            num_files=0,
        ).model_dump()

    directories = []
    files = []

    for child in target.iterdir():
        if child.name == "__pycache__":
            continue

        if child.is_dir():
            directories.append(
                DirectoryItem(
                    name=child.name,
                    path=str(child),
                )
            )

        elif child.is_file():
            files.append(
                FileItem(
                    name=child.name,
                    path=str(child),
                    size=child.stat().st_size,
                )
            )

    directories.sort(key=lambda item: item.name.lower())
    files.sort(key=lambda item: item.name.lower())

    return OutputSchema(
        ok=True,
        error="",
        path=str(target),
        directories=directories,
        files=files,
        num_directories=len(directories),
        num_files=len(files),
    ).model_dump()


def render_result_for_llm(result: dict) -> str:
    output = OutputSchema(**result)

    if not output.ok:
        return f"获取目录信息失败：{output.error}"

    lines = [
        f"目录：{output.path}",
        "",
        f"文件夹数量：{output.num_directories}",
    ]

    if output.directories:
        for item in output.directories:
            lines.append(f"- [dir] {item.name} | {item.path}")
    else:
        lines.append("- 无")

    lines.extend([
        "",
        f"文件数量：{output.num_files}",
    ])

    if output.files:
        for item in output.files:
            lines.append(f"- [file] {item.name} | {item.path} | {item.size} bytes")
    else:
        lines.append("- 无")

    return "\n".join(lines)
