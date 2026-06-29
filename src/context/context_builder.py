from __future__ import annotations

from pathlib import Path

from src.context.project_instruction_loader import build_project_instruction_system_message
from src.context.skills_loader import build_skill_system_message
from src.context.working_dir_loader import build_working_dir_system_message


def build_system_context(
    working_dir: str | Path | None = None,
) -> str:
    parts = [
        build_working_dir_system_message(working_dir),
        build_project_instruction_system_message(
            working_dir=working_dir,
            mode="nearest",
        ),
        build_skill_system_message(),
    ]

    return "\n\n".join(part for part in parts if part)