from __future__ import annotations

import base64
import binascii
import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.response import ApiTrace, success_response
from core.config import get_settings
from services.book_breakdown_service import analyze_novel, decode_novel, reference_chapter_chunks
from services.breakdown_planning_agent_service import get_breakdown_planning_agent
from services.project_service import get_project_service

router = APIRouter(tags=["breakdown"])

BREAKDOWN_JOBS: dict[str, dict[str, Any]] = {}
ProgressReporter = Callable[[str], None]


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
    requirement: str = Field(default="", max_length=1000)
    model_config = ConfigDict(populate_by_name=True)


class IdeaSelectionRequest(BaseModel):
    analysis_id: str = Field(alias="analysisId")
    idea_run_id: str = Field(alias="ideaRunId")
    idea_id: str = Field(alias="ideaId")
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


@router.get("/breakdown/{analysis_id}")
def breakdown_detail(analysis_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", analysis_id):
        raise HTTPException(status_code=400, detail="拆书记录标识无效。")
    analysis_path = get_settings().global_root / "breakdowns" / analysis_id / "analysis.json"
    try:
        result = json.loads(analysis_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="未找到这条拆书记录。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="拆书记录无法读取。") from exc
    if not isinstance(result, dict):
        raise HTTPException(status_code=500, detail="拆书记录格式无效。")
    idea_runs_root = analysis_path.parent / "idea-runs"
    if idea_runs_root.is_dir():
        for run_path in sorted(idea_runs_root.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                latest_run = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(latest_run, dict) and isinstance(latest_run.get("ideas"), list):
                result["latestIdeaRun"] = latest_run
                break
    return success_response(
        data=result,
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "read_breakdown", "analysisId": analysis_id}],
    ).model_dump(by_alias=True)


@router.delete("/breakdown/{analysis_id}")
def delete_breakdown(analysis_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", analysis_id):
        raise HTTPException(status_code=400, detail="拆书记录标识无效。")
    root = get_settings().global_root / "breakdowns" / analysis_id
    if not (root / "analysis.json").exists():
        raise HTTPException(status_code=404, detail="未找到这条拆书记录。")
    project = get_project_service().current_project()
    project_root = Path(project["workspaceRoot"])
    active_path = project_root / ".storydex" / "references" / "brainstorm" / "active.json"
    try:
        active = json.loads(active_path.read_text(encoding="utf-8")) if active_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        active = {}
    if str(active.get("analysisId") or "") == analysis_id:
        active_path.unlink(missing_ok=True)
    link_root = active_path.parent
    if link_root.is_dir():
        for link_path in link_root.glob("*.json"):
            if link_path.name == "active.json":
                continue
            try:
                link = json.loads(link_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(link.get("analysisId") or "") == analysis_id:
                link_path.unlink(missing_ok=True)
    shutil.rmtree(root)
    return success_response(
        data={"analysisId": analysis_id, "deleted": True},
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "delete_breakdown", "analysisId": analysis_id}],
    ).model_dump(by_alias=True)


@router.post("/breakdown/ideas/select")
async def select_breakdown_idea(payload: IdeaSelectionRequest, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", payload.analysis_id) or not re.fullmatch(r"[0-9a-fA-F-]{36}", payload.idea_run_id):
        raise HTTPException(status_code=400, detail="脑洞记录标识无效。")
    run_path = get_settings().global_root / "breakdowns" / payload.analysis_id / "idea-runs" / f"{payload.idea_run_id}.json"
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="未找到这次脑洞生成记录。") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="脑洞生成记录无法读取。") from exc
    ideas = run.get("ideas") if isinstance(run.get("ideas"), list) else []
    idea = next((item for item in ideas if isinstance(item, dict) and str(item.get("id") or "") == payload.idea_id), None)
    if idea is None:
        raise HTTPException(status_code=404, detail="未找到所选的新书脑洞。")
    required_idea_fields = ("genre", "protagonist", "coreRule", "mainConflict", "longTermEngine", "tenChapterPromise")
    if not all(str(idea.get(field) or "").strip() for field in required_idea_fields):
        raise HTTPException(
            status_code=409,
            detail="这是一张旧版候选，缺少原创立项字段，不能设为主脑洞。请重新生成新版脑洞候选。",
        )
    analysis_path = get_settings().global_root / "breakdowns" / payload.analysis_id / "analysis.json"
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="拆书结构记录无法读取。") from exc
    style_profile = analysis.get("styleProfile") if isinstance(analysis.get("styleProfile"), dict) else {}
    reference_rhythm = analysis.get("referenceRhythm") if isinstance(analysis.get("referenceRhythm"), list) else []
    if len(reference_rhythm) != 10:
        raise HTTPException(status_code=409, detail="该拆书记录缺少逐章节奏档案，请重新分析参考书后再设为主脑洞。")
    try:
        chapter_plan = await get_breakdown_planning_agent().build_new_book_plan(idea, reference_rhythm)
    except asyncio.TimeoutError:
        chapter_plan = []
        plan_error = "新书十章规划超时：模型在 120 秒内未完成，请稍后重试。"
    except Exception as exc:
        chapter_plan = []
        plan_error = f"新书十章规划生成失败（{type(exc).__name__}）：请检查模型服务后重试。"
    else:
        plan_error = "" if chapter_plan else "AI 返回的新书十章规划格式不完整，请重试。"
    if not chapter_plan:
        raise HTTPException(
            status_code=503,
            detail=plan_error or "新书十章规划生成失败，请确认模型服务后重试。",
        )

    project = get_project_service().current_project()
    project_root = Path(project["workspaceRoot"])
    active_path = project_root / ".storydex" / "references" / "brainstorm" / "active.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    # Candidates belong to the breakdown record; only the confirmed idea belongs to the project.
    (active_path.parent / f"{payload.idea_run_id}.json").unlink(missing_ok=True)
    selected = {
        "status": "active",
        "selectedAt": datetime.now(timezone.utc).isoformat(),
        "analysisId": payload.analysis_id,
        "ideaRunId": payload.idea_run_id,
        "ideaId": payload.idea_id,
        "projectName": str(project.get("projectName") or project_root.name),
        "idea": idea,
        "chapterStructureReference": chapter_plan,
        "writingStyleReference": style_profile,
        "originalityVerified": True,
    }
    active_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    return success_response(
        data={
            "selectedIdeaId": payload.idea_id,
            "projectName": selected["projectName"],
            "idea": idea,
            "chapterStructureCount": len(chapter_plan),
        },
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "select_breakdown_idea", "analysisId": payload.analysis_id, "ideaRunId": payload.idea_run_id, "ideaId": payload.idea_id}],
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
        analyze_novel(raw, Path(payload.file_name).name, payload.options.chapter_pattern)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job_id = str(uuid4())
    BREAKDOWN_JOBS[job_id] = {"status": "running", "events": [], "result": None, "error": ""}
    _report_breakdown_job(job_id, "已接收 TXT，正在解析前十章范围。")
    asyncio.create_task(_run_breakdown_job(job_id, raw, payload.file_name, payload.options.chapter_pattern))
    return success_response(
        data={"jobId": job_id, "status": "running"},
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
        audit=[{"action": "breakdown_analyze", "jobId": job_id}],
    ).model_dump(by_alias=True)


@router.get("/breakdown/jobs/{job_id}")
def breakdown_job(job_id: str, request: Request) -> dict[str, Any]:
    job = BREAKDOWN_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="拆书任务不存在或已失效。")
    return success_response(
        data={"jobId": job_id, **job},
        trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4())),
    ).model_dump(by_alias=True)


@router.post("/breakdown/{analysis_id}/rhythm/retry")
async def retry_breakdown_rhythm(analysis_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", analysis_id):
        raise HTTPException(status_code=400, detail="拆书记录标识无效。")
    analysis_path = get_settings().global_root / "breakdowns" / analysis_id / "analysis.json"
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="未找到可继续的拆书记录。") from exc
    if not isinstance(analysis.get("studyCards"), list) or len(analysis["studyCards"]) != 10:
        raise HTTPException(status_code=409, detail="该记录尚未完成章节研究，无法继续节奏档案。")
    job_id = str(uuid4())
    BREAKDOWN_JOBS[job_id] = {"status": "running", "events": [], "result": None, "error": ""}
    _report_breakdown_job(job_id, "已载入保存的章节研究，继续生成逐章节奏档案。")
    asyncio.create_task(_run_rhythm_retry_job(job_id, analysis_path, analysis))
    return success_response(data={"jobId": job_id, "status": "running"}, trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4()))).model_dump(by_alias=True)


@router.post("/breakdown/{analysis_id}/continue")
async def continue_breakdown(analysis_id: str, request: Request) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", analysis_id):
        raise HTTPException(status_code=400, detail="拆书记录标识无效。")
    analysis_path = get_settings().global_root / "breakdowns" / analysis_id / "analysis.json"
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=404, detail="未找到可继续的拆书记录。") from exc
    if not isinstance(analysis.get("chunkAnalyses"), list):
        raise HTTPException(status_code=409, detail="该旧记录没有保存分块恢复点，无法继续；请重新提交一次 TXT。")
    job_id = str(uuid4())
    BREAKDOWN_JOBS[job_id] = {"status": "running", "events": [], "result": None, "error": ""}
    _report_breakdown_job(job_id, "已加载最近恢复点，继续拆书任务。")
    asyncio.create_task(_run_continue_job(job_id, analysis_path, analysis))
    return success_response(data={"jobId": job_id, "status": "running"}, trace=ApiTrace(traceId=request.headers.get("x-trace-id") or str(uuid4()))).model_dump(by_alias=True)


def _report_breakdown_job(job_id: str, content: str) -> None:
    job = BREAKDOWN_JOBS.get(job_id)
    if job is None:
        return
    job["events"].append({"id": str(uuid4()), "content": content, "timestamp": datetime.now(timezone.utc).isoformat()})


async def _run_breakdown_job(job_id: str, raw: bytes, file_name: str, chapter_pattern: str) -> None:
    job = BREAKDOWN_JOBS[job_id]
    result: dict[str, Any] | None = None
    try:
        result = analyze_novel(raw, Path(file_name).name, chapter_pattern)
        analysis_id = str(uuid4())
        result["analysisId"] = analysis_id
        root = get_settings().global_root / "breakdowns" / analysis_id
        root.mkdir(parents=True, exist_ok=True)
        (root / "source.txt").write_bytes(raw)

        def checkpoint(update: dict[str, Any]) -> None:
            result.update(update)
            result["status"] = "partial"
            (root / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        chapter_chunks = reference_chapter_chunks(raw, result)
        _report_breakdown_job(job_id, f"已识别前 {len(result['selectedChapters'])} 章，分为 {len(chapter_chunks)} 个分析块。")
        enhanced, analysis_error = await _analyze_reference_with_ai(
            result=result,
            chapter_chunks=chapter_chunks,
            progress=lambda content: _report_breakdown_job(job_id, content),
            checkpoint=checkpoint,
        )
        if not enhanced:
            raise RuntimeError(analysis_error or "AI 拆书分析不可用：请确认 Coomi 模型已配置、网络可用，并稍后重试。")
        result["studyCards"] = enhanced["studyCards"]
        result["motherCards"] = enhanced["motherCards"]
        result["styleProfile"] = enhanced["styleProfile"]
        result["referenceRhythm"] = enhanced["referenceRhythm"]
        result["analysisMode"] = "ai_reference_analysis"
        result["status"] = "completed"
        (root / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        job["result"] = result
        job["status"] = "completed"
        _report_breakdown_job(job_id, "拆书研究完成，章节卡、节奏档案、母卡和风格研究已保存。")
    except Exception as exc:
        job["error"] = str(exc) or "AI 拆书分析失败，请重试。"
        if result and isinstance(result.get("chunkAnalyses"), list):
            result["status"] = "partial"
            result.setdefault("warnings", []).append(f"已保留可继续的中间研究：{job['error']}")
            analysis_path = get_settings().global_root / "breakdowns" / str(result["analysisId"]) / "analysis.json"
            analysis_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            job["result"] = result
            job["status"] = "partial"
        else:
            job["status"] = "failed"
        _report_breakdown_job(job_id, job["error"])


async def _run_rhythm_retry_job(job_id: str, analysis_path: Path, analysis: dict[str, Any]) -> None:
    job = BREAKDOWN_JOBS[job_id]
    try:
        report = lambda content: _report_breakdown_job(job_id, content)
        report("拆书规划 Agent 正在根据已保存的十张章节研究卡生成节奏档案。")
        rhythm = await get_breakdown_planning_agent().build_reference_rhythm(analysis["studyCards"])
        if not rhythm:
            raise RuntimeError("AI 节奏档案未返回完整十章结果。")
        analysis["referenceRhythm"] = rhythm
        analysis["status"] = "completed"
        analysis["analysisMode"] = "ai_reference_analysis"
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        job["result"] = analysis
        job["status"] = "completed"
        report("逐章节奏档案已完成并关联到保存的拆书记录。")
    except Exception as exc:
        job["status"] = "partial"
        job["result"] = analysis
        job["error"] = str(exc) or "逐章节奏档案继续失败。"
        _report_breakdown_job(job_id, job["error"])


async def _run_continue_job(job_id: str, analysis_path: Path, analysis: dict[str, Any]) -> None:
    job = BREAKDOWN_JOBS[job_id]
    try:
        report = lambda content: _report_breakdown_job(job_id, content)
        study_cards = analysis.get("studyCards") if isinstance(analysis.get("studyCards"), list) and all(
            str(item.get("status") or "") == "AI 已分析" for item in analysis["studyCards"] if isinstance(item, dict)
        ) else None
        if not study_cards:
            report("正在从已保存的分块事实汇总十张章节研究卡。")
            study_cards = await _build_study_cards_with_ai(analysis, analysis["chunkAnalyses"])
            analysis["studyCards"] = study_cards
            analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        materials_ready = isinstance(analysis.get("styleProfile"), dict) and isinstance(analysis.get("motherCards"), list) and len(analysis["motherCards"]) >= 3
        if not materials_ready:
            report("正在从保存的章节研究卡提炼母卡与写作风格。")
            materials = await _build_mother_cards_and_style_with_ai(study_cards)
            if not materials:
                raise RuntimeError("AI 返回的母卡与风格结构不完整。")
            analysis.update(materials)
            analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        await _run_rhythm_retry_job(job_id, analysis_path, analysis)
    except Exception as exc:
        analysis["status"] = "partial"
        analysis.setdefault("warnings", []).append(f"可继续的拆书阶段失败：{str(exc)}")
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        job["status"] = "partial"
        job["result"] = analysis
        job["error"] = str(exc) or "继续拆书失败。"
        _report_breakdown_job(job_id, job["error"])


async def _analyze_reference_with_ai(
    *, result: dict[str, Any], chapter_chunks: list[dict[str, Any]], progress: ProgressReporter | None = None,
    checkpoint: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Analyze complete first-ten chapters through chunk extraction then aggregation."""
    report = progress or (lambda _content: None)
    save = checkpoint or (lambda _update: None)
    try:
        report("拆书规划 Agent 正在逐块提炼章节事实。")
        chunk_analyses = await _analyze_chunks_with_ai(chapter_chunks, progress=report)
    except asyncio.TimeoutError:
        return None, "AI 拆书分析超时：参考书前十章较长，请稍后重试。"
    except Exception:
        return None, "AI 拆书分析失败：请确认模型服务可用后重试。"
    save({"chunkAnalyses": chunk_analyses})
    try:
        report("分块事实已完成，正在汇总十张章节研究卡。")
        study_cards = await _build_study_cards_with_ai(result, chunk_analyses)
    except asyncio.TimeoutError:
        return None, "AI 章节研究卡生成超时：请稍后重试。"
    except Exception:
        return None, "AI 章节研究卡生成失败：请确认模型服务可用后重试。"
    save({"studyCards": study_cards})
    try:
        report("章节研究卡已完成，正在提炼母卡与写作风格。")
        materials = await _build_mother_cards_and_style_with_ai(study_cards)
    except asyncio.TimeoutError:
        return None, "AI 母卡与风格汇总超时：请稍后重试。"
    except Exception:
        return None, "AI 母卡与风格汇总失败：请确认模型服务可用后重试。"
    if not materials:
        return None, "AI 返回的母卡或风格结构不完整，请重试。"
    save(materials)
    try:
        report("母卡与风格研究已完成，正在生成逐章节奏档案。")
        reference_rhythm = await get_breakdown_planning_agent().build_reference_rhythm(study_cards)
    except asyncio.TimeoutError:
        return None, "AI 逐章节奏档案生成超时：请稍后重试。"
    except Exception:
        return None, "AI 逐章节奏档案生成失败：请确认模型服务可用后重试。"
    if not reference_rhythm:
        return None, "AI 返回的逐章节奏档案不完整，请重试。"
    report("逐章节奏档案已生成。")
    return {"studyCards": study_cards, "referenceRhythm": reference_rhythm, **materials}, ""


async def _build_study_cards_with_ai(result: dict[str, Any], chunk_analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_chapter: dict[int, list[dict[str, Any]]] = {}
    for item in chunk_analyses:
        index = int(item.get("chapterIndex") or 0)
        if index > 0:
            by_chapter.setdefault(index, []).append(item)
    semaphore = asyncio.Semaphore(3)

    async def build(chapter: dict[str, Any]) -> dict[str, Any]:
        index = int(chapter["index"])
        prompt = {
            "task": "基于本章分块事实，生成一张小说结构研究卡。",
            "chapterIndex": index,
            "chapterTitle": chapter["title"],
            "chunkAnalyses": by_chapter.get(index, []),
            "requirements": [
            "只分析结构机制，不复述原文或引用长段落。",
                "输出 JSON 对象，字段：function, readerQuestion, conflict, informationShift, relationshipShift, endHook。",
                "每个字段不超过 180 字。",
            ],
        }
        async with semaphore:
            response = await _call_creative_provider(
                system="你是小说章节结构编辑。只提炼本章节奏和叙事功能，不做改写。",
                prompt=prompt,
                purpose="breakdown_chapter_card",
                timeout=75,
            )
        parsed = _extract_json_object(str(getattr(response, "content", "") or ""))
        fields = ("function", "readerQuestion", "conflict", "informationShift", "relationshipShift", "endHook")
        if not all(str(parsed.get(field) or "").strip() for field in fields):
            raise ValueError(f"第 {index} 章研究卡结构不完整")
        return {
            "id": f"study-chapter-{index}",
            "chapterIndex": index,
            "chapterTitle": str(chapter["title"]),
            "evidence": chapter["evidence"],
            "status": "AI 已分析",
            **{field: str(parsed[field]).strip()[:600] for field in fields},
        }

    chapters = [item for item in result.get("selectedChapters", []) if isinstance(item, dict)]
    cards = await asyncio.gather(*(build(chapter) for chapter in chapters))
    return sorted(cards, key=lambda item: int(item["chapterIndex"]))


async def _build_mother_cards_and_style_with_ai(study_cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    compact_cards = [
        {
            "chapterIndex": card["chapterIndex"],
            "function": str(card["function"])[:180],
            "conflict": str(card["conflict"])[:180],
            "informationShift": str(card["informationShift"])[:180],
            "endHook": str(card["endHook"])[:180],
        }
        for card in study_cards
    ]
    prompt = {
        "task": "从十章结构研究卡中提炼原创可用的母卡与写作风格方法。",
        "studyCards": compact_cards,
        "requirements": [
            "motherCards 生成 3 至 6 张抽象创意母卡，字段：title, type, mechanism, useFor, doNotReuse。",
            "styleProfile 输出字段：narrativePerspective, sentenceRhythm, languageTexture, dialogueStrategy, hookTechnique, avoidReuse。",
            "只描述可迁移结构与写法，不包含原书人物、设定、事件或原句。",
            "只输出 JSON 对象：{motherCards: [], styleProfile: {}}。",
        ],
    }
    response = await _call_creative_provider(
        system="你是小说编辑。提炼抽象创作机制和风格方法，严格避免复述参考书具体内容。",
        prompt=prompt,
        purpose="breakdown_materials_analysis",
        timeout=90,
    )
    payload = _extract_json_object(str(getattr(response, "content", "") or ""))
    return _normalize_mother_cards_and_style(payload)


async def _analyze_chunks_with_ai(chunks: list[dict[str, Any]], *, progress: ProgressReporter | None = None) -> list[dict[str, Any]]:
    # Smaller inputs and restrained concurrency keep slow model providers from queueing out.
    semaphore = asyncio.Semaphore(2)
    completed = 0
    completed_lock = asyncio.Lock()
    report = progress or (lambda _content: None)

    async def analyze_once(chunk: dict[str, Any]) -> dict[str, Any]:
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
                timeout=100,
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

    async def analyze_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal completed
        try:
            item = await analyze_once(chunk)
            async with completed_lock:
                completed += 1
                report(f"已完成 {completed}/{len(chunks)} 个章节分析块。")
            return [item]
        except asyncio.TimeoutError:
            children = _split_timed_out_chunk(chunk)
            if not children:
                raise
            report(f"第 {chunk.get('chapterIndex')} 章的一个分析块响应较慢，已自动细分重试。")
            # Do not discard a whole ten-chapter study because one slow request timed out.
            results = await asyncio.gather(*(analyze_chunk(child) for child in children))
            return [item for group in results for item in group]

    groups = await asyncio.gather(*(analyze_chunk(chunk) for chunk in chunks))
    return [item for group in groups for item in group]


def _split_timed_out_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Retry a slow model request with two smaller paragraph-aware inputs."""
    text = str(chunk.get("text") or "").strip()
    if len(text) <= 900:
        return []
    midpoint = len(text) // 2
    boundary = text.rfind("\n", max(0, midpoint - 500), min(len(text), midpoint + 500))
    if boundary <= 0:
        boundary = midpoint
    parts = [text[:boundary].strip(), text[boundary:].strip()]
    return [
        {**chunk, "chunkIndex": f"{chunk.get('chunkIndex')}.{index}", "text": part}
        for index, part in enumerate(parts, start=1)
        if part
    ]


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
        requirement=payload.requirement,
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
    requirement: str,
) -> tuple[list[dict[str, Any]], str, str]:
    """Use the configured Coomi provider only; never synthesize fallback ideas."""
    compact_cards = [
        {
            "id": str(card.get("id") or ""),
            "title": str(card.get("title") or "")[:100],
            "type": str(card.get("type") or "")[:64],
            "mechanism": str(card.get("mechanism") or "")[:420],
            "useFor": _string_list(card.get("useFor"))[:3],
            "doNotReuse": _string_list(card.get("doNotReuse"))[:3],
        }
        for card in cards
    ]
    prompt = {
        "projectName": project_name,
        "genre": genre,
        "tone": tone,
        "targetAudience": target_audience,
        "userRequirement": requirement[:1000],
        "motherCards": compact_cards,
        "requirements": [
            "生成 2 条彼此差异明显、可直接立项的新书脑洞。",
            "如 userRequirement 非空，必须在不违背原创立项要求的前提下纳入该需求。",
            "只能使用母卡的抽象机制，不能复用参考书人物、设定、情节、谜底或表达。",
            "必须创建全新的题材设定、人物身份、能力规则与事件起点；不得沿用母卡中的专有名词或具体物件。",
            "每条包含 title、genre、protagonist、coreRule、mainConflict、longTermEngine、tenChapterPromise。每个字段不超过 120 字。",
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
    return await get_breakdown_planning_agent().run_model_turn(
        system=system,
        prompt=prompt,
        purpose=purpose,
        timeout=timeout,
    )


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


def _normalize_reference_analysis(payload: dict[str, Any], result: dict[str, Any]) -> dict[str, Any] | None:
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
    raw_style = payload.get("styleProfile") if isinstance(payload.get("styleProfile"), dict) else {}
    style_fields = ("narrativePerspective", "sentenceRhythm", "languageTexture", "dialogueStrategy", "hookTechnique", "avoidReuse")
    if len(study_cards) != len(chapter_by_index) or len(mother_cards) < 3 or not all(str(raw_style.get(field) or "").strip() for field in style_fields):
        return None
    style_profile = {field: str(raw_style[field]).strip()[:500] for field in style_fields}
    return {"studyCards": study_cards, "motherCards": mother_cards, "styleProfile": style_profile}


def _normalize_mother_cards_and_style(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw_mother = payload.get("motherCards") if isinstance(payload.get("motherCards"), list) else []
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
    raw_style = payload.get("styleProfile") if isinstance(payload.get("styleProfile"), dict) else {}
    style_fields = ("narrativePerspective", "sentenceRhythm", "languageTexture", "dialogueStrategy", "hookTechnique", "avoidReuse")
    if len(mother_cards) < 3 or not all(str(raw_style.get(field) or "").strip() for field in style_fields):
        return None
    return {
        "motherCards": mother_cards,
        "styleProfile": {field: str(raw_style[field]).strip()[:500] for field in style_fields},
    }


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
        genre = str(item.get("genre") or "").strip()
        protagonist = str(item.get("protagonist") or "").strip()
        core_rule = str(item.get("coreRule") or "").strip()
        main_conflict = str(item.get("mainConflict") or "").strip()
        long_term_engine = str(item.get("longTermEngine") or "").strip()
        ten_chapter_promise = str(item.get("tenChapterPromise") or "").strip()
        if not all((title, genre, protagonist, core_rule, main_conflict, long_term_engine, ten_chapter_promise)):
            continue
        normalized.append({
            "id": f"idea-{index + 1}",
            "title": title[:100],
            "genre": genre[:120],
            "protagonist": protagonist[:160],
            "coreRule": core_rule[:240],
            "mainConflict": main_conflict[:240],
            "longTermEngine": long_term_engine[:240],
            "tenChapterPromise": ten_chapter_promise[:240],
            "logline": main_conflict[:500],
            "storyEngine": long_term_engine[:500],
            "openingPlan": ten_chapter_promise[:500],
            "derivedFrom": source_ids,
            "derivationMethods": ["AI 抽象发散"],
            "sourceMechanism": "来源：已参考抽象机制，具体设定、人物和事件均为新生成",
            "originalityConstraints": ["不得复用参考书人物、设定、事件链或表达", "仅使用抽象机制", "正文创作不注入参考原文"],
        })
    return normalized
