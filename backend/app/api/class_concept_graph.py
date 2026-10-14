"""Authorized class knowledge-graph and source extraction operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import (
    ConceptAliasCreate,
    ConceptCreate,
    ConceptDecision,
    ConceptEdgeCreate,
    ConceptExtractionRequest,
    ConceptMerge,
    ConceptPatch,
)
from app.security.rate_limit import InMemoryRateLimiter
from app.services.class_access import class_access, is_class_manager
from app.services.concept_graph import (
    add_concept_alias,
    concept_extraction_status,
    concept_graph,
    concept_merge_history,
    create_concept,
    create_edge,
    decide_alias_proposal,
    decide_concept,
    decide_edge,
    delete_concept_alias,
    delete_edge,
    enqueue_concept_extraction,
    merge_concept,
    process_queued_concept_extractions,
    update_concept,
)

router = APIRouter(tags=["concept-graph"])


async def _user(request: Request) -> tuple[UUID, object]:
    user_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return user_id, database(request)


async def _extraction_rate_limit(request: Request, user_id: UUID) -> None:
    limiter: InMemoryRateLimiter = request.app.state.concept_graph_rate_limiter
    retry_after = await limiter.check(str(user_id))
    if retry_after is not None:
        raise ApiError(
            429,
            "concept_extraction_rate_limited",
            "Too many extraction requests. Please wait before trying again.",
            retry_after_seconds=retry_after,
        )


@router.get("/api/classes/{class_id}/concept-graph")
async def get_concept_graph(class_id: UUID, request: Request, response: Response) -> dict:
    user_id, db = await _user(request)
    response.headers["Cache-Control"] = "private, no-store"
    return await concept_graph(db, class_id=str(class_id), user_id=str(user_id))


@router.get("/api/classes/{class_id}/concept-graph/review")
async def get_concept_graph_review(class_id: UUID, request: Request, response: Response) -> dict:
    user_id, db = await _user(request)
    response.headers["Cache-Control"] = "private, no-store"
    return await concept_graph(db, class_id=str(class_id), user_id=str(user_id), review=True)


@router.get("/api/classes/{class_id}/concept-graph/merges")
async def get_concept_merge_history(
    class_id: UUID,
    request: Request,
    response: Response,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict:
    user_id, db = await _user(request)
    response.headers["Cache-Control"] = "private, no-store"
    return await concept_merge_history(
        db, class_id=str(class_id), user_id=str(user_id), limit=limit
    )


@router.post("/api/classes/{class_id}/concept-graph/extractions", status_code=202)
async def request_concept_extraction(
    class_id: UUID,
    payload: ConceptExtractionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    user_id, db = await _user(request)
    access = await class_access(db, str(class_id), str(user_id), required="read")
    if not is_class_manager(access):
        raise ApiError(
            403, "insufficient_permission", "A class manager must request concept extraction."
        )
    await _extraction_rate_limit(request, user_id)
    if getattr(request.app.state, "gemini_content_service", None) is None:
        raise ApiError(
            503, "provider_unavailable", "Concept extraction is temporarily unavailable."
        )
    result = await enqueue_concept_extraction(
        db, class_id=str(class_id), user_id=str(user_id), upload_id=str(payload.upload_id)
    )
    if result["status"] == "queued":
        background_tasks.add_task(process_queued_concept_extractions, request.app, limit=1)
    return result


@router.get("/api/classes/{class_id}/concept-graph/extractions/{operation_id}")
async def get_concept_extraction_status(
    class_id: UUID, operation_id: str, request: Request, response: Response
) -> dict:
    user_id, db = await _user(request)
    response.headers["Cache-Control"] = "private, no-store"
    return await concept_extraction_status(
        db, class_id=str(class_id), user_id=str(user_id), operation_id=operation_id
    )


@router.post("/api/classes/{class_id}/concepts", status_code=201)
async def create_class_concept(class_id: UUID, payload: ConceptCreate, request: Request) -> dict:
    user_id, db = await _user(request)
    return await create_concept(db, class_id=str(class_id), user_id=str(user_id), payload=payload)


@router.patch("/api/classes/{class_id}/concepts/{concept_id}")
async def patch_class_concept(
    class_id: UUID, concept_id: UUID, payload: ConceptPatch, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await update_concept(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        payload=payload,
    )


@router.post("/api/classes/{class_id}/concepts/{concept_id}/aliases", status_code=201)
async def create_class_concept_alias(
    class_id: UUID, concept_id: UUID, payload: ConceptAliasCreate, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await add_concept_alias(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        name=payload.name,
        language=payload.language,
    )


@router.delete("/api/classes/{class_id}/concepts/{concept_id}/aliases/{alias_id}")
async def remove_class_concept_alias(
    class_id: UUID,
    concept_id: UUID,
    alias_id: UUID,
    request: Request,
    expected_version: int = Query(ge=1),
) -> dict:
    user_id, db = await _user(request)
    return await delete_concept_alias(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        alias_id=str(alias_id),
        expected_version=expected_version,
    )


@router.post("/api/classes/{class_id}/concepts/{concept_id}/aliases/{alias_id}/decision")
async def decide_class_concept_alias(
    class_id: UUID, concept_id: UUID, alias_id: UUID, payload: ConceptDecision, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await decide_alias_proposal(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        alias_id=str(alias_id),
        decision=payload.decision,
        expected_version=payload.expected_version,
    )


@router.post("/api/classes/{class_id}/concepts/{concept_id}/merge")
async def merge_class_concept(
    class_id: UUID, concept_id: UUID, payload: ConceptMerge, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await merge_concept(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        payload=payload,
    )


@router.post("/api/classes/{class_id}/concepts/{concept_id}/decision")
async def decide_class_concept(
    class_id: UUID, concept_id: UUID, payload: ConceptDecision, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await decide_concept(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        concept_id=str(concept_id),
        decision=payload.decision,
        expected_version=payload.expected_version,
    )


@router.post("/api/classes/{class_id}/concept-edges", status_code=201)
async def create_class_concept_edge(
    class_id: UUID, payload: ConceptEdgeCreate, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await create_edge(db, class_id=str(class_id), user_id=str(user_id), payload=payload)


@router.post("/api/classes/{class_id}/concept-edges/{edge_id}/decision")
async def decide_class_concept_edge(
    class_id: UUID, edge_id: UUID, payload: ConceptDecision, request: Request
) -> dict:
    user_id, db = await _user(request)
    return await decide_edge(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        edge_id=str(edge_id),
        decision=payload.decision,
        expected_version=payload.expected_version,
    )


@router.delete("/api/classes/{class_id}/concept-edges/{edge_id}", status_code=204)
async def remove_class_concept_edge(
    class_id: UUID,
    edge_id: UUID,
    request: Request,
    expected_version: int = Query(ge=1),
) -> Response:
    user_id, db = await _user(request)
    await delete_edge(
        db,
        class_id=str(class_id),
        user_id=str(user_id),
        edge_id=str(edge_id),
        expected_version=expected_version,
    )
    return Response(status_code=204)
