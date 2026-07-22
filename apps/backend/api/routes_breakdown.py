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
from services.book_breakdown_service import analyze_novel, generate_idea_cards
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


@router.post("/breakdown/analyze")
def breakdown_analyze(payload: BreakdownRequest, request: Request) -> dict[str, Any]:
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
    fallback_ideas = generate_idea_cards(
        cards,
        project_name=payload.project_name,
        genre=payload.genre,
        tone=payload.tone,
        target_audience=payload.target_audience,
    )
    ideas, generation_mode = await _generate_ideas_with_ai(
        cards=cards,
        fallback_ideas=fallback_ideas,
        project_name=payload.project_name,
        genre=payload.genre,
        tone=payload.tone,
        target_audience=payload.target_audience,
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
    fallback_ideas: list[dict[str, Any]],
    project_name: str,
    genre: str,
    tone: str,
    target_audience: str,
) -> tuple[list[dict[str, Any]], str]:
    """Use the configured Coomi provider, with an explicit safe local fallback."""
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
        from services.coomi_agent_service import _call_provider_chat, _storydex_coomi_home
        from services.llm_replay import get_replayable_llm_provider, llm_purpose

        messages = [
            {
                "role": "system",
                "content": "你是小说创意总监。你只处理抽象创作机制，严格避免对参考书的改写或近似复述。",
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        with _storydex_coomi_home():
            with llm_purpose("breakdown_idea_generation"):
                provider = get_replayable_llm_provider()
                response = await asyncio.wait_for(_call_provider_chat(provider, messages, None), timeout=35)
        parsed = _extract_idea_array(str(getattr(response, "content", "") or ""))
        ideas = _normalize_ai_ideas(parsed, fallback_ideas, cards)
        if ideas:
            return ideas, "ai_originality_guard"
    except Exception:
        pass
    return fallback_ideas, "local_originality_guard"


def _extract_idea_array(content: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"^\s*\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60\s*$", "", content.strip(), flags=re.I)
    payload = json.loads(cleaned)
    if isinstance(payload, dict):
        payload = payload.get("ideas")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _normalize_ai_ideas(
    ideas: list[dict[str, Any]], fallback_ideas: list[dict[str, Any]], cards: list[dict[str, Any]]
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
        base = fallback_ideas[index % len(fallback_ideas)]
        normalized.append({
            **base,
            "id": f"idea-{index + 1}",
            "title": title[:100],
            "logline": logline[:500],
            "storyEngine": story_engine[:500],
            "openingPlan": opening_plan[:500],
            "derivedFrom": source_ids,
        })
    return normalized
