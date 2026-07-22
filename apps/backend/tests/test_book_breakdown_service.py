import pytest

from services.book_breakdown_service import MAX_BYTES, analyze_novel, decode_novel, reference_chapter_chunks
from services.breakdown_planning_agent_service import _NEW_BOOK_FIELDS, _normalize_plan
from api.routes_breakdown import _redact_reference_content, _reference_content_overlap


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


def test_reference_chunks_keep_all_text_from_a_long_chapter():
    text = "第一章 长章\n" + "甲" * 13000 + "\n第二章 短章\n" + "乙" * 100
    analysis = analyze_novel(text.encode(), "long.txt")
    chunks = reference_chapter_chunks(text.encode(), analysis)

    assert len(chunks) == 5
    assert [item["chapterIndex"] for item in chunks] == [1, 1, 1, 1, 2]
    assert sum(len(item["text"]) for item in chunks) >= 13100


def test_new_book_chapter_plan_requires_a_complete_dynamic_ten_chapter_plan():
    payload = {"chapters": [
        {
            "chapterIndex": index,
            "narrativeTask": f"原创主线推进 {index}",
            "conflictProgress": f"原创冲突升级 {index}",
            "informationProgress": f"原创信息揭示 {index}",
            "endQuestion": f"原创悬念 {index}",
        }
        for index in range(1, 11)
    ]}

    plan = _normalize_plan(payload, fields=_NEW_BOOK_FIELDS)

    assert [item["chapterIndex"] for item in plan] == list(range(1, 11))
    assert plan[0]["narrativeTask"] == "原创主线推进 1"


def test_reference_rhythm_rejects_source_book_content():
    rhythm = [{
        "narrativeMotion": "展示时间静止的限制",
        "tensionTransition": "压力上升",
        "informationRelease": "信息递进",
        "readerContract": "危机承诺",
        "hookShape": "未解问题",
    }]

    assert _reference_content_overlap(rhythm, "主角发现时间静止的限制条件。") == "时间静止"


def test_reference_rhythm_redaction_keeps_the_rhythm_axes():
    rhythm = [{
        "narrativeMotion": "展示时间静止的限制",
        "tensionTransition": "压力上升",
        "informationRelease": "信息递进",
        "readerContract": "危机承诺",
        "hookShape": "未解问题",
    }]

    assert _redact_reference_content(rhythm, "主角发现时间静止的限制条件。") == 1
    assert _reference_content_overlap(rhythm, "主角发现时间静止的限制条件。") == ""
    assert rhythm[0]["tensionTransition"] == "压力上升"
