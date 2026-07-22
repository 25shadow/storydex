"""Deterministic first pass for TXT novel deconstruction.

The parser deliberately returns evidence (line and character spans) so later
LLM/NLP passes can make claims against source text instead of inventing facts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


MAX_BYTES = 20 * 1024 * 1024
REFERENCE_CHAPTER_LIMIT = 10


@dataclass(frozen=True)
class DecodedNovel:
    text: str
    encoding: str
    warnings: list[str]


CHAPTER_PATTERNS = (
    ("chinese", re.compile(r"^\s*(?P<title>(?:第\s*[0-9零一二三四五六七八九十百千万两]+\s*[章节回卷部篇集]|序章|楔子|尾声|后记|番外(?:篇)?|引子).{0,80})\s*$")),
    ("english", re.compile(r"^\s*(?P<title>(?:chapter|part|book)\s+[0-9ivxlcdm]+(?:\s*[-:：.]\s*.*)?)\s*$", re.I)),
)


def decode_novel(raw: bytes) -> DecodedNovel:
    if len(raw) > MAX_BYTES:
        raise ValueError(f"文件超过 {MAX_BYTES // (1024 * 1024)} MB 限制")
    warnings: list[str] = []
    candidates = (("utf-8-sig", "UTF-8 BOM"),) if raw.startswith(b"\xef\xbb\xbf") else ()
    candidates += (("utf-8", "UTF-8"), ("gb18030", "GB18030"))
    for codec, label in candidates:
        try:
            text = raw.decode(codec)
            if codec == "gb18030":
                warnings.append("文件未使用 UTF-8，已按 GB18030 解码；建议转为 UTF-8 保存。")
            return DecodedNovel(_normalize_text(text), label, warnings)
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文本编码，请将 TXT 另存为 UTF-8 或 GB18030。")


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def analyze_novel(raw: bytes, file_name: str, chapter_pattern: str = "auto") -> dict[str, Any]:
    decoded = decode_novel(raw)
    lines = decoded.text.splitlines()
    headings: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        for pattern_name, pattern in CHAPTER_PATTERNS:
            if chapter_pattern not in ("auto", pattern_name):
                continue
            match = pattern.match(line)
            if match:
                title = match.group("title").strip()
                if headings and headings[-1]["title"] == title:
                    decoded.warnings.append(f"检测到重复章节标题“{title}”，已合并重复标题。")
                    continue
                headings.append({"title": title, "startLine": line_index, "pattern": pattern_name})
                break

    if headings:
        chapters = []
        for index, heading in enumerate(headings):
            start_line = heading["startLine"]
            end_line = headings[index + 1]["startLine"] if index + 1 < len(headings) else len(lines)
            body = "\n".join(lines[start_line + 1:end_line]).strip()
            chapters.append({
                "index": index + 1,
                "title": heading["title"],
                "startLine": start_line + 1,
                "endLine": end_line,
                "characterCount": len(body),
                "evidence": {"lineStart": start_line + 1, "lineEnd": min(start_line + 3, end_line)},
            })
    else:
        window = 6000
        text = decoded.text
        chapters = [
            {"index": index + 1, "title": f"片段 {index + 1}", "startLine": None, "endLine": None,
             "characterCount": len(text[start:end]), "evidence": {"charStart": start, "charEnd": end}}
            for index, start in enumerate(range(0, len(text), window))
            for end in [min(start + window, len(text))]
        ]
        decoded.warnings.append("未识别到章节标题，已按约 6000 字生成分析片段；请在章节视图中复核边界。")

    selected_chapters = chapters[:REFERENCE_CHAPTER_LIMIT]
    if len(chapters) > REFERENCE_CHAPTER_LIMIT:
        decoded.warnings.append("本次热榜书研究仅分析前 10 章；其余章节不会进入创作参考上下文。")
    return {
        "analysisId": None,
        "fileName": file_name,
        "encoding": decoded.encoding,
        "characterCount": len(decoded.text),
        "lineCount": len(lines),
        "chapterCount": len(chapters),
        "chapters": chapters,
        "referenceChapterLimit": REFERENCE_CHAPTER_LIMIT,
        "selectedChapters": selected_chapters,
        "studyCards": _build_study_cards(selected_chapters),
        "motherCards": _build_mother_cards(selected_chapters),
        "warnings": decoded.warnings,
        "status": "parsed",
        "nextStages": ["study_cards", "mother_cards", "new_book_ideas"],
    }


def reference_chapter_chunks(raw: bytes, analysis: dict[str, Any], max_chars_per_chunk: int = 6000) -> list[dict[str, Any]]:
    """Split the complete first-ten chapters at paragraph boundaries for map-reduce analysis."""
    decoded = decode_novel(raw)
    lines = decoded.text.splitlines()
    chunks: list[dict[str, Any]] = []
    for chapter in analysis.get("selectedChapters", []):
        if not isinstance(chapter, dict):
            continue
        start_line = chapter.get("startLine")
        end_line = chapter.get("endLine")
        if isinstance(start_line, int) and isinstance(end_line, int):
            body = "\n".join(lines[start_line:end_line]).strip()
        else:
            evidence = chapter.get("evidence") if isinstance(chapter.get("evidence"), dict) else {}
            start = int(evidence.get("charStart") or 0)
            end = int(evidence.get("charEnd") or start + max_chars_per_chapter)
            body = decoded.text[start:end]
        paragraphs = [item.strip() for item in body.split("\n") if item.strip()]
        current: list[str] = []
        current_size = 0
        chunk_index = 1
        for paragraph in paragraphs or [body]:
            parts = [paragraph[offset:offset + max_chars_per_chunk] for offset in range(0, len(paragraph), max_chars_per_chunk)] or [""]
            for part in parts:
                if current and current_size + len(part) + 1 > max_chars_per_chunk:
                    chunks.append(_chapter_chunk(chapter, chunk_index, "\n".join(current)))
                    chunk_index += 1
                    current = []
                    current_size = 0
                current.append(part)
                current_size += len(part) + 1
        if current:
            chunks.append(_chapter_chunk(chapter, chunk_index, "\n".join(current)))
    return chunks


def _chapter_chunk(chapter: dict[str, Any], chunk_index: int, text: str) -> dict[str, Any]:
    return {
        "chapterIndex": chapter.get("index"),
        "chapterTitle": chapter.get("title"),
        "chunkIndex": chunk_index,
        "text": text,
    }


def _build_study_cards(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    functions = (
        "开篇承诺：建立主角处境、异常或读者问题。",
        "世界与人物：补足主角欲望、关系张力或核心规则。",
        "首次升级：让阻力或代价变得具体。",
        "不可逆选择：推动主角离开原有状态。",
        "主线锁定：明确短期目标与失败代价。",
        "兑现承诺：用行动、关系或信息推进读者期待。",
        "扩大压力：使冲突从局部走向更高层级。",
        "信息重估：给出能改变读者理解的新线索。",
        "连续推进：兑现一个小目标，同时制造新缺口。",
        "十章转折：用新危机、真相或目标重设下一阶段。",
    )
    return [
        {
            "id": f"study-chapter-{chapter['index']}",
            "chapterIndex": chapter["index"],
            "chapterTitle": chapter["title"],
            "function": functions[position],
            "evidence": chapter["evidence"],
            "status": "待 AI 深度复核",
        }
        for position, chapter in enumerate(chapters[:REFERENCE_CHAPTER_LIMIT])
    ]
def _build_mother_cards(chapters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chapter_range = f"前 {len(chapters)} 章" if chapters else "文本开篇"
    return [
        {
            "id": "mother-opening-contract",
            "title": "开篇承诺与读者问题",
            "type": "opening_contract",
            "mechanism": f"研究 {chapter_range} 如何在早期建立异常、目标或未解问题，并在章节末留下继续阅读的理由。",
            "useFor": ["首章钩子", "前三章信息投放", "章节悬念"],
            "doNotReuse": ["原书人物", "原书事件链", "原书表述"],
        },
        {
            "id": "mother-escalation-engine",
            "title": "不可逆选择与升级机制",
            "type": "escalation_engine",
            "mechanism": "研究主角如何从初始困境进入不可逆任务，并持续提高风险、代价或目标。",
            "useFor": ["主线推进", "失败代价", "十章转折"],
            "doNotReuse": ["原书设定规则", "原书谜底", "原书反转答案"],
        },
        {
            "id": "mother-relationship-engine",
            "title": "关系张力与信息差",
            "type": "relationship_engine",
            "mechanism": "研究角色间因目标冲突、秘密、利益绑定或身份错位产生的持续戏剧张力。",
            "useFor": ["双主角", "对手关系", "秘密与误解"],
            "doNotReuse": ["原书角色关系", "原书角色名称", "原书关键场面"],
        },
    ]
