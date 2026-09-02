import json
from dataclasses import dataclass

import pytest

from src.rag.evaluate_cli import hit_at_k, load_cases, reciprocal_rank


@dataclass(frozen=True)
class FakeResult:
    document_id: str


def test_retrieval_metrics() -> None:
    results = [
        FakeResult("wrong"),
        FakeResult("expected"),
        FakeResult("other"),
    ]
    expected = {"expected"}

    assert hit_at_k(results, expected, 1) is False
    assert hit_at_k(results, expected, 2) is True
    assert reciprocal_rank(results, expected) == 0.5


def test_metrics_return_miss_for_absent_document() -> None:
    results = [FakeResult("wrong")]
    expected = {"expected"}

    assert hit_at_k(results, expected, 3) is False
    assert reciprocal_rank(results, expected) == 0.0


def test_hit_at_k_rejects_non_positive_k() -> None:
    with pytest.raises(ValueError, match="k 必须大于 0"):
        hit_at_k([], {"expected"}, 0)


def test_load_cases(tmp_path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "tenant_id": "study",
                "cases": [
                    {
                        "question": "测试问题",
                        "expected_document_ids": ["document-1"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    tenant_id, cases = load_cases(cases_path)

    assert tenant_id == "study"
    assert cases[0]["question"] == "测试问题"


def test_load_cases_rejects_empty_cases(tmp_path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"tenant_id": "study", "cases": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="非空 cases"):
        load_cases(cases_path)
