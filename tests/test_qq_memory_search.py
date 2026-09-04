import json

import pytest

from src.tools.QqMemorySearch import tool


def write_group_records(
    root,
    group_id: str,
    records: list[dict | str],
    *,
    trailing_newline: bool = True,
):
    file_path = root / group_id / "dialog" / "messages.jsonl"
    file_path.parent.mkdir(parents=True)

    lines = [
        record
        if isinstance(record, str)
        else json.dumps(record, ensure_ascii=False)
        for record in records
    ]
    text = "\n".join(lines)
    if trailing_newline and lines:
        text += "\n"
    file_path.write_text(text, encoding="utf-8")
    return file_path


def make_record(index: int) -> dict:
    return {
        "datetime": f"2026-09-04 12:00:{index:02d}",
        "group_id": 123456,
        "group_name": "测试群",
        "message_id": index,
        "user_id": 10000 + index,
        "display_name": f"用户{index}",
        "summary": f"消息{index}",
        "segments": [],
        "reply": None,
    }


@pytest.mark.parametrize("trailing_newline", [True, False])
def test_reverse_pages_are_ordered_without_overlap(
    tmp_path,
    monkeypatch,
    trailing_newline,
) -> None:
    root = tmp_path / "groups"
    monkeypatch.setattr(tool, "QQ_MEMORY_DIR", root)
    write_group_records(
        root,
        "123456",
        [make_record(index) for index in range(1, 6)],
        trailing_newline=trailing_newline,
    )

    first = tool.call(group_index="123456", limit=2)
    assert first["ok"] is True
    assert [
        message["message_id"]
        for message in first["data"]["messages"]
    ] == [4, 5]
    assert first["data"]["scanned_count"] == 2
    assert isinstance(first["data"]["next_cursor"], str)

    second = tool.call(
        group_index="123456",
        limit=2,
        cursor=first["data"]["next_cursor"],
    )
    assert second["ok"] is True
    assert [
        message["message_id"]
        for message in second["data"]["messages"]
    ] == [2, 3]
    assert second["data"]["scanned_count"] == 4


def test_invalid_json_is_skipped_and_counted(tmp_path, monkeypatch) -> None:
    root = tmp_path / "groups"
    monkeypatch.setattr(tool, "QQ_MEMORY_DIR", root)
    write_group_records(
        root,
        "123456",
        [make_record(1), "not-json", make_record(2)],
    )

    result = tool.call(group_index="123456", limit=2)

    assert result["ok"] is True
    assert [
        message["message_id"]
        for message in result["data"]["messages"]
    ] == [1, 2]
    assert result["data"]["scanned_count"] == 3
    assert result["data"]["has_more"] is False


def test_cursor_is_bound_to_group(tmp_path, monkeypatch) -> None:
    root = tmp_path / "groups"
    monkeypatch.setattr(tool, "QQ_MEMORY_DIR", root)
    first_path = write_group_records(
        root,
        "123456",
        [make_record(1), make_record(2)],
    )
    write_group_records(
        root,
        "654321",
        [make_record(3), make_record(4)],
    )

    _, cursor, _, _, _ = tool.read_reverse_page(
        first_path,
        group_index="123456",
        cursor=None,
        limit=1,
    )

    result = tool.call(
        group_index="654321",
        limit=1,
        cursor=cursor,
    )
    assert result["ok"] is False
    assert "不属于当前 QQ 群" in result["error"]


def test_scan_stops_at_configured_limit(tmp_path, monkeypatch) -> None:
    root = tmp_path / "groups"
    monkeypatch.setattr(tool, "QQ_MEMORY_DIR", root)
    write_group_records(
        root,
        "123456",
        [make_record(index) for index in range(1005)],
    )

    cursor = None
    result = None
    for _ in range(10):
        result = tool.call(
            group_index="123456",
            limit=100,
            cursor=cursor,
        )
        assert result["ok"] is True
        cursor = result["data"]["next_cursor"]

    assert result is not None
    assert result["data"]["scanned_count"] == 1000
    assert result["data"]["has_more"] is False
    assert result["data"]["next_cursor"] is None


def test_render_result_uses_new_pagination_fields(
    tmp_path,
    monkeypatch,
) -> None:
    root = tmp_path / "groups"
    monkeypatch.setattr(tool, "QQ_MEMORY_DIR", root)
    write_group_records(root, "123456", [make_record(1)])

    result = tool.call(group_index="123456", limit=1)
    rendered = tool.render_result_for_llm(result)

    assert "累计向前扫描了 1 条记录" in rendered
    assert "messageid: 1" in rendered
