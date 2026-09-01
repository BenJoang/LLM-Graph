#!/usr/bin/env python3
"""Query Degoog and emit compact, LLM-friendly search results."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "http://127.0.0.1:4444"
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
        description="Search a Degoog instance and print compact results."
    )
    parser.add_argument("query", help="Search query")
    parser.add_argument("--url", help="Degoog base URL")
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 51))
    parser.add_argument("--type", default="web", dest="search_type")
    parser.add_argument("--language")
    parser.add_argument("--engines", help="Comma-separated Degoog engine IDs")
    parser.add_argument(
        "--time-range",
        choices=("any", "hour", "day", "week", "month", "year"),
    )
    parser.add_argument("--page", type=int, default=1, choices=range(1, 11))
    parser.add_argument("--safe-mode")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--output", choices=("json", "text"), default="json")
    args = parser.parse_args()
    if not 0 < args.timeout <= 60:
        parser.error("--timeout must be greater than 0 and at most 60")
    return args


def instance_url(args: argparse.Namespace) -> str:
    return (args.url or os.environ.get("DEGOOG_URL") or DEFAULT_URL).rstrip("/")


def search_endpoint(base_url: str) -> str:
    if base_url.endswith("/api/search"):
        return base_url
    if base_url.endswith("/api"):
        return f"{base_url}/search"
    return f"{base_url}/api/search"


def build_body(args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "query": args.query,
        "type": args.search_type,
        "page": args.page,
    }
    if args.language:
        body["lang"] = args.language
    if args.time_range:
        body["time"] = args.time_range
    if args.safe_mode:
        body["safeMode"] = args.safe_mode
    if args.engines:
        body["engines"] = [
            value.strip() for value in args.engines.split(",") if value.strip()
        ]
    return body


def request_json(
    url: str,
    *,
    timeout: float,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "LLM-Graph-Degoog-Skill/1.0",
    }
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    api_key = os.environ.get("DEGOOG_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError(
                "Degoog returned HTTP 401. Set DEGOOG_API_KEY when search API protection is enabled."
            ) from exc
        raise RuntimeError(f"Degoog returned HTTP {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect to Degoog: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(
            f"Degoog request timed out after {timeout:g} seconds."
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Degoog did not return valid JSON.") from exc


def compact_result(item: dict[str, Any]) -> dict[str, Any]:
    sources = item.get("sources") or ([item["source"]] if item.get("source") else [])
    result = {
        "title": clean_text(item.get("title")),
        "url": item.get("url", ""),
        "content": clean_text(item.get("content") or item.get("snippet")),
        "sources": sources,
        "score": item.get("score"),
        "published_date": item.get("publishedDate") or item.get("published_date"),
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def installed_engine_count(base_url: str, timeout: float) -> int | None:
    try:
        payload = request_json(
            f"{base_url}/api/extensions?type=engine",
            timeout=timeout,
        )
    except RuntimeError:
        return None
    engines = payload.get("engines")
    return len(engines) if isinstance(engines, list) else None


def compact_payload(
    args: argparse.Namespace,
    base_url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    raw_results = payload.get("results") or []
    engine_timings = payload.get("engineTimings") or payload.get("engine_timings") or []
    engine_count = None
    if not raw_results and not engine_timings:
        engine_count = installed_engine_count(base_url, args.timeout)

    return {
        "query": payload.get("query") or args.query,
        "instance": base_url,
        "type": payload.get("type") or args.search_type,
        "result_count": min(len(raw_results), args.limit),
        "total_time_ms": payload.get("totalTime"),
        "engine_timings": engine_timings,
        "related_searches": payload.get("relatedSearches", [])[:10],
        "installed_engine_count": engine_count,
        "no_engines_installed": engine_count == 0,
        "results": [compact_result(item) for item in raw_results[: args.limit]],
    }


def print_text(data: dict[str, Any]) -> None:
    print(f"Query: {data['query']}")
    print(f"Results: {data['result_count']}")
    if data.get("no_engines_installed"):
        print("No Degoog search engines are installed or enabled.")
    for timing in data.get("engine_timings", []):
        print(f"Engine: {json.dumps(timing, ensure_ascii=False)}")
    for index, item in enumerate(data["results"], start=1):
        print(f"\n{index}. {item.get('title', '(untitled)')}")
        print(item.get("url", ""))
        if item.get("content"):
            print(item["content"])
        if item.get("sources"):
            print(f"Sources: {', '.join(item['sources'])}")


def main() -> int:
    args = parse_args()
    base_url = instance_url(args)
    try:
        payload = request_json(
            search_endpoint(base_url),
            timeout=args.timeout,
            body=build_body(args),
        )
        data = compact_payload(args, base_url, payload)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.output == "text":
        print_text(data)
    else:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
