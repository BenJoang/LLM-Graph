from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_INITIALIZED = False


def _is_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def setup_phoenix_tracing() -> bool:
    """Enable Phoenix tracing when explicitly requested in the environment."""
    global _INITIALIZED

    if _INITIALIZED:
        return True

    load_dotenv(PROJECT_ROOT / ".env")

    if not _is_enabled(os.getenv("PHOENIX_TRACING_ENABLED")):
        return False

    try:
        from phoenix.otel import register
    except ImportError as error:
        raise RuntimeError(
            "Phoenix tracing 已启用，但缺少依赖；请重新运行 setup.ps1 "
            "或安装 requirements-LLMv1.txt。"
        ) from error

    project_name = os.getenv("PHOENIX_PROJECT_NAME", "llm-graph").strip()
    register(
        project_name=project_name or "llm-graph",
        auto_instrument=True,
        batch=True,
        verbose=False,
    )

    _INITIALIZED = True
    logging.getLogger(__name__).info(
        "Phoenix tracing 已启用：project=%s endpoint=%s",
        project_name or "llm-graph",
        os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6006"),
    )
    return True
