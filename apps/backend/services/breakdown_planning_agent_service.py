from __future__ import annotations

import asyncio
import json
import re
from typing import Any


class BreakdownPlanningAgent:
    """Transforms a reference rhythm into a source-isolated new-book plan."""

    async def build_reference_rhythm(self, study_cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cards = [
            {
                "chapterIndex": int(card.get("chapterIndex") or 0),
                "function": str(card.get("function") or "")[:220],
                "conflict": str(card.get("conflict") or "")[:220],
                "informationShift": str(card.get("informationShift") or "")[:220],
                "relationshipShift": str(card.get("relationshipShift") or "")[:220],
                "endHook": str(card.get("endHook") or "")[:220],
            }
            for card in study_cards
            if isinstance(card, dict)
        ]
        payload = await self._ask(
            system="你是拆书规划 Agent。你的职责是保留章节节奏关系，同时彻底去除参考书的具体内容。",
            purpose="breakdown_reference_rhythm",
            prompt={
                "task": "将十章研究卡转换为逐章结构节奏档案。",
                "studyCards": cards,
                "requirements": [
                    "输出 chapters 数组，必须恰好十项；每项包含 chapterIndex、narrativeMotion、tensionTransition、informationRelease、readerContract、hookShape。",
                    "逐章保留原有节奏的先后关系与升级方式，不得使用固定章节标签或统一模板。",
                    "只能描述叙事动作和读者体验；不得包含人物、地名、组织、物件、能力、设定、事件、谜底或原句。",
                    "每个字段不超过 90 字；只输出 JSON 对象：{chapters: []}。",
                ],
            },
        )
        return _normalize_plan(payload, fields=_RHYTHM_FIELDS)

    async def build_new_book_plan(self, idea: dict[str, Any], reference_rhythm: list[dict[str, Any]]) -> list[dict[str, Any]]:
        premise = {
            field: str(idea.get(field) or "").strip()[:500]
            for field in ("title", "genre", "protagonist", "coreRule", "mainConflict", "longTermEngine", "tenChapterPromise")
        }
        payload = await self._ask(
            system="你是拆书规划 Agent。你只用抽象节奏档案和原创立项，生成新书方案，绝不复述或猜测参考作品。",
            purpose="new_book_chapter_plan",
            prompt={
                "task": "为原创立项生成前十章规划，并逐章继承抽象节奏档案中的节奏关系。",
                "originalPremise": premise,
                "referenceRhythm": reference_rhythm,
                "requirements": [
                    "输出 chapters 数组，必须恰好十项；每项包含 chapterIndex、narrativeTask、conflictProgress、informationProgress、endQuestion。",
                    "chapterIndex 必须与 referenceRhythm 一一对应，继承其叙事运动、压力转折、信息释放和钩子形态，但内容必须由 originalPremise 的原创人物、规则和冲突构成。",
                    "不得引用、猜测或补充任何参考书人物、设定、事件、物件、专有名词或措辞。",
                    "每个字段不超过 100 字；只输出 JSON 对象：{chapters: []}。",
                ],
            },
        )
        return _normalize_plan(payload, fields=_NEW_BOOK_FIELDS)

    async def _ask(self, *, system: str, purpose: str, prompt: dict[str, Any]) -> dict[str, Any]:
        response = await self.run_model_turn(system=system, purpose=purpose, prompt=prompt, timeout=120)
        return _extract_json_object(str(getattr(response, "content", "") or ""))

    async def run_model_turn(self, *, system: str, purpose: str, prompt: dict[str, Any], timeout: int) -> Any:
        """Single execution gateway for every AI turn in the breakdown workflow."""
        from services.coomi_agent_service import _call_provider_chat, _storydex_coomi_home
        from services.llm_replay import get_replayable_llm_provider, llm_purpose

        messages = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}]
        with _storydex_coomi_home():
            with llm_purpose(purpose):
                provider = get_replayable_llm_provider()
                return await asyncio.wait_for(_call_provider_chat(provider, messages, None), timeout=timeout)


_RHYTHM_FIELDS = ("narrativeMotion", "tensionTransition", "informationRelease", "readerContract", "hookShape")
_NEW_BOOK_FIELDS = ("narrativeTask", "conflictProgress", "informationProgress", "endQuestion")


def get_breakdown_planning_agent() -> BreakdownPlanningAgent:
    return BreakdownPlanningAgent()


def _normalize_plan(payload: dict[str, Any], *, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    raw_chapters = payload.get("chapters") if isinstance(payload.get("chapters"), list) else []
    plan: list[dict[str, Any]] = []
    for item in raw_chapters:
        if not isinstance(item, dict):
            continue
        index = int(item.get("chapterIndex") or 0)
        if index < 1 or index > 10 or not all(str(item.get(field) or "").strip() for field in fields):
            continue
        plan.append({"chapterIndex": index, **{field: str(item[field]).strip()[:300] for field in fields}})
    plan.sort(key=lambda item: int(item["chapterIndex"]))
    return plan if [item["chapterIndex"] for item in plan] == list(range(1, 11)) else []


def _extract_json_object(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^\s*\x60\x60\x60(?:json)?\s*|\s*\x60\x60\x60\s*$", "", content.strip(), flags=re.I)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return payload if isinstance(payload, dict) else {}
