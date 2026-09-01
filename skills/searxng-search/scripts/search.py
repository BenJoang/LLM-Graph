#!/usr/bin/env python3
"""Query a SearXNG instance and emit compact, LLM-friendly results."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:8080"
TAG_RE = re.compile(r"<[^>]+>")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="backslashreplace")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = TAG_RE.sub("", str(value))
    return " ".join(html.unescape(text).split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search a SearXNG JSON API and print compact results."
    )
    parser.add_argument("query", help="Search query")
    parser.add_argument("--url", help="SearXNG base URL")
    parser.add_argument("--limit", type=int, default=5, choices=range(1, 21))
    parser.add_argument("--language")
    parser.add_argument("--categories")
    parser.add_argument("--engines")
    parser.add_argument("--time-range", choices=("day", "month", "year"))
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--safesearch", type=int, choices=(0, 1, 2))
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--output", choices=("json", "text"), default="json")
    args = parser.parse_args()
    if args.page < 1:
        parser.error("--page must be at least 1")
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be greater than 0 and at most 60")
    return args


def instance_url(args: argparse.Namespace) -> str:
    value = (
        args.url
        or os.environ.get("SEARXNG_URL")
        or os.environ.get("SEARXNG_BASE_URL")
        or DEFAULT_URL
    )
    return value.rstrip("/")


def build_params(args: argparse.Namespace) -> dict[str, str | int]:
    params: dict[str, str | int] = {
        "q": args.query,
        "format": "json",
        "pageno": args.page,
    }
    optional = {
        "language": args.language,
        "categories": args.categories,
        "engines": args.engines,
        "time_range": args.time_range,
        "safesearch": args.safesearch,
    }
    params.update({key: value for key, value in optional.items() if value is not None})
    return params


def request_search(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    base_url = instance_url(args)
    endpoint = f"{base_url}/search?{urlencode(build_params(args))}"
    request = Request(
        endpoint,
        headers={
            "Accept": "application/json",
            "User-Agent": "LLM-Graph-SearXNG-Skill/1.0",
        },
    )
    try:
        with urlopen(request, timeout=args.timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(response.read().decode(charset))
    except HTTPError as exc:
        if exc.code == 403:
            raise RuntimeError(
                "SearXNG returned HTTP 403. Enable 'json' in search.formats."
            ) from exc
        raise RuntimeError(f"SearXNG returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect to SearXNG at {base_url}: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("SearXNG did not return valid JSON.") from exc
    return base_url, payload


def compact_result(item: dict[str, Any]) -> dict[str, Any]:
    engines = item.get("engines") or ([item["engine"]] if item.get("engine") else [])
    result = {
        "title": clean_text(item.get("title")),
        "url": item.get("url", ""),
        "content": clean_text(item.get("content")),
        "engines": engines,
        "category": item.get("category", ""),
        "score": item.get("score"),
        "published_date": item.get("publishedDate") or item.get("published_date"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def compact_payload(
    query: str, base_url: str, payload: dict[str, Any], limit: int
) -> dict[str, Any]:
    raw_results = payload.get("results") or []
    results = [compact_result(item) for item in raw_results[:limit]]
    return {
        "query": query,
        "instance": base_url,
        "result_count": len(results),
        "answers": [clean_text(value) for value in payload.get("answers", [])],
        "corrections": payload.get("corrections", []),
        "suggestions": payload.get("suggestions", [])[:10],
        "unresponsive_engines": payload.get("unresponsive_engines", []),
        "results": results,
    }


def print_text(data: dict[str, Any]) -> None:
    print(f"Query: {data['query']}")
    print(f"Results: {data['result_count']}")
    for index, item in enumerate(data["results"], start=1):
        print(f"\n{index}. {item.get('title', '(untitled)')}")
        print(item.get("url", ""))
        if item.get("content"):
            print(item["content"])
        if item.get("engines"):
            print(f"Engines: {', '.join(item['engines'])}")


def main() -> int:
    args = parse_args()
    try:
        base_url, payload = request_search(args)
        data = compact_payload(args.query, base_url, payload, args.limit)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.output == "text":
        print_text(data)
    else:
        # ASCII-only JSON avoids Windows console/code-page failures. JSON
        # consumers, including the LLM tool wrapper, decode \u escapes normally.
        print(json.dumps(data, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
