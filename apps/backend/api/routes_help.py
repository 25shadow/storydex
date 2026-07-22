from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from api.response import ApiEnvelope, ApiTrace, success_response
from services.help_guide_service import get_help_guide_service
from services.prompt_repository_service import get_prompt_repository_service

router = APIRouter(tags=["help"])


class PromptCreateRequest(BaseModel):
    title: str
    category: str
    summary: str = ""
    prompt_text: str = Field(alias="promptText")
    file_name: str = Field(default="", alias="fileName")

    model_config = ConfigDict(populate_by_name=True)


def _trace(started: float, trace_id: str) -> ApiTrace:
    return ApiTrace(traceId=trace_id, durationMs=int((perf_counter() - started) * 1000))


@router.get("/help/guide", response_model=ApiEnvelope)
def read_help_guide() -> ApiEnvelope:
    started = perf_counter()
    trace_id = str(uuid4())
    data = get_help_guide_service().read_guide()
    return success_response(
        data=data,
        trace=_trace(started, trace_id),
        audit=[{"action": "read_help_guide", "count": len(data.get("items") or [])}],
    )


@router.get("/help/guide/search", response_model=ApiEnvelope)
def search_help_guide(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=6, ge=1, le=20),
) -> ApiEnvelope:
    started = perf_counter()
    trace_id = str(uuid4())
    data = get_help_guide_service().search(q, max_results=limit)
    return success_response(
        data=data,
        trace=_trace(started, trace_id),
        audit=[{"action": "search_help_guide", "query": q, "count": len(data.get("items") or [])}],
    )


@router.get("/help/prompts", response_model=ApiEnvelope)
def read_prompt_repository(
    q: str = Query(default="", max_length=200),
    category: str = Query(default="", max_length=80),
) -> ApiEnvelope:
    started = perf_counter()
    trace_id = str(uuid4())
    data = get_prompt_repository_service().read_repository(query=q, category=category)
    return success_response(
        data=data,
        trace=_trace(started, trace_id),
        audit=[
            {
                "action": "read_prompt_repository",
                "query": q,
                "category": category,
                "count": len(data.get("items") or []),
            }
        ],
    )


@router.post("/help/prompts", response_model=ApiEnvelope)
def create_prompt_repository_item(payload: PromptCreateRequest) -> ApiEnvelope:
    started = perf_counter()
    trace_id = str(uuid4())
    item = get_prompt_repository_service().create_prompt(
        title=payload.title,
        category=payload.category,
        summary=payload.summary,
        prompt_text=payload.prompt_text,
        file_name=payload.file_name,
    )
    return success_response(
        data=item,
        trace=_trace(started, trace_id),
        audit=[{"action": "create_prompt_repository_item", "id": item.get("id", "")}],
    )
