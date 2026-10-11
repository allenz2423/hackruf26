"""Cookie-authenticated access to the student's saved tutoring recaps."""

from uuid import UUID

from fastapi import APIRouter, Request

from app.api.dependencies import enforce_sqlite_api_policy
from app.api.schemas import TutorMemoryPatch, TutorMemoryState

router = APIRouter(prefix="/api/tutor-memory", tags=["tutor-memory"])


@router.get("", response_model=TutorMemoryState)
async def get_memory(request: Request, class_id: UUID | None = None) -> TutorMemoryState:
    owner = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return await request.app.state.tutor_memory_service.state(owner, class_id)


@router.patch("", response_model=TutorMemoryState)
async def update_memory(request: Request, payload: TutorMemoryPatch) -> TutorMemoryState:
    owner = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return await request.app.state.tutor_memory_service.set_enabled(owner, payload.class_id, payload.enabled)


@router.delete("", response_model=TutorMemoryState)
async def clear_memory(request: Request, class_id: UUID | None = None) -> TutorMemoryState:
    owner = await enforce_sqlite_api_policy(request, request.app.state.settings)
    return await request.app.state.tutor_memory_service.clear(owner, class_id)
