from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_working_dir(working_dir: str | Path | None = None) -> Path:
    if working_dir is None:
        return PROJECT_ROOT

    path = Path(working_dir).expanduser().resolve()

    if path.is_file():
        return path.parent

    return path


def build_working_dir_system_message(
    working_dir: str | Path | None = None,
    working_dir_need_switch: bool = False,
) -> str:
    if working_dir_need_switch:
        resolved_working_dir = resolve_working_dir(working_dir)

        return (
            "本次运行的工作目录如下。涉及相对路径、项目文件读取、项目规则判断时，"
            "优先以该目录作为当前项目目录。\n"
            f'<working-directory>'
            f'{resolved_working_dir}'
            f'</working-directory>'
        )
    else:
        return 