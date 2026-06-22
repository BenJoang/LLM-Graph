from pathlib import Path

def read_markdown_section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    target = f"## {heading}"
    collecting = False
    result = []

    for line in lines:
        if line.strip() == target:
            collecting = True
            continue
        
        if collecting:
            if line.startswith("## "):
                break
            result.append(line)

    return "\n".join(result).strip()

def read_tool_description(tool_dir: Path) -> str:
    return read_markdown_section(tool_dir / "prompt.md", "DESCRIPTION")

def read_tool_prompt(tool_dir: Path) -> str:
    return read_markdown_section(tool_dir / "prompt.md", "PROMPT")