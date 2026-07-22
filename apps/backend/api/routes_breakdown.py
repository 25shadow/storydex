from __future__ import annotations

import base64
import binascii
import asyncio
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.response import ApiTrace, success_response
from core.config import get_settings
from services.book_breakdown_service import analyze_novel, reference_chapter_chunks
from services.project_service import get_project_service

router = APIRouter(tags=["breakdown"])


class BreakdownOptions(BaseModel):
    chapter_pattern: str = Field(default="auto", alias="chapterPattern")
    model_config = ConfigDict(populate_by_name=True)


class BreakdownRequest(BaseModel):
    file_name: str = Field(alias="fileName")
    content_base64: str = Field(alias="contentBase64")
    options: BreakdownOptions = Field(default_factory=BreakdownOptions)
    model_config = ConfigDict(populate_by_name=True)


class IdeaGenerationRequest(BaseModel):
    analysis_id: str = Field(alias="analysisId")
    mother_card_ids: list[str] = Field(alias="motherCardIds", min_length=1)
    project_name: str = Field(default="", alias="projectName")
    genre: str = ""
    tone: str = ""
    target_audience: str = Field(default="", alias="targetAudience")
    model_config = ConfigDict(populate_by_name=True)


@router.get("/breakdown/history")
def breakdown_history(request: Request) -> dict[str, Any]:
    root = get_settings().global_root / "breakdowns"
    items: list[dict[str, Any]] = []
    if root.exists():
        for directory in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
            analysis_path = directory / "analysis.json"
            if not directory.is_dir() or not analysis_path.exists():
                continue
            try:
                analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append({
                "analysisId": str(analysis.get("analysisId") or directory.name),
                "fileName": str(analysis.get("fileName") or "未命名参考书"),
                "chapterCount": int(analysis.get("chapterCount") or 0),
                "selectedChapterCount": len(analysis.get("selectedChapters") or []),
                "status": str(analysis.get("status") or "unknown"),
                "updatedAt": int(directory.stat().st_mtime * 1000),
            })
    return success_response(
        data={"items": items[:30]},
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
    ).model_dump(by_alias=True)


@router.post("/breakdown/analyze")
async def breakdown_analyze(payload: BreakdownRequest, request: Request) -> dict[str, Any]:
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="contentBase64 不是有效的 Base64 文件内容") from exc
    if not payload.file_name.lower().endswith(".txt"):
        raise HTTPException(status_code=400, detail="拆书目前仅支持 .txt 文件")
    try:
        result = analyze_novel(raw, Path(payload.file_name).name, payload.options.chapter_pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    enhanced, analysis_error = await _analyze_reference_with_ai(
        result=result,
        chapter_chunks=reference_chapter_chunks(raw, result),
    )
    if not enhanced:
        raise HTTPException(
            status_code=503,
            detail=analysis_error or "AI 拆书分析不可用：请确认 Coomi 模型已配置、网络可用，并稍后重试。",
        )
    result["studyCards"] = enhanced["studyCards"]
    result["motherCards"] = enhanced["motherCards"]
    result["analysisMode"] = "ai_reference_analysis"
    analysis_id = str(uuid4())
    result["analysisId"] = analysis_id
    root = get_settings().global_root / "breakdowns" / analysis_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.txt").write_bytes(raw)
    (root / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return success_response(
        data=result,
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "breakdown_analyze", "analysisId": analysis_id, "chapterCount": result["chapterCount"]}],
    ).model_dump(by_alias=True)


async def _analyze_reference_with_ai(
    *, result: dict[str, Any], chapter_chunks: list[dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]] | None, str]:
    """Analyze complete first-ten chapters through chunk extraction then aggregation."""
    try:
        chunk_analyses = await _analyze_chunks_with_ai(chapter_chunks)
    except asyncio.TimeoutError:
        return None, "AI 拆书分析超时：参考书前十章较长，请稍后重试。"
    except Exception:
        return None, "AI 拆书分析失败：请确认模型服务可用后重试。"
    prompt = {
        "task": "对热榜书前十章做结构研究，为原创新书提供抽象参考。",
        "chunkAnalyses": chunk_analyses,
        "requirements": [
            "只分析结构机制，不复述原文，不输出原书的长段落。",
            "studyCards 必须与每章一一对应，字段：chapterIndex, chapterTitle, function, readerQuestion, conflict, informationShift, relationshipShift, endHook。",
            "motherCards 生成 3 至 6 张抽象创意母卡，字段：id, title, type, mechanism, useFor, doNotReuse。",
            "doNotReuse 必须包含对人物、设定、事件链、表达的禁止复用约束。",
            "只输出 JSON 对象：{studyCards: [], motherCards: []}。",
        ],
    }
    try:
        response = await _call_creative_provider(
            system="你是专业小说编辑和拆书研究员。严格区分结构规律与原书具体内容，不生成改写或复述。",
            prompt=prompt,
            purpose="breakdown_reference_analysis",
            timeout=90,
        )
        payload = _extract_json_object(str(getattr(response, "content", "") or ""))
        normalized = _normalize_reference_analysis(payload, result)
        if normalized:
            return normalized, ""
        return None, "AI 返回的拆书结构不完整，请重试。"
    except asyncio.TimeoutError:
        return None, "AI 拆书分析超时：前十章结构较长，请稍后重试。"
    except Exception:
        return None, "AI 拆书分析失败：请确认模型服务可用后重试。"


async def _analyze_chunks_with_ai(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(3)

    async def analyze_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "task": "提炼小说章节分块的结构事实，供后续章节汇总使用。",
            "chapterIndex": chunk["chapterIndex"],
            "chapterTitle": chunk["chapterTitle"],
            "chunkIndex": chunk["chunkIndex"],
            "text": chunk["text"],
            "requirements": [
                "不复述原文，不引用长段落。",
                "输出 JSON 对象：summary, events, conflict, informationShift, relationshipShift, suspense。",
                "events 为不超过 5 条的简短事实数组，其余字段为简短字符串。",
            ],
        }
        async with semaphore:
            response = await _call_creative_provider(
                system="你是小说结构分析师。只提炼当前分块的结构事实，不做改写。",
                prompt=prompt,
                purpose="breakdown_chunk_analysis",
                timeout=75,
            )
        parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
        fields = ("summary", "conflict", "informationShift", "relationshipShift", "suspense")
        if not all(str(parsed.get(field) or "").strip() for field in fields):
            raise ValueError("AI 分块分析结构不完整")
        return {
            "chapterIndex": chunk["chapterIndex"],
            "chapterTitle": chunk["chapterTitle"],
            "chunkIndex": chunk["chunkIndex"],
            "summary": str(parsed["summary"]).strip()[:700],
            "events": _string_list(parsed.get("events"))[:5],
            **{field: str(parsed[field]).strip()[:500] for field in fields if field != "summary"},
        }

    return await asyncio.gather(*(analyze_chunk(chunk) for chunk in chunks))


@router.post("/breakdown/ideas/generate")
async def generate_breakdown_ideas(payload: IdeaGenerationRequest, request: Request) -> dict[str, Any]:
    analysis_root = get_settings().global_root / "breakdowns" / payload.analysis_id
    analysis_path = analysis_root / "analysis.json"
    if not analysis_path.exists():
        raise HTTPException(status_code=404, detail="未找到拆书分析记录，请重新上传参考书。")
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="拆书分析记录无法读取。") from exc
    available = {
        str(item.get("id") or ""): item
        for item in analysis.get("motherCards", [])
        if isinstance(item, dict)
    }
    cards = [available[card_id] for card_id in payload.mother_card_ids if card_id in available]
    if not cards:
        raise HTTPException(status_code=400, detail="请选择至少一张有效的脑洞母卡。")

    idea_run_id = str(uuid4())
    ideas, generation_mode, generation_error = await _generate_ideas_with_ai(
        cards=cards,
        project_name=payload.project_name,
        genre=payload.genre,
        tone=payload.tone,
        target_audience=payload.target_audience,
    )
    if not ideas:
        raise HTTPException(
            status_code=503,
            detail=generation_error or "AI 脑洞生成不可用：请确认 Coomi 模型已配置、网络可用，并稍后重试。",
        )
    result = {
        "ideaRunId": idea_run_id,
        "analysisId": payload.analysis_id,
        "projectName": payload.project_name,
        "generationMode": generation_mode,
        "ideas": ideas,
        "notice": "候选基于抽象母卡生成，未使用参考书原文；确认后可作为新书立项素材。",
    }
    ideas_root = analysis_root / "idea-runs"
    ideas_root.mkdir(parents=True, exist_ok=True)
    (ideas_root / f"{idea_run_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    project = get_project_service().current_project()
    project_root = Path(project["workspaceRoot"])
    link_root = project_root / ".storydex" / "references" / "brainstorm"
    link_root.mkdir(parents=True, exist_ok=True)
    (link_root / f"{idea_run_id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["linkedProject"] = str(project.get("projectName") or project_root.name)
    return success_response(
        data=result,
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "generate_breakdown_ideas", "analysisId": payload.analysis_id, "ideaRunId": idea_run_id}],
    ).model_dump(by_alias=True)


async def _generate_ideas_with_ai(
    *,
    cards: list[dict[str, Any]],
    project_name: str,
    genre: str,
    tone: str,
    target_audience: str,
) -> tuple[list[dict[str, Any]], str, str]:
    """Use the configured Coomi provider only; never synthesize fallback ideas."""
    prompt = {
        "projectName": project_name,
        "genre": genre,
        "tone": tone,
        "targetAudience": target_audience,
        "motherCards": cards,
        "requirements": [
            "生成 6 条彼此差异明显的新书脑洞。",
            "只能使用母卡的抽象机制，不能复用参考书人物、设定、情节、谜底或表达。",
            "每条包含 title、logline、storyEngine、openingPlan。",
            "只输出 JSON 数组，不要 Markdown。",
        ],
    }
    try:
        response = await _call_creative_provider(
            system="你是小说创意总监。你只处理抽象创作机制，严格避免对参考书的改写或近似复述。",
            prompt=prompt,
            purpose="breakdown_idea_generation",
            timeout=120,
        )
        parsed = _extract_idea_array(str(getattr(response, "content", "") or ""))
        ideas = _normalize_ai_ideas(parsed, cards)
        if ideas:
            return ideas, "ai_originality_guard", ""
        return [], "ai_unavailable", "AI 返回内容不符合脑洞卡格式，请重试。"
    except asyncio.TimeoutError:
        return [], "ai_unavailable", "AI 脑洞生成超时：模型响应超过 120 秒，请稍后重试。"
    except Exception as exc:
        # Do not expose provider internals or credentials, but preserve the actionable failure class.
        label = type(exc).__name__
        return [], "ai_unavailable", f"AI 脑洞生成失败（{label}）：请检查模型服务后重试。"


async def _call_creative_provider(*, system: str, prompt: dict[str, Any], purpose: str, timeout: int) -> Any:
    from services.coomi_agent_service import _call_provider_chat, _storydex_coomi_home
    from services.llm_replay import get_replayable_llm_provider, llm_purpose

    messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
    with _storydex_coomi_home():
        with llm_purpose(purpose):
            provider = get_replayable_llm_provider()
            return await asyncio.wait_for(_call_provider_chat(provider, messages, None), timeout=timeout)


def _extract_idea_array(content: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"^\s*\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60\s*$", "", content.strip(), flags=re.I)
    payload: Any
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        # Providers sometimes add a short introduction despite being asked for raw JSON.
        # Find the outer array without accepting arbitrary non-JSON content as an idea.
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start < 0 or end <= start:
            raise
        payload = json.loads(cleaned[start : end + 1])
    if isinstance(payload, dict):
        payload = payload.get("ideas")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _extract_json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60\s*$", "", content.strip(), flags=re.I)
    payload = json.loads(cleaned)
    return payload if isinstance(payload, dict) else {}


def _normalize_reference_analysis(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, list[dict[str, Any]]] | None:
    raw_study = payload.get("studyCards") if isinstance(payload.get("studyCards"), list) else []
    raw_mother = payload.get("motherCards") if isinstance(payload.get("motherCards"), list) else []
    chapter_by_index = {
        int(item.get("index")): item for item in result.get("selectedChapters", []) if isinstance(item, dict)
    }
    study_cards: list[dict[str, Any]] = []
    for item in raw_study:
        if not isinstance(item, dict):
            continue
        index = int(item.get("chapterIndex") or 0)
        chapter = chapter_by_index.get(index)
        if not chapter:
            continue
        fields = ("function", "readerQuestion", "conflict", "informationShift", "relationshipShift", "endHook")
        if not all(str(item.get(field) or "").strip() for field in fields):
            continue
        study_cards.append({
            "id": f"study-chapter-{index}",
            "chapterIndex": index,
            "chapterTitle": str(item.get("chapterTitle") or chapter["title"]).strip(),
            "evidence": chapter["evidence"],
            "status": "AI 已分析",
            **{field: str(item[field]).strip()[:600] for field in fields},
        })
    mother_cards: list[dict[str, Any]] = []
    for index, item in enumerate(raw_mother[:6]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        mechanism = str(item.get("mechanism") or "").strip()
        use_for = _string_list(item.get("useFor"))
        do_not_reuse = _string_list(item.get("doNotReuse"))
        if not title or not mechanism or not use_for or not do_not_reuse:
            continue
        mother_cards.append({
            "id": f"mother-ai-{index + 1}",
            "title": title[:100],
            "type": str(item.get("type") or "creative_mechanism")[:64],
            "mechanism": mechanism[:700],
            "useFor": [str(value)[:80] for value in use_for[:5]],
            "doNotReuse": [str(value)[:120] for value in do_not_reuse[:6]],
        })
    if len(study_cards) != len(chapter_by_index) or len(mother_cards) < 3:
        return None
    return {"studyCards": study_cards, "motherCards": mother_cards}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []


def _normalize_ai_ideas(
    ideas: list[dict[str, Any]], cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    source_ids = [str(card.get("id") or "") for card in cards]
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(ideas[:6]):
        title = str(item.get("title") or "").strip()
        logline = str(item.get("logline") or "").strip()
        story_engine = str(item.get("storyEngine") or "").strip()
        opening_plan = str(item.get("openingPlan") or "").strip()
        if not all((title, logline, story_engine, opening_plan)):
            continue
        normalized.append({
            "id": f"idea-{index + 1}",
            "title": title[:100],
            "logline": logline[:500],
            "storyEngine": story_engine[:500],
            "openingPlan": opening_plan[:500],
            "derivedFrom": source_ids,
            "derivationMethods": ["AI 抽象发散"],
            "originalityConstraints": ["不得复用参考书人物、设定、事件链或表达", "仅使用抽象机制", "正文创作不注入参考原文"],
        })
    return normalized
