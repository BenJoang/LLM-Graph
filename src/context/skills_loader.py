from dataclasses import dataclass
from pathlib import Path
import frontmatter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = PROJECT_ROOT / "skills"

@dataclass
class SkillInfo:
    name: str
    description: str
    path: Path
    content: str

def _find_skill_file(skill_dir: Path) -> Path | None:
    for filename in ("skill.md", "SKILL.md"):
        path = skill_dir / filename
        if path.exists() and path.is_file():
            return path
    return None

def list_skills(skills_dir: Path = SKILLS_DIR) -> list[SkillInfo]:
    if not skills_dir.exists():
        return []

    result: list[SkillInfo] = []

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue

        skill_file = _find_skill_file(skill_dir)
        if skill_file is None:
            continue

        post = frontmatter.load(skill_file)

        name = post.get("name") or skill_dir.name
        description = post.get("description") or _fallback_description(post.content)

        result.append(
            SkillInfo(
                name=name,
                description=description,
                path=skill_file,
                content=post.content.strip(),
            )
        )

    return result


def _fallback_description(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return line[:120]
    return "No description provided."


def read_skill(skill_name: str, skills_dir: Path = SKILLS_DIR) -> SkillInfo:
    for skill in list_skills(skills_dir):
        if skill.name == skill_name:
            return skill
    available = ", ".join(skill.name for skill in list_skills(skills_dir))
    raise FileNotFoundError(f"Skill '{skill_name}' not found. Available: {available}")


def format_available_skills(skills_dir: Path = SKILLS_DIR) -> str:
    skills = list_skills(skills_dir)

    if not skills:
        return "当前没有可用 skills。"

    lines = [
        "-- 当前可以使用的skills --",
    ]

    for skill in skills:
        lines.extend(
            [
                f"- {skill.name} :{skill.description}",
            ]
        )

    lines.append("-- skills end --")
    return "\n".join(lines)


def build_skill_system_message(skills_dir: Path = SKILLS_DIR) -> str:
    return f"""当任务匹配某个 Skill 描述时，先调用 skill 工具加载该 Skill 的完整说明。
不要猜测 Skill 内容。
{format_available_skills(skills_dir)}
"""