"""Storydex 意图识别服务（项目语义接地版）。

三层混合路由（layered intent routing）：
1. 确定性信号（slash 命令、空输入）直接短路，零成本零误判；
2. LLM 结构化分类：封闭标签集 + 严格 JSON 输出 + 超时控制，
   复用 Coomi 已配置的 LLM provider，无需额外密钥；
3. 关键词启发式兜底：LLM 不可用/超时/输出不合法时退回正则逻辑，
   保证离线与故障场景下功能不中断。

项目语义接地：
- 意图目录（intent catalog）在内置标签之上动态合并项目
  `.storydex/.agent/skills/registry.json`：每个意图携带资产落点
  （assetTargets，如 character_work → .storydex/characters/）与
  项目已注册技能名；自定义技能声明的新 intent 会成为可选标签。
- 分类结果帧携带 assetTargets / matchedSkills，下游（TurnContract
  system prompt、任务规划器）据此知道该意图的产出应写到哪里、
  该用哪些技能。
- 会话级上一轮记忆（prompt + 意图）注入分类上下文，使"继续"
  "然后呢"等省略式请求能延续正确意图。
"""
from __future__ import annotations

import asyncio
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List


INTENT_LABELS: tuple[str, ...] = (
    "story_generation",
    "character_work",
    "worldbook_work",
    "script_work",
    "wiki_work",
    "project_organization",
    "general",
)
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_INTENT_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DEFAULT_LLM_TIMEOUT_SECONDS = 8.0
_MAX_PROMPT_CHARS = 2000
_MAX_SESSION_MEMORY = 256

# 内置意图目录：描述、资产落点（与 TurnContract assetTargets 对齐）、少样本示例。
_BUILTIN_INTENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "story_generation": {
        "description": "撰写、续写、改写或扩写小说正文（章节、场景、片段、正文）",
        "assetTargets": ["chapters/", ".storydex/memory/chapters/"],
        "examples": ["续写下一段", "然后呢", "写第三章的开头"],
    },
    "character_work": {
        "description": "创建或更新角色卡、人物设定、性格、背景与人物关系",
        "assetTargets": [".storydex/characters/"],
        "examples": ["设计一个反派角色", "把女主的背景改成孤儿出身"],
    },
    "worldbook_work": {
        "description": "创建或更新世界书/世界观/设定集条目（地理、势力、魔法体系、历史等）",
        "assetTargets": [".storydex/worldbook/"],
        "examples": ["完善大陆的魔法体系设定", "给北境王国加一条世界书"],
    },
    "script_work": {
        "description": "设计剧本、大纲、分镜、台词或情节骨架",
        "assetTargets": [".storydex/scripts/"],
        "examples": ["帮我列一份第二卷的大纲", "把这场冲突写成剧本"],
    },
    "wiki_work": {
        "description": "整理或同步项目 WIKI / 知识图谱（实体、关系、伏笔、设定关系）",
        "assetTargets": [".storydex/wiki/"],
        "examples": ["整理一下知识图谱", "把最近几章的设定同步到 WIKI"],
    },
    "project_organization": {
        "description": "整理项目目录或文件结构",
        "assetTargets": [".storydex/", "chapters/"],
        "examples": ["整理一下项目目录"],
    },
    "general": {
        "description": "提问、闲聊、反馈、软件使用问题或其他不属于以上类别的请求",
        "assetTargets": [],
        "examples": ["这个软件怎么导出章节", "你觉得这段写得怎么样"],
    },
}

# 启发式兜底正则（LLM 不可用时使用；仅覆盖内置标签）。
_STORY_INTENT_RE = re.compile(
    r"(续写|写(一|1)?段|写第|生成.*(剧情|故事|章节|片段)|创作.*(剧情|故事)|正文|剧情|章节|片段|story|chapter|scene|continue)",
    re.IGNORECASE,
)
_CHARACTER_INTENT_RE = re.compile(r"(角色|人物|character|cast)", re.IGNORECASE)
_WORLDBOOK_INTENT_RE = re.compile(r"(世界书|世界观|设定集|worldbook|lorebook|lore)", re.IGNORECASE)
_SCRIPT_INTENT_RE = re.compile(r"(剧本|分镜|台词|大纲|screenplay|script)", re.IGNORECASE)
_WIKI_INTENT_RE = re.compile(r"(wiki|知识图谱|知识库|整理设定|整理关系)", re.IGNORECASE)
_PROJECT_ORGANIZE_RE = re.compile(r"(整理目录|项目目录|整理项目|organize)", re.IGNORECASE)


def build_intent_catalog(
    *,
    workspace_root: Path | None = None,
    story_project_service: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """内置目录 + 项目 skill registry 合并出的意图目录。

    registry 中每个技能按其声明的 intent 归入对应条目（技能名进 skills、
    assetTargets 合并去重）；声明了未知 intent 的自定义技能会新增一个
    可选标签，使按项目扩展的技能也能被路由到。
    """
    catalog: Dict[str, Dict[str, Any]] = {
        label: {
            "description": str(entry.get("description") or ""),
            "assetTargets": list(entry.get("assetTargets") or []),
            "skills": [],
            "examples": list(entry.get("examples") or []),
        }
        for label, entry in _BUILTIN_INTENT_CATALOG.items()
    }
    if workspace_root is None:
        return catalog
    try:
        if story_project_service is None:
            from services.story_project_service import get_story_project_service

            story_project_service = get_story_project_service()
        registry = story_project_service.read_agent_skill_registry(Path(workspace_root))
    except Exception:
        return catalog
    skills = registry.get("skills") if isinstance(registry, dict) and isinstance(registry.get("skills"), list) else []
    for item in skills:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "").strip()
        name = str(item.get("name") or item.get("id") or "").strip()
        if not intent or not _INTENT_SLUG_RE.match(intent):
            continue
        entry = catalog.setdefault(
            intent,
            {"description": f"项目自定义技能意图（{name}）", "assetTargets": [], "skills": [], "examples": []},
        )
        if name and name not in entry["skills"]:
            entry["skills"].append(name)
        targets = item.get("assetTargets") if isinstance(item.get("assetTargets"), list) else []
        for target in targets:
            normalized = str(target or "").strip()
            if normalized and normalized not in entry["assetTargets"]:
                entry["assetTargets"].append(normalized)
    return catalog


def heuristic_intent_frame(*, prompt: str, active_file: str) -> Dict[str, Any]:
    """关键词启发式分类，作为 LLM 不可用时的兜底路径。"""
    text = f"{prompt}\n{active_file}"
    signals: List[str] = []
    primary = "general"
    if _STORY_INTENT_RE.search(text):
        primary = "story_generation"
        signals.append("story_keywords")
    elif _CHARACTER_INTENT_RE.search(text):
        primary = "character_work"
        signals.append("character_keywords")
    elif _WORLDBOOK_INTENT_RE.search(text):
        primary = "worldbook_work"
        signals.append("worldbook_keywords")
    elif _SCRIPT_INTENT_RE.search(text):
        primary = "script_work"
        signals.append("script_keywords")
    elif _WIKI_INTENT_RE.search(text):
        primary = "wiki_work"
        signals.append("wiki_keywords")
    elif _PROJECT_ORGANIZE_RE.search(text):
        primary = "project_organization"
        signals.append("project_organization_keywords")
    if active_file.startswith("chapters/") and primary == "general":
        primary = "story_generation"
        signals.append("active_chapter_file")
    return {
        "primary": primary,
        "confidence": "medium" if signals else "low",
        "signals": signals,
        "method": "heuristic",
    }


def is_valid_intent_frame(frame: Any) -> bool:
    """校验分类管线产出的意图帧：primary 为合法 slug 且带 method 出处标记。"""
    if not isinstance(frame, dict):
        return False
    primary = str(frame.get("primary") or "")
    if not _INTENT_SLUG_RE.match(primary):
        return False
    return bool(str(frame.get("method") or ""))


def _extract_json_object(content: str) -> Any:
    text = str(content or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", text).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_intent_frame(content: str, *, valid_labels: set[str]) -> Dict[str, Any] | None:
    payload = _extract_json_object(content)
    if not isinstance(payload, dict):
        return None
    primary = str(payload.get("primary") or "").strip()
    if primary not in valid_labels:
        return None
    secondary = str(payload.get("secondary") or "").strip()
    if secondary not in valid_labels or secondary == primary:
        secondary = ""
    confidence = str(payload.get("confidence") or "").strip().lower()
    if confidence not in _CONFIDENCE_LEVELS:
        confidence = "medium"
    reason = str(payload.get("reason") or "").strip()
    frame = {
        "primary": primary,
        "confidence": confidence,
        "signals": ["llm_classifier"],
        "method": "llm",
        "reason": reason[:200],
    }
    if secondary:
        frame["secondary"] = secondary
    return frame


def _catalog_prompt_lines(catalog: Dict[str, Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for label, entry in catalog.items():
        targets = ", ".join(str(t) for t in entry.get("assetTargets") or []) or "(no fixed output path)"
        skills = ", ".join(str(s) for s in entry.get("skills") or [])
        examples = " / ".join(f'"{e}"' for e in (entry.get("examples") or [])[:3])
        line = f"- {label}: {entry.get('description') or label}. Outputs go under: {targets}."
        if skills:
            line += f" Project skills: {skills}."
        if examples:
            line += f" e.g. {examples}"
        lines.append(line)
    return lines


def _intent_messages(
    *,
    prompt: str,
    active_file: str,
    catalog: Dict[str, Dict[str, Any]],
    previous_turn: Dict[str, str] | None,
) -> list[Dict[str, Any]]:
    system_prompt = (
        "You are Storydex's intent router for a fiction-writing workspace. "
        "Classify the user's request into exactly one primary intent label from this project's catalog:\n"
        + "\n".join(_catalog_prompt_lines(catalog))
        + "\n\nRules:\n"
        "- The user usually writes Chinese; requests may be indirect or elliptical.\n"
        "- Short continuations like 「继续」「然后呢」「再来一段」 normally keep previousTurn's intent "
        "unless the topic clearly changed.\n"
        "- Use activeFile as context (an open chapters/ file suggests story continuation), not as an override.\n"
        "- If the request mixes two intents, pick the one the user wants executed now as primary "
        "and put the other in secondary.\n"
        "Return ONLY a JSON object: "
        '{"primary": "<label>", "secondary": "<label or empty string>", '
        '"confidence": "high"|"medium"|"low", "reason": "<short sentence>"}. '
        "No markdown, no extra keys, no chain-of-thought."
    )
    request: Dict[str, Any] = {
        "prompt": str(prompt or "")[:_MAX_PROMPT_CHARS],
        "activeFile": str(active_file or ""),
        "activeFileIsChapter": str(active_file or "").startswith("chapters/"),
        "previousTurn": previous_turn or None,
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]


def _enrich_frame(frame: Dict[str, Any], catalog: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    entry = catalog.get(str(frame.get("primary") or "")) or {}
    frame["assetTargets"] = list(entry.get("assetTargets") or [])
    frame["matchedSkills"] = list(entry.get("skills") or [])
    return frame


class StorydexIntentService:
    def __init__(
        self,
        *,
        llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        story_project_service: Any = None,
    ) -> None:
        self.llm_timeout_seconds = llm_timeout_seconds
        self._story_project_service = story_project_service
        self._session_turns: OrderedDict[str, Dict[str, str]] = OrderedDict()

    async def classify_intent(
        self,
        *,
        prompt: str,
        active_file: str = "",
        workspace_root: Path | None = None,
        session_id: str = "",
    ) -> Dict[str, Any]:
        normalized_prompt = str(prompt or "").strip()
        catalog = self._catalog(workspace_root)
        previous_turn = self._session_turns.get(session_id) if session_id else None
        # 确定性短路：slash 命令与空输入不需要语义分类。
        if not normalized_prompt or normalized_prompt.startswith("/"):
            frame = heuristic_intent_frame(prompt=normalized_prompt, active_file=active_file)
            frame["method"] = "deterministic"
        else:
            frame = await self._llm_intent_frame(
                prompt=normalized_prompt,
                active_file=active_file,
                catalog=catalog,
                previous_turn=previous_turn,
            )
            if frame is None:
                frame = heuristic_intent_frame(prompt=normalized_prompt, active_file=active_file)
                frame["method"] = "heuristic_fallback"
        _enrich_frame(frame, catalog)
        self._remember(session_id=session_id, prompt=normalized_prompt, primary=str(frame.get("primary") or ""))
        return frame

    def _catalog(self, workspace_root: Path | None) -> Dict[str, Dict[str, Any]]:
        try:
            return build_intent_catalog(
                workspace_root=workspace_root,
                story_project_service=self._story_project_service,
            )
        except Exception:
            return build_intent_catalog()

    def _remember(self, *, session_id: str, prompt: str, primary: str) -> None:
        if not session_id or not prompt or not primary:
            return
        self._session_turns[session_id] = {"prompt": prompt[:200], "intent": primary}
        self._session_turns.move_to_end(session_id)
        while len(self._session_turns) > _MAX_SESSION_MEMORY:
            self._session_turns.popitem(last=False)

    async def _llm_intent_frame(
        self,
        *,
        prompt: str,
        active_file: str,
        catalog: Dict[str, Dict[str, Any]],
        previous_turn: Dict[str, str] | None,
    ) -> Dict[str, Any] | None:
        try:
            from services.coomi_agent_service import _call_provider_chat, _storydex_coomi_home

            with _storydex_coomi_home():
                from coomi.services import get_llm_provider

                provider = get_llm_provider()
                response = await asyncio.wait_for(
                    _call_provider_chat(
                        provider,
                        _intent_messages(
                            prompt=prompt,
                            active_file=active_file,
                            catalog=catalog,
                            previous_turn=previous_turn,
                        ),
                        None,
                    ),
                    timeout=self.llm_timeout_seconds,
                )
        except Exception:
            return None
        return _parse_intent_frame(
            str(getattr(response, "content", "") or ""),
            valid_labels=set(catalog),
        )


_SERVICE = StorydexIntentService()


def get_storydex_intent_service() -> StorydexIntentService:
    return _SERVICE
