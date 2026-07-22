from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from core.config import get_settings
from core.exceptions import StorydexError


_PROMPT_BLOCK_RE = re.compile(r"```(?:prompt|text)\s*\n(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
_PLACEHOLDER_RE = re.compile(r"\[[^\]\r\n]{1,40}\]")


class PromptRepositoryService:
    DIRECTORY_NAME = "prompts"

    def read_repository(self, *, query: str = "", category: str = "") -> Dict[str, Any]:
        roots = self._resolve_roots()
        all_items = self._read_items(roots)
        normalized_query = str(query or "").strip().lower()
        normalized_category = str(category or "").strip()

        items = [
            item
            for item in all_items
            if (not normalized_category or str(item.get("category") or "") == normalized_category)
            and (
                not normalized_query
                or normalized_query
                in "\n".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("summary") or ""),
                        str(item.get("category") or ""),
                        str(item.get("content") or ""),
                    ]
                ).lower()
            )
        ]

        category_counts: Dict[str, int] = {}
        for item in all_items:
            name = str(item.get("category") or "通用")
            category_counts[name] = category_counts.get(name, 0) + 1

        categories = [
            {"id": name, "label": name, "count": count}
            for name, count in sorted(category_counts.items(), key=lambda entry: entry[0])
        ]
        return {
            "root": roots[-1].as_posix() if roots else "",
            "query": str(query or "").strip(),
            "category": normalized_category,
            "categories": categories,
            "items": items,
        }

    def create_prompt(
        self,
        *,
        title: str,
        category: str,
        summary: str,
        prompt_text: str,
        file_name: str = "",
    ) -> Dict[str, Any]:
        title = str(title or "").strip()
        category = str(category or "").strip()
        summary = str(summary or "").strip()
        prompt_text = str(prompt_text or "").strip()
        if not title or not category or not prompt_text:
            raise StorydexError(
                "Prompt title, category and body are required.",
                code="prompt_fields_required",
                status_code=400,
            )
        if len(title) > 120 or len(category) > 80 or len(summary) > 500 or len(prompt_text) > 100_000:
            raise StorydexError(
                "Prompt fields exceed the allowed length.",
                code="prompt_fields_too_long",
                status_code=400,
            )

        safe_category = self._safe_segment(category)
        safe_name = self._safe_segment(file_name or title)
        target_root = self._resolve_user_root()
        target = target_root / safe_category / f"{safe_name}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise StorydexError(
                "A prompt with this filename already exists.",
                code="prompt_already_exists",
                status_code=409,
            )
        content = f"# {title}\n\n> {summary}\n\n## 可直接使用的指令\n\n```prompt\n{prompt_text}\n```\n"
        target.write_text(content, encoding="utf-8")
        item = self._read_item(target, target_root)
        return item

    def _resolve_roots(self) -> List[Path]:
        configured_raw = os.environ.get("STORYDEX_PROMPT_REPOSITORY_ROOT", "").strip()
        configured = Path(configured_raw).expanduser() if configured_raw else None
        if configured is not None:
            roots = [configured.resolve()]
            return [root for root in roots if root.exists() and root.is_dir()]

        builtin = self._resolve_builtin_root()
        user = self._resolve_user_root()
        return [root for root in (builtin, user) if root is not None and root.exists() and root.is_dir()]

    def _resolve_builtin_root(self) -> Path | None:
        current = Path(__file__).resolve()
        for parent in current.parents:
            candidate = parent / "docs" / self.DIRECTORY_NAME
            if candidate.exists() and candidate.is_dir():
                return candidate.resolve()
        return None

    def _resolve_user_root(self) -> Path:
        configured_raw = os.environ.get("STORYDEX_PROMPT_REPOSITORY_ROOT", "").strip()
        if configured_raw:
            return Path(configured_raw).expanduser().resolve()
        return (get_settings().global_root / self.DIRECTORY_NAME).resolve()

    @staticmethod
    def _safe_segment(value: str) -> str:
        normalized = re.sub(r"[\\/:*?\"<>|\r\n]+", "-", str(value or "").strip())
        normalized = re.sub(r"\s+", "-", normalized).strip(" .-")
        return normalized[:100] or "untitled"

    def _read_items(self, roots: List[Path]) -> List[Dict[str, Any]]:
        by_relative: Dict[str, Dict[str, Any]] = {}
        for root in roots:
            for path in sorted(root.rglob("*.md"), key=lambda item: item.relative_to(root).as_posix().lower()):
                if not path.is_file() or path.name.lower() == "readme.md":
                    continue
                relative = path.relative_to(root).as_posix()
                by_relative[relative] = self._read_item(path, root)
        return sorted(by_relative.values(), key=lambda item: str(item.get("relativePath") or "").lower())

    def _read_item(self, path: Path, root: Path) -> Dict[str, Any]:
        content = path.read_text(encoding="utf-8")
        relative = path.relative_to(root)
        prompt_text = self._extract_prompt_text(content)
        category = relative.parts[0] if len(relative.parts) > 1 else "通用"
        return {
            "id": relative.with_suffix("").as_posix(),
            "title": self._extract_title(content, path.stem),
            "summary": self._extract_summary(content),
            "category": category,
            "relativePath": relative.as_posix(),
            "content": content,
            "promptText": prompt_text,
            "placeholders": self._extract_placeholders(prompt_text),
            "updatedAt": self._mtime_iso(path),
        }

    @staticmethod
    def _extract_title(content: str, fallback: str) -> str:
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if line.startswith("# "):
                return line[2:].strip() or fallback
        return fallback

    @staticmethod
    def _extract_summary(content: str) -> str:
        for raw_line in str(content or "").splitlines():
            line = raw_line.strip()
            if line.startswith(">"):
                return line.lstrip(">").strip()
        return ""

    @staticmethod
    def _extract_prompt_text(content: str) -> str:
        match = _PROMPT_BLOCK_RE.search(str(content or ""))
        return (match.group("body") if match else str(content or "")).strip()

    @staticmethod
    def _extract_placeholders(prompt_text: str) -> List[str]:
        placeholders: List[str] = []
        for match in _PLACEHOLDER_RE.finditer(str(prompt_text or "")):
            value = match.group(0)
            if value not in placeholders:
                placeholders.append(value)
        return placeholders

    @staticmethod
    def _mtime_iso(path: Path) -> str:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            return ""


@lru_cache(maxsize=1)
def get_prompt_repository_service() -> PromptRepositoryService:
    return PromptRepositoryService()
