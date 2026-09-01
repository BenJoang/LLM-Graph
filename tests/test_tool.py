from src.tools.ReadFile import tool as read_file



def test_read_text_file(tmp_path):
    target = tmp_path / "example.txt"
    target.write_text("第一行\n第二行\n第三行", encoding="utf-8")

    result = read_file.call(
        file_path=str(target),
        offset=1,
        limit=2,
    )

    assert result["ok"] is True
    assert result["count"] == 2
    assert "1: 第一行" in result["content"]
    assert "2: 第二行" in result["content"]
    assert result["next_offset"] == 3
    assert result["truncated_by_lines"] is True


def test_read_missing_file(tmp_path):
    result = read_file.call(
        file_path=str(tmp_path / "missing.txt"),
    )

    assert result["ok"] is False
    assert "不存在" in result["error"]