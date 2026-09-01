#!/usr/bin/env python3
"""Small deterministic helper used by the skill starter example."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize a JSON item list.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--group-by")
    return parser.parse_args()


def load_items(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read input file: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc

    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("input must be a JSON array of objects or an object with an items array")
    return items


def summarize(items: list[dict[str, Any]], group_by: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": len(items),
        "fields": sorted({key for item in items for key in item}),
    }
    if group_by:
        missing = sum(group_by not in item for item in items)
        groups = Counter(str(item[group_by]) for item in items if group_by in item)
        result["group_by"] = group_by
        result["groups"] = dict(sorted(groups.items()))
        result["missing_group_field"] = missing
    return result


def main() -> int:
    args = parse_args()
    try:
        items = load_items(args.input)
        result = summarize(items, args.group_by)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
