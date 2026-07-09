"""Storydex 意图识别服务的回归测试。

覆盖：
1. LLM 结构化分类正常路径（JSON 解析、标签校验、置信度归一）。
2. LLM 异常 / 超时 / 非法输出时回退关键词启发式。
3. 确定性短路（slash 命令、空输入）。
4. orchestration 注入 intent_frame 与无效注入的兜底。
5. 项目语义接地：意图目录合并 skill registry、assetTargets/matchedSkills
   富化、自定义 intent 标签、会话上一轮记忆。
"""
from __future__ import annotations

import asyncio
import json
import sys
import types
from contextlib import contextmanager

from services.storydex_intent_service import (
    StorydexIntentService,
    _parse_intent_frame,
    build_intent_catalog,
    heuristic_intent_frame,
)
from services.story_project_service import get_story_project_service
from services.storydex_orchestration_service import get_storydex_orchestration_service


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


def _install_fake_provider(monkeypatch, provider) -> None:
    fake_services = types.ModuleType("coomi.services")
    fake_services.get_llm_provider = lambda: provider
    monkeypatch.setitem(sys.modules, "coomi", types.ModuleType("coomi"))
    monkeypatch.setitem(sys.modules, "coomi.services", fake_services)

    @contextmanager
    def fake_home():
        yield

    import services.coomi_agent_service as coomi_agent_service

    monkeypatch.setattr(coomi_agent_service, "_storydex_coomi_home", fake_home)


# ─────────────────── 1. LLM 正常路径 ───────────────────


def test_classify_intent_uses_llm_structured_output(monkeypatch):
    class FakeProvider:
        async def chat(self, messages, options):
            assert options is None
            assert messages[0]["role"] == "system"
            assert "worldbook_work" in messages[0]["content"]
            return _FakeResponse('{"primary": "worldbook_work", "confidence": "high", "reason": "设计世界观条目"}')

    _install_fake_provider(monkeypatch, FakeProvider())
    service = StorydexIntentService()
    frame = asyncio.run(service.classify_intent(prompt="帮我完善一下大陆的魔法体系设定", active_file=""))
    assert frame["primary"] == "worldbook_work"
    assert frame["confidence"] == "high"
    assert frame["method"] == "llm"
    assert frame["signals"] == ["llm_classifier"]


def test_parse_intent_frame_accepts_fenced_json_and_normalizes_confidence():
    labels = set(build_intent_catalog())
    frame = _parse_intent_frame(
        '```json\n{"primary": "story_generation", "confidence": "certain"}\n```',
        valid_labels=labels,
    )
    assert frame is not None
    assert frame["primary"] == "story_generation"
    assert frame["confidence"] == "medium"


def test_parse_intent_frame_rejects_unknown_label_and_bad_json():
    labels = set(build_intent_catalog())
    assert _parse_intent_frame('{"primary": "hack_the_planet", "confidence": "high"}', valid_labels=labels) is None
    assert _parse_intent_frame("not json at all", valid_labels=labels) is None
    assert _parse_intent_frame("", valid_labels=labels) is None


# ─────────────────── 2. 兜底路径 ───────────────────


def test_classify_intent_falls_back_when_provider_raises(monkeypatch):
    class BrokenProvider:
        async def chat(self, messages, options):
            raise RuntimeError("provider offline")

    _install_fake_provider(monkeypatch, BrokenProvider())
    service = StorydexIntentService()
    frame = asyncio.run(service.classify_intent(prompt="帮我整理知识图谱", active_file=""))
    assert frame["primary"] == "wiki_work"
    assert frame["method"] == "heuristic_fallback"


def test_classify_intent_falls_back_on_timeout(monkeypatch):
    class SlowProvider:
        async def chat(self, messages, options):
            await asyncio.sleep(0.2)
            return _FakeResponse('{"primary": "wiki_work", "confidence": "high"}')

    _install_fake_provider(monkeypatch, SlowProvider())
    service = StorydexIntentService(llm_timeout_seconds=0.01)
    frame = asyncio.run(service.classify_intent(prompt="续写一段剧情", active_file=""))
    assert frame["primary"] == "story_generation"
    assert frame["method"] == "heuristic_fallback"


def test_classify_intent_falls_back_on_invalid_llm_output(monkeypatch):
    class NoisyProvider:
        async def chat(self, messages, options):
            return _FakeResponse("好的，我认为这是一个角色设计请求。")

    _install_fake_provider(monkeypatch, NoisyProvider())
    service = StorydexIntentService()
    frame = asyncio.run(service.classify_intent(prompt="设计一个反派角色", active_file=""))
    assert frame["primary"] == "character_work"
    assert frame["method"] == "heuristic_fallback"


# ─────────────────── 3. 确定性短路 ───────────────────


def test_classify_intent_short_circuits_slash_commands_and_empty_prompt():
    service = StorydexIntentService()
    frame = asyncio.run(service.classify_intent(prompt="/plan 下一章", active_file=""))
    assert frame["method"] == "deterministic"
    empty = asyncio.run(service.classify_intent(prompt="   ", active_file="chapters/第一章/001.md"))
    assert empty["method"] == "deterministic"
    assert empty["primary"] == "story_generation"


def test_heuristic_frame_covers_worldbook_and_script_intents():
    assert heuristic_intent_frame(prompt="更新世界书里的王国设定", active_file="")["primary"] == "worldbook_work"
    assert heuristic_intent_frame(prompt="设计一份剧本大纲", active_file="")["primary"] == "script_work"


# ─────────────────── 4. orchestration 注入 ───────────────────


def test_build_turn_contract_uses_injected_intent_frame(tmp_path):
    orchestration = get_storydex_orchestration_service()
    contract = orchestration.build_turn_contract(
        tmp_path,
        prompt="随便聊聊",
        intent_frame={
            "primary": "character_work",
            "confidence": "high",
            "signals": ["llm_classifier"],
            "method": "llm",
        },
    )
    intent = contract["intentFrame"]
    assert intent["primary"] == "character_work"
    assert intent["confidence"] == "high"
    assert intent["existingChapterCount"] == 0


def test_build_turn_contract_ignores_invalid_injected_frame(tmp_path):
    orchestration = get_storydex_orchestration_service()
    contract = orchestration.build_turn_contract(
        tmp_path,
        prompt="续写一段剧情",
        intent_frame={"primary": "not_a_label"},
    )
    intent = contract["intentFrame"]
    assert intent["primary"] == "story_generation"
    assert "story_keywords" in intent["signals"]


def test_build_turn_contract_without_frame_keeps_heuristic_behavior(tmp_path):
    orchestration = get_storydex_orchestration_service()
    contract = orchestration.build_turn_contract(tmp_path, prompt="继续写一段剧情")
    assert contract["intentFrame"]["primary"] == "story_generation"


# ─────────────────── 5. 项目语义接地 ───────────────────


def test_intent_catalog_merges_default_skill_registry(tmp_path):
    catalog = build_intent_catalog(workspace_root=tmp_path)
    character = catalog["character_work"]
    assert ".storydex/characters/" in character["assetTargets"]
    assert "设计角色" in character["skills"]
    assert "角色更新" in character["skills"]
    wiki = catalog["wiki_work"]
    assert ".storydex/wiki/" in wiki["assetTargets"]
    assert "WIKI整理" in wiki["skills"]


def test_classify_intent_enriches_frame_with_asset_targets_and_skills(monkeypatch, tmp_path):
    class FakeProvider:
        async def chat(self, messages, options):
            assert ".storydex/characters/" in messages[0]["content"]
            return _FakeResponse('{"primary": "character_work", "confidence": "high", "reason": "角色设定"}')

    _install_fake_provider(monkeypatch, FakeProvider())
    service = StorydexIntentService()
    frame = asyncio.run(
        service.classify_intent(prompt="设计一个反派角色", active_file="", workspace_root=tmp_path)
    )
    assert frame["primary"] == "character_work"
    assert frame["assetTargets"] == [".storydex/characters/"]
    assert "设计角色" in frame["matchedSkills"]


def test_classify_intent_accepts_custom_registry_intent(monkeypatch, tmp_path):
    registry_path = get_story_project_service().agent_root(tmp_path) / "skills" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": [
                    {"id": "poetry", "name": "写诗", "intent": "poetry_work", "assetTargets": [".storydex/poetry/"]}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeProvider:
        async def chat(self, messages, options):
            assert "poetry_work" in messages[0]["content"]
            return _FakeResponse('{"primary": "poetry_work", "confidence": "high", "reason": "写诗请求"}')

    _install_fake_provider(monkeypatch, FakeProvider())
    service = StorydexIntentService()
    frame = asyncio.run(service.classify_intent(prompt="给男主写一首出场诗", active_file="", workspace_root=tmp_path))
    assert frame["primary"] == "poetry_work"
    assert frame["assetTargets"] == [".storydex/poetry/"]
    assert frame["matchedSkills"] == ["写诗"]

    orchestration = get_storydex_orchestration_service()
    contract = orchestration.build_turn_contract(tmp_path, prompt="给男主写一首出场诗", intent_frame=frame)
    assert contract["intentFrame"]["primary"] == "poetry_work"


def test_classify_intent_passes_previous_turn_context(monkeypatch):
    seen_requests = []

    class FakeProvider:
        async def chat(self, messages, options):
            seen_requests.append(json.loads(messages[1]["content"]))
            if len(seen_requests) == 1:
                return _FakeResponse('{"primary": "character_work", "confidence": "high"}')
            return _FakeResponse('{"primary": "character_work", "confidence": "medium", "reason": "延续上一轮"}')

    _install_fake_provider(monkeypatch, FakeProvider())
    service = StorydexIntentService()
    asyncio.run(service.classify_intent(prompt="设计一个新角色", session_id="s1"))
    frame = asyncio.run(service.classify_intent(prompt="继续", session_id="s1"))
    assert seen_requests[0]["previousTurn"] is None
    assert seen_requests[1]["previousTurn"] == {"prompt": "设计一个新角色", "intent": "character_work"}
    assert frame["primary"] == "character_work"
