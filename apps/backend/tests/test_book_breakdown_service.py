import pytest

from services.book_breakdown_service import MAX_BYTES, analyze_novel, decode_novel, generate_idea_cards


def test_detects_chinese_chapters_and_evidence_lines():
    result = analyze_novel("第一章 开端\n甲出门。\n第二章 风雨\n乙回家。".encode(), "novel.txt")

    assert result["encoding"] == "UTF-8"
    assert result["chapterCount"] == 2
    assert [item["title"] for item in result["chapters"]] == ["第一章 开端", "第二章 风雨"]
    assert result["chapters"][0]["evidence"]["lineStart"] == 1


def test_detects_english_chapters():
    result = analyze_novel(b"Chapter 1: Arrival\nHello\nCHAPTER II - Storm\nWorld", "book.txt")

    assert result["chapterCount"] == 2
    assert result["chapters"][1]["title"] == "CHAPTER II - Storm"


def test_decodes_gb18030_with_warning():
    result = analyze_novel("序章\n开始\n尾声\n结束".encode("gb18030"), "legacy.txt")

    assert result["encoding"] == "GB18030"
    assert result["chapterCount"] == 2
    assert any("GB18030" in warning for warning in result["warnings"])


def test_falls_back_to_stable_character_windows_without_headings():
    result = analyze_novel(("正文" * 3500).encode(), "plain.txt")

    assert result["chapterCount"] == 2
    assert result["chapters"][0]["evidence"] == {"charStart": 0, "charEnd": 6000}
    assert any("未识别到章节标题" in warning for warning in result["warnings"])


def test_rejects_oversized_file():
    with pytest.raises(ValueError, match="20 MB"):
        decode_novel(b"x" * (MAX_BYTES + 1))


def test_reference_analysis_keeps_only_first_ten_chapters_and_builds_cards():
    text = "\n".join(f"第{i}章 测试\n正文{i}" for i in range(1, 13))
    result = analyze_novel(text.encode(), "hot-book.txt")

    assert result["chapterCount"] == 12
    assert len(result["selectedChapters"]) == 10
    assert len(result["studyCards"]) == 10
    assert len(result["motherCards"]) == 3
    assert "第10章" in result["selectedChapters"][-1]["title"]
    assert any("前 10 章" in warning for warning in result["warnings"])


def test_new_book_ideas_keep_source_chain_and_originality_constraints():
    cards = analyze_novel("第一章 开始\n正文".encode(), "hot-book.txt")["motherCards"]
    ideas = generate_idea_cards(cards[:2], project_name="新书", genre="都市悬疑", tone="紧张", target_audience="连载读者")

    assert len(ideas) == 6
    assert ideas[0]["derivedFrom"] == ["mother-opening-contract", "mother-escalation-engine"]
    assert "不得复用参考书人物、设定、事件链或表达" in ideas[0]["originalityConstraints"]
    assert ideas[0]["genre"] == "都市悬疑"
