"""章节落盘规范化的回归测试。

背景：旧版规范名用中文数字（第一章），默认模板与 LLM 落盘路径用
阿拉伯数字（第1章），`_normalize_chapter_directories` 的重命名与
增量写入互相打架，每轮生成都会留下一个同章号的空目录。

覆盖：
1. 规范名与新章命名统一为阿拉伯数字。
2. 同章号异体命名的落盘路径归一到现有目录，不再分裂。
3. 与非空章节同章号的空目录被自动清理；章号唯一的空目录保留。
"""
from __future__ import annotations

from services.story_project_service import get_story_project_service


def _write_segment(root, chapter: str, name: str = "001.md", text: str = "正文内容。") -> None:
    chapter_dir = root / "chapters" / chapter
    chapter_dir.mkdir(parents=True, exist_ok=True)
    (chapter_dir / name).write_text(text, encoding="utf-8")


def test_display_name_uses_arabic_numerals(tmp_path):
    service = get_story_project_service()
    assert service._build_chapter_display_name("第一章 苏家少年") == "第1章 苏家少年"
    assert service._build_chapter_display_name("第十二章 决战") == "第12章 决战"
    assert service._build_new_chapter_name(3, title="风起") == "第3章 风起"


def test_new_chapter_path_matches_template_style(tmp_path):
    service = get_story_project_service()
    _write_segment(tmp_path, "第1章 苏家少年")
    _write_segment(tmp_path, "第1章 苏家少年", name="002.md")
    _write_segment(tmp_path, "第1章 苏家少年", name="003.md")
    # 第1章已写满（默认每章 3 段），新章应延续阿拉伯数字风格
    next_path = service.compute_next_segment_path(tmp_path)
    assert next_path.startswith("chapters/第2章 ")


def test_variant_chapter_name_redirects_to_existing_dir(tmp_path):
    service = get_story_project_service()
    _write_segment(tmp_path, "第1章 苏家少年")
    settings = service.read_project_settings(tmp_path)
    resolved = service._resolve_story_increment_segment_path(
        tmp_path,
        {"path": "chapters/第一章 苏家少年/002.md"},
        active_file="",
        prompt="",
        settings=settings,
    )
    assert resolved == "chapters/第1章 苏家少年/002.md"
    # 磁盘上不应出现第二个章节目录
    assert not (tmp_path / "chapters" / "第一章 苏家少年").exists()


def test_apply_increment_does_not_leave_empty_duplicate_dirs(tmp_path):
    service = get_story_project_service()
    service.ensure_project_structure(tmp_path)
    result = service.apply_story_generation_increment(
        tmp_path,
        {
            "fragments": [
                {"path": "chapters/第1章 苏家少年/001.md", "text": "少年立于山巅。"}
            ]
        },
    )
    assert result["ok"] is True
    # 模拟旧 bug / LLM mkdir 留下的同章号空目录
    (tmp_path / "chapters" / "第一章 苏家少年").mkdir()
    result = service.apply_story_generation_increment(
        tmp_path,
        {
            "fragments": [
                {"path": "chapters/第一章 苏家少年/002.md", "text": "他转身下山。"}
            ]
        },
    )
    assert result["ok"] is True
    chapters = sorted(p.name for p in (tmp_path / "chapters").iterdir() if p.is_dir())
    assert chapters == ["第1章 苏家少年"]
    assert (tmp_path / "chapters" / "第1章 苏家少年" / "002.md").exists()


def test_prune_keeps_unique_number_empty_chapter(tmp_path):
    service = get_story_project_service()
    _write_segment(tmp_path, "第1章 苏家少年")
    # 用户手动新建的待写作章节（章号唯一）不能被清理
    (tmp_path / "chapters" / "第2章 青州城").mkdir()
    # 与第1章同号的空目录应被清理
    (tmp_path / "chapters" / "第一章 苏家少年").mkdir()
    removed = service._prune_duplicate_empty_chapter_dirs(tmp_path)
    assert removed == ["chapters/第一章 苏家少年"]
    assert (tmp_path / "chapters" / "第2章 青州城").exists()
