import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Protocol

from src.rag.hybrid_retriever import hybrid_retrieve
from src.rag.service import retrieve_chunks


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = PROJECT_ROOT / "evals" / "rag_cases.json"


class RetrievalResult(Protocol):
    document_id: str


def reciprocal_rank(
    results: list[RetrievalResult],
    expected_document_ids: set[str],
) -> float:
    for rank, result in enumerate(results, start=1):
        if result.document_id in expected_document_ids:
            return 1.0 / rank

    return 0.0


def hit_at_k(
    results: list[RetrievalResult],
    expected_document_ids: set[str],
    k: int,
) -> bool:
    if k <= 0:
        raise ValueError("k 必须大于 0")

    return any(
        result.document_id in expected_document_ids
        for result in results[:k]
    )


def load_cases(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tenant_id = str(data.get("tenant_id", "")).strip()
    cases = data.get("cases")

    if not tenant_id:
        raise ValueError("评估文件缺少 tenant_id")

    if not isinstance(cases, list) or not cases:
        raise ValueError("评估文件必须包含非空 cases 列表")

    for index, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"第 {index} 个评估用例不是对象")

        question = str(case.get("question", "")).strip()
        expected = case.get("expected_document_ids")

        if not question:
            raise ValueError(f"第 {index} 个评估用例缺少 question")

        if not isinstance(expected, list) or not expected:
            raise ValueError(
                f"第 {index} 个评估用例缺少 expected_document_ids"
            )

    return tenant_id, cases


async def evaluate(
    cases_path: Path,
    *,
    top_k: int,
) -> None:
    if not 1 <= top_k <= 100:
        raise ValueError("top_k 必须在 1 到 100 之间")

    tenant_id, cases = load_cases(cases_path)
    scores = {
        "vector": {"hit1": 0, "hitk": 0, "rr": 0.0},
        "hybrid": {"hit1": 0, "hitk": 0, "rr": 0.0},
    }

    for index, case in enumerate(cases, start=1):
        question = str(case["question"])
        expected = {
            str(document_id)
            for document_id in case["expected_document_ids"]
        }

        # 顺序执行，避免一次评估同时向本地 embedding 服务发送两份请求。
        vector_results = await retrieve_chunks(
            question,
            tenant_id=tenant_id,
            top_k=top_k,
        )
        hybrid_results = await hybrid_retrieve(
            question,
            tenant_id=tenant_id,
            top_k=top_k,
        )

        print()
        print(f"[{index}] {question}")
        print(f"预期文档：{sorted(expected)}")

        for mode, results in (
            ("vector", vector_results),
            ("hybrid", hybrid_results),
        ):
            document_ids = [result.document_id for result in results]
            hit1 = hit_at_k(results, expected, 1)
            hitk = hit_at_k(results, expected, top_k)
            rr = reciprocal_rank(results, expected)

            scores[mode]["hit1"] += int(hit1)
            scores[mode]["hitk"] += int(hitk)
            scores[mode]["rr"] += rr

            print(
                f"{mode:>6}: "
                f"结果={document_ids}, "
                f"Hit@1={hit1}, "
                f"Hit@{top_k}={hitk}, "
                f"RR={rr:.3f}"
            )

    total = len(cases)

    print()
    print("=" * 60)
    print(f"问题总数：{total}")

    for mode in ("vector", "hybrid"):
        hit1 = scores[mode]["hit1"] / total
        hitk = scores[mode]["hitk"] / total
        mrr = scores[mode]["rr"] / total

        print()
        print(mode)
        print(f"Hit@1：{hit1:.2%}")
        print(f"Hit@{top_k}：{hitk:.2%}")
        print(f"MRR：{mrr:.4f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="比较向量检索和 BM25 + RRF 混合检索",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
    )
    parser.add_argument("--top-k", type=int, default=3)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    await evaluate(args.cases, top_k=args.top_k)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(
            main(),
            loop_factory=asyncio.SelectorEventLoop,
        )
    else:
        asyncio.run(main())
