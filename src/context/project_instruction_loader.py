from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INSTRUCTION_NAMES = (
    "AGENTS.md",
    "CLAUDE.md",
    "PROJECT.md",
)

MAX_INSTRUCTION_CHARS = 40000

LoadMode = Literal["nearest", "stack"]

@dataclass(frozen=True)
class ProjectInstruction:
    path: Path
    content: str

def resolve_working_dir(working_dir: str | Path | None = None) -> Path:
    if working_dir is None:
        return PROJECT_ROOT

    path = Path(working_dir).expanduser().resolve()

    if path.is_file():
        return path.parent

    return path

def iter_dirs_root_to_workdir(working_dir: Path, stop_dir: Path | None = None) -> list[Path]:
    working_dir = working_dir.resolve()
    stop_dir = (stop_dir or PROJECT_ROOT).resolve()

    dirs: list[Path] = []
    current = working_dir

    while True:
        dirs.append(current)

        if current == stop_dir or current.parent == current:
            break

        current = current.parent

    return list(reversed(dirs))

def discover_instruction_files(
    working_dir: str | Path | None = None,
    names: Sequence[str] = DEFAULT_INSTRUCTION_NAMES,
    mode: LoadMode = "nearest",
    stop_dir: str | Path | None = None,
) -> list[Path]:
    resolved_working_dir = resolve_working_dir(working_dir)
    resolved_stop_dir = Path(stop_dir).resolve() if stop_dir else PROJECT_ROOT

    dirs = iter_dirs_root_to_workdir(
        working_dir=resolved_working_dir,
        stop_dir=resolved_stop_dir,
    )

    if mode == "nearest":
        for directory in reversed(dirs):
            for name in names:
                path = directory / name
                if path.exists() and path.is_file():
                    return [path]
        return []

    if mode == "stack":
        result: list[Path] = []

        for directory in dirs:
            for name in names:
                path = directory / name
                if path.exists() and path.is_file():
                    result.append(path)

        return result

    raise ValueError(f"Unsupported instruction load mode: {mode}")

def read_instruction_file(path: Path) -> ProjectInstruction | None:
    try:
        content = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError:
        content = path.read_text(encoding="utf-8-sig").strip()
    except FileNotFoundError:
        return None

    if not content:
        return None

    if len(content) > MAX_INSTRUCTION_CHARS:
        content = content[:MAX_INSTRUCTION_CHARS] + "\n\n[truncated]"

    return ProjectInstruction(
        path=path,
        content=content,
    )

def load_project_instructions(
    working_dir: str | Path | None = None,
    names: Sequence[str] = DEFAULT_INSTRUCTION_NAMES,
    mode: LoadMode = "nearest",
    stop_dir: str | Path | None = None,
) -> list[ProjectInstruction]:
    files = discover_instruction_files(
        working_dir=working_dir,
        names=names,
        mode=mode,
        stop_dir=stop_dir,
    )

    instructions: list[ProjectInstruction] = []

    for path in files:
        instruction = read_instruction_file(path)
        if instruction is not None:
            instructions.append(instruction)

    return instructions


def format_project_instructions(
    instructions: list[ProjectInstruction],
) -> str:
    if not instructions:
        return ""

    parts = [
        "项目内存在的说明性md文件，请根据内容调整行为。"
    ]

    for item in instructions:
        parts.append(
            f'<project-instruction source为"{item.path}">\n'
            f'{item.content}\n'
            f'</project-instruction>'
        )

    return "\n".join(parts)


def build_project_instruction_system_message(
    working_dir: str | Path | None = None,
    names: Sequence[str] = DEFAULT_INSTRUCTION_NAMES,
    mode: LoadMode = "nearest",
    stop_dir: str | Path | None = None,
) -> str:
    instructions = load_project_instructions(
        working_dir=working_dir,
        names=names,
        mode=mode,
        stop_dir=stop_dir,
    )

    return format_project_instructions(instructions)