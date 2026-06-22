from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1] / "skills"

def list_skills() -> list[str]:
    if not SKILL_DIR.exists():
        return []
    
    return [
        path.name
        for path in SKILL_DIR.iterdir()
        if path.is_dir() and (path / "skill.md").exists()
    ]

def read_skill(skill_name: str) -> str:
    skill_path = SKILL_DIR / skill_name / "skill.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"Skill '{skill_name}' not found.")
    
    return skill_path.read_text(encoding="utf-8")
