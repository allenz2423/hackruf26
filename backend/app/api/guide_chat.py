"""Owner-authorized persistent guide chat and private guide-check progress."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import ValidationError

from app.api.dependencies import database, enforce_sqlite_api_policy
from app.api.errors import ApiError
from app.api.schemas import (
    GuideChatEditPreview,
    GuideChatEditStateRequest,
    GuideChatEditStateResponse,
    GuideChatMessage,
    GuideChatReference,
    GuideChatRequest,
    GuideChatResponse,
    GuideChatSectionEdit,
    GuideMiniQuizAttemptRequest,
    GuideMiniQuizAttemptResponse,
    GuidePracticeQuestion,
    GuideProgressResponse,
    GuideSectionContent,
    GuideSectionProgress,
    SavedGuideChatMessage,
    SavedGuideChatResponse,
    StudyGuideContent,
    StudyGuideSectionV2,
)
from app.services.ai_usage_ledger import bind_usage_context, reset_usage_context
from app.services.class_access import (
    can_view_unreleased,
    class_access,
    is_effectively_published,
    material_access,
)
from app.services.database import json_value
from app.services.guide_chat import GUIDE_CHAT_MAX_CONTEXT_CHARS
from app.services.guide_editing import guide_section_fingerprint
from app.services.guide_templates import guide_block_alt_text, guide_block_fallback
from app.services.source_index import fetch_authorized_chunk, search_authorized_sources

logger = logging.getLogger("screen_tutor.guide_chat_api")
router = APIRouter(tags=["study-guide-chat"])
MAX_SOURCE_FILES = 6
MAX_SOURCE_CHUNKS = 8
MAX_SOURCE_CHUNK_CHARS = 2_200
MAX_GUIDE_TEXT_CHARS = 14_000
MAX_CONTEXT_CHARS = GUIDE_CHAT_MAX_CONTEXT_CHARS
MAX_SECTION_CITATIONS = 32
MAX_EDITABLE_SECTION_CONTEXT_CHARS = 6_000
MAX_SAVED_CHAT_MESSAGES = 200
MAX_SAVED_CHAT_JSON_CHARS = 2_000_000
GUIDE_EDIT_REQUEST_RE = re.compile(
    r"\b(?:adapt|tailor|rewrite|revise|modify|edit|update|shorten|expand|simplify|"
    r"reformat|add|remove|replace|make|turn|change|apply|customize|personalize|"
    r"adjust|rework|improve|fix|correct)\b",
    re.IGNORECASE,
)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


async def _ready_guide(request: Request, material_id: UUID, owner_id: UUID):
    db = database(request)
    material, _access = await material_access(db, material_id, owner_id)
    if material["kind"] != "study_guide":
        raise ApiError(409, "guide_chat_not_supported", "This material is not a study guide.")
    if material["status"] != "ready":
        raise ApiError(409, "guide_chat_not_ready", "Wait for this guide to finish before asking.")
    try:
        content = StudyGuideContent.model_validate(json_value(material.get("content"), {}))
    except (ValidationError, TypeError, ValueError):
        raise ApiError(
            503, "guide_chat_context_unavailable", "This guide is temporarily unavailable for chat.",
        ) from None
    return db, material, content


async def _chat_material(request: Request, material_id: UUID, owner_id: UUID):
    db = database(request)
    material, _access = await material_access(db, material_id, owner_id)
    if material["kind"] != "study_guide":
        raise ApiError(409, "guide_chat_not_supported", "This material is not a study guide.")
    return db, material


def _chat_lock(request: Request, material_id: UUID, owner_id: UUID) -> asyncio.Lock:
    """Serialize one learner's guide thread without holding SQLite during provider work."""
    locks = getattr(request.app.state, "guide_chat_locks", None)
    if locks is None:
        locks = {}
        request.app.state.guide_chat_locks = locks
    key = (str(material_id), str(owner_id))
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


async def _load_chat_transcript(db, material_id: UUID, owner_id: UUID):
    row = await db.fetchrow(
        "SELECT messages_json,revision,updated_at FROM study_material_guide_chats "
        "WHERE material_id=?1 AND user_id=?2",
        material_id,
        owner_id,
    )
    if row is None:
        return [], 0, None
    try:
        raw_messages = json.loads(row["messages_json"])
        transcript = SavedGuideChatResponse(
            messages=[SavedGuideChatMessage.model_validate(item) for item in raw_messages],
            updated_at=row["updated_at"],
        )
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
        logger.error("guide_chat_transcript_invalid material_id=%s", material_id)
        raise ApiError(
            503,
            "guide_chat_unavailable",
            "This saved chat cannot be read right now. Contact support if it continues.",
        ) from None
    return transcript.messages, int(row["revision"]), str(row["updated_at"])


async def _save_chat_transcript(
    db,
    *,
    material_id: UUID,
    owner_id: UUID,
    expected_revision: int,
    messages: list[SavedGuideChatMessage],
) -> str:
    if len(messages) > MAX_SAVED_CHAT_MESSAGES:
        raise ApiError(
            409,
            "guide_chat_limit",
            "This chat reached its saved-message limit. Clear it to continue.",
        )
    if len(messages) % 2 or any(
        item.role != ("user" if index % 2 == 0 else "assistant")
        for index, item in enumerate(messages)
    ):
        raise ApiError(500, "guide_chat_invalid", "The chat could not be saved safely.")
    serialized = json.dumps(
        [item.model_dump(mode="json", exclude_none=True) for item in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    if len(serialized) > MAX_SAVED_CHAT_JSON_CHARS:
        raise ApiError(
            409, "guide_chat_limit", "This chat reached its saved-size limit. Clear it to continue."
        )
    async with db.acquire() as connection:
        async with connection.transaction():
            row = await connection.fetchrow(
                "SELECT revision FROM study_material_guide_chats "
                "WHERE material_id=?1 AND user_id=?2",
                material_id,
                owner_id,
            )
            actual_revision = int(row["revision"]) if row else 0
            if actual_revision != expected_revision:
                raise ApiError(
                    409,
                    "guide_chat_changed",
                    "This chat changed in another window. Reload the guide chat before continuing.",
                )
            if row is None:
                await connection.execute(
                    "INSERT INTO study_material_guide_chats "
                    "(material_id,user_id,messages_json,revision) VALUES (?1,?2,?3,1)",
                    material_id,
                    owner_id,
                    serialized,
                )
            else:
                await connection.execute(
                    "UPDATE study_material_guide_chats SET messages_json=?3,revision=revision+1,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE material_id=?1 AND user_id=?2 AND revision=?4",
                    material_id,
                    owner_id,
                    serialized,
                    expected_revision,
                )
            updated_at = await connection.fetchval(
                "SELECT updated_at FROM study_material_guide_chats "
                "WHERE material_id=?1 AND user_id=?2",
                material_id,
                owner_id,
            )
    return str(updated_at)


def _model_history(messages: list[SavedGuideChatMessage]) -> list[GuideChatMessage]:
    recent = list(messages[-6:])

    def text_for(item: SavedGuideChatMessage) -> str:
        value = item.text
        if item.role == "assistant" and item.practice_question:
            value += "\n\nFresh practice question: " + item.practice_question.prompt
        return value[:6_000]

    while recent and sum(len(text_for(item)) for item in recent) > 12_000:
        recent = recent[2:]
    return [GuideChatMessage(role=item.role, text=text_for(item)) for item in recent]


def _section_text(section: object) -> str:
    """Extract readable saved guide content while excluding answer keys and raw SVG."""
    lines = [str(getattr(section, "heading", ""))]
    body = getattr(section, "body", None)
    if isinstance(body, str) and body:
        lines.append(body)
    blocks = getattr(section, "blocks", None)
    if isinstance(blocks, list):
        for block in blocks:
            data = block.model_dump(mode="json", exclude_none=True)
            fallback = guide_block_fallback(data)
            title = data.get("title")
            alt_text = guide_block_alt_text(data)
            # The fallback includes mathematical definitions and the app-owned
            # explanatory text. Never send arbitrary SVG markup or mini-quiz keys.
            lines.extend(item for item in (title, fallback, alt_text) if item)
    key_terms = getattr(section, "key_terms", [])
    if key_terms:
        lines.append("Key terms: " + ", ".join(str(term) for term in key_terms))
    return "\n".join(lines)[:10_000]


def _citations(section: object) -> list[dict]:
    values: list[dict] = []
    for citation in getattr(section, "sources", []) or []:
        values.append(citation.model_dump(mode="json", exclude_none=True))
    for block in getattr(section, "blocks", []) or []:
        for citation in getattr(block, "sources", []) or []:
            values.append(citation.model_dump(mode="json", exclude_none=True))
    return values


def _editable_section_blocks(section: object, section_index: int) -> list[dict]:
    """Make block IDs and concise descriptions available without sending raw SVG or quiz keys."""
    if isinstance(section, StudyGuideSectionV2):
        raw_blocks = [block.model_dump(mode="json", exclude_none=True) for block in section.blocks]
    elif isinstance(section, GuideSectionContent):
        raw_blocks = [{
            "type": "paragraph",
            "text": section.body,
        }]
    else:
        return []

    blocks: list[dict] = []
    used_ids: set[str] = set()
    for index, raw in enumerate(raw_blocks):
        item = dict(raw)
        block_id = str(item.get("id") or f"legacy-s{section_index + 1}-b{index + 1}")[:100]
        if block_id in used_ids:
            block_id = f"{block_id[:84]}-{index + 1}"
        used_ids.add(block_id)
        item["id"] = block_id
        if item.get("type") == "svg_diagram":
            item.pop("svg", None)
            item["visual_present"] = True
        item.pop("sources", None)
        item.pop("id", None)
        for key in ("text", "expression", "fallback_text", "alt_text"):
            if isinstance(item.get(key), str):
                item[key] = item[key][:1_200]
        item["id"] = block_id
        blocks.append(item)

    bounded: list[dict] = []
    used_chars = 0
    for item in blocks:
        encoded_size = len(json.dumps(item, ensure_ascii=False))
        if bounded and used_chars + encoded_size > MAX_EDITABLE_SECTION_CONTEXT_CHARS:
            bounded.append({"id": item["id"], "type": item.get("type"), "summary_truncated": True})
            continue
        bounded.append(item)
        used_chars += encoded_size
    return bounded


def _section_edit_basis(section: object, section_index: int) -> tuple[str, list[dict]]:
    if isinstance(section, StudyGuideSectionV2):
        heading = section.heading
        key_terms = list(section.key_terms)
    elif isinstance(section, GuideSectionContent):
        heading = section.heading
        key_terms = list(section.key_terms)
    else:
        return "", []
    blocks = _editable_section_blocks(section, section_index)
    return json.dumps({"heading": heading, "key_terms": key_terms, "blocks": blocks}, ensure_ascii=False), blocks


def _default_section_citations(section: object) -> list[dict]:
    section_sources = [
        citation.model_dump(mode="json", exclude_none=True)
        for citation in getattr(section, "sources", []) or []
    ]
    if section_sources:
        return section_sources[:20]
    block_sources: list[dict] = []
    seen: set[str] = set()
    for block in getattr(section, "blocks", []) or []:
        for citation in getattr(block, "sources", []) or []:
            value = citation.model_dump(mode="json", exclude_none=True)
            key = json.dumps(value, sort_keys=True, separators=(",", ":"))
            if key not in seen:
                block_sources.append(value)
                seen.add(key)
    return block_sources[:20]


def _build_edit_preview(
    section: object,
    *,
    section_index: int,
    edit: object,
    retrieved_source_ids: list[str],
    section_citations: list[dict],
) -> GuideChatSectionEdit:
    """Apply validated, bounded operations to a saved section without touching its check."""
    if isinstance(section, StudyGuideSectionV2):
        raw_blocks = [block.model_dump(mode="json", exclude_none=True) for block in section.blocks]
        heading = section.heading
        key_terms = list(section.key_terms)
    elif isinstance(section, GuideSectionContent):
        raw_blocks = [{
            "id": f"legacy-s{section_index + 1}-b1",
            "type": "paragraph",
            "text": section.body,
            "sources": [],
        }]
        heading = section.heading
        key_terms = list(section.key_terms)
    else:
        raise ValueError("This section cannot be edited safely.")

    normalized_blocks: list[dict] = []
    ids: set[str] = set()
    for index, raw in enumerate(raw_blocks):
        block = dict(raw)
        block_id = str(block.get("id") or f"legacy-s{section_index + 1}-b{index + 1}")[:100]
        if block_id in ids:
            block_id = f"{block_id[:84]}-{index + 1}"
        ids.add(block_id)
        block["id"] = block_id
        normalized_blocks.append(block)
    by_id = {block["id"]: block for block in normalized_blocks}

    replacements = {item.block_id: item.block for item in edit.replace_blocks}
    removals = set(edit.remove_block_ids)
    insertions: dict[str | None, list[object]] = {}
    for item in edit.insert_blocks:
        if item.after_block_id is not None and item.after_block_id not in by_id:
            raise ValueError("The proposed insertion point is not in the saved section.")
        insertions.setdefault(item.after_block_id, []).append(item.block)
    if not set(replacements).issubset(by_id) or not removals.issubset(by_id):
        raise ValueError("The proposal references a block that is no longer in this section.")
    if set(replacements) & removals:
        raise ValueError("The proposal both replaces and removes the same block.")

    # Never trust model-authored source IDs or locators. Preserve old citations for
    # replacements and attach file-level IDs from source chunks fetched this turn.
    existing_citation_ids = {
        str(item.get("upload_id"))
        for item in section_citations
        if item.get("upload_id")
    }
    retrieved_citations = [
        {"upload_id": source_id}
        for source_id in dict.fromkeys(retrieved_source_ids)
        if source_id not in existing_citation_ids
    ][:8]
    fallback_citations = retrieved_citations or section_citations

    final_blocks: list[dict] = []
    for block in normalized_blocks:
        block_id = block["id"]
        if block_id not in removals:
            if block_id in replacements:
                replacement = replacements[block_id].model_dump(mode="json", exclude_none=True)
                replacement["id"] = block_id
                replacement["sources"] = block.get("sources") or fallback_citations
                final_blocks.append(replacement)
            else:
                final_blocks.append(block)
        for inserted in insertions.get(block_id, []):
            data = inserted.model_dump(mode="json", exclude_none=True)
            data["id"] = f"chat-edit-{uuid4().hex}"
            data["sources"] = fallback_citations
            final_blocks.append(data)
    for inserted in insertions.get(None, []):
        data = inserted.model_dump(mode="json", exclude_none=True)
        data["id"] = f"chat-edit-{uuid4().hex}"
        data["sources"] = fallback_citations
        final_blocks.append(data)
    if not 1 <= len(final_blocks) <= 24:
        raise ValueError("The proposed section would have an unsupported number of blocks.")
    if not any(block.get("type") == "paragraph" for block in final_blocks):
        raise ValueError("Keep at least one plain-language explanation in each section.")

    result = GuideChatSectionEdit(
        heading=edit.heading if edit.heading is not None else heading,
        key_terms=edit.key_terms if edit.key_terms is not None else key_terms,
        blocks=final_blocks,
    )
    return result


def _reference_from_chunk(chunk: dict) -> GuideChatReference | None:
    try:
        locator = chunk.get("chunk_locator") or chunk.get("locator") or {}
        if not isinstance(locator, dict):
            locator = {}
        page = locator.get("page")
        slide = locator.get("slide")
        timestamp = locator.get("timestamp_seconds", locator.get("start_seconds"))
        return GuideChatReference(
            upload_id=UUID(str(chunk["material_id"])),
            label=str(chunk.get("title") or "Course source")[:200],
            page=page if isinstance(page, int) and not isinstance(page, bool) and page > 0 else None,
            slide=slide if isinstance(slide, int) and not isinstance(slide, bool) and slide > 0 else None,
            timestamp_s=(
                float(timestamp)
                if isinstance(timestamp, (int, float))
                and not isinstance(timestamp, bool)
                and timestamp >= 0
                else None
            ),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _authorized_source_context(
    db,
    *,
    owner_id: UUID,
    material_id: UUID,
    linked_sources: list[dict],
    preferred_upload_ids: list[str],
    query: str,
    warnings: set[str],
) -> tuple[list[dict], list[GuideChatReference]]:
    sources_by_id = {str(row["id"]): row for row in linked_sources if row.get("id")}
    preferred = list(dict.fromkeys(
        source_id for source_id in preferred_upload_ids if source_id in sources_by_id
    ))
    ordered_ids = preferred + [key for key in sources_by_id if key not in preferred]
    if not ordered_ids:
        warnings.add("source_missing")
        warnings.add("source_evidence_unavailable")
        return [], []
    if len(ordered_ids) > MAX_SOURCE_FILES:
        warnings.add("source_context_truncated")

    excerpts: list[dict] = []
    references: list[GuideChatReference] = []
    seen_references: set[tuple[str, int | None, int | None, float | None]] = set()
    for upload_id in ordered_ids[:MAX_SOURCE_FILES]:
        source = sources_by_id[upload_id]
        class_id = str(source.get("class_id") or "")
        if not class_id:
            warnings.add("source_missing")
            continue
        try:
            result = await search_authorized_sources(
                db,
                class_id=class_id,
                user_id=str(owner_id),
                query=query[:500],
                upload_id=upload_id,
                limit=2,
            )
        except ApiError as exc:
            if exc.status_code in {403, 404}:
                warnings.add("source_missing")
                continue
            raise
        for item in result.get("items", []):
            if len(excerpts) >= MAX_SOURCE_CHUNKS:
                warnings.add("source_context_truncated")
                break
            try:
                # Search supplies only a short preview. Fetch the exact current
                # authorized revision again before putting an excerpt in context.
                chunk = await fetch_authorized_chunk(
                    db,
                    class_id=class_id,
                    user_id=str(owner_id),
                    chunk_revision_id=str(item["chunk_revision_id"]),
                )
            except ApiError as exc:
                if exc.status_code in {403, 404}:
                    warnings.add("source_missing")
                    continue
                raise
            excerpt = str(chunk.get("text") or "").strip()[:MAX_SOURCE_CHUNK_CHARS]
            if not excerpt:
                continue
            locator = chunk.get("chunk_locator") or {}
            excerpts.append({
                "upload_id": str(chunk["material_id"]),
                "title": str(chunk.get("title") or "Course source")[:200],
                "locator": locator if isinstance(locator, dict) else {},
                "text": excerpt,
            })
            reference = _reference_from_chunk(chunk)
            if reference is not None:
                key = (
                    str(reference.upload_id), reference.page, reference.slide,
                    reference.timestamp_s,
                )
                if key not in seen_references:
                    references.append(reference)
                    seen_references.add(key)
    if not excerpts:
        warnings.add("source_evidence_unavailable")
    return excerpts, references[:8]


@router.get(
    "/api/study-materials/{material_id}/guide-chat",
    response_model=SavedGuideChatResponse,
)
async def get_guide_chat(
    material_id: UUID, request: Request, response: Response
) -> SavedGuideChatResponse:
    _no_store(response)
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material = await _chat_material(request, material_id, owner_id)
    async with _chat_lock(request, material_id, owner_id):
        messages, _revision, updated_at = await _load_chat_transcript(db, material_id, owner_id)
    return SavedGuideChatResponse(messages=messages, updated_at=updated_at)


@router.delete("/api/study-materials/{material_id}/guide-chat", status_code=204)
async def clear_guide_chat(material_id: UUID, request: Request) -> Response:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material = await _chat_material(request, material_id, owner_id)
    async with _chat_lock(request, material_id, owner_id):
        _messages, revision, _updated_at = await _load_chat_transcript(db, material_id, owner_id)
        await _save_chat_transcript(
            db,
            material_id=material_id,
            owner_id=owner_id,
            expected_revision=revision,
            messages=[],
        )
    result = Response(status_code=204)
    _no_store(result)
    return result


@router.patch(
    "/api/study-materials/{material_id}/guide-chat/{message_id}",
    response_model=GuideChatEditStateResponse,
)
async def update_guide_chat_edit_state(
    material_id: UUID,
    message_id: UUID,
    payload: GuideChatEditStateRequest,
    request: Request,
    response: Response,
) -> GuideChatEditStateResponse:
    _no_store(response)
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material = await _chat_material(request, material_id, owner_id)
    async with _chat_lock(request, material_id, owner_id):
        messages, revision, updated_at = await _load_chat_transcript(db, material_id, owner_id)
        target_index = next(
            (index for index, item in enumerate(messages) if item.id == message_id),
            None,
        )
        if target_index is None:
            raise ApiError(
                404, "guide_chat_message_not_found", "That saved chat message is unavailable."
            )
        target = messages[target_index]
        if target.role != "assistant" or target.edit_proposal is None:
            raise ApiError(
                409,
                "guide_chat_edit_unavailable",
                "That message has no saved guide edit to update.",
            )
        if target.edit_state == payload.state:
            updated_at = updated_at or datetime.now(UTC).isoformat(
                timespec="milliseconds"
            ).replace("+00:00", "Z")
        elif target.edit_state != "pending":
            raise ApiError(
                409, "guide_chat_edit_changed", "That guide edit was already resolved."
            )
        else:
            messages[target_index] = target.model_copy(update={"edit_state": payload.state})
            updated_at = await _save_chat_transcript(
                db,
                material_id=material_id,
                owner_id=owner_id,
                expected_revision=revision,
                messages=messages,
            )
    return GuideChatEditStateResponse(
        message_id=message_id, state=payload.state, updated_at=updated_at
    )


@router.post(
    "/api/study-materials/{material_id}/guide-chat",
    response_model=GuideChatResponse,
)
async def guide_chat(
    material_id: UUID,
    payload: GuideChatRequest,
    request: Request,
    response: Response,
) -> GuideChatResponse:
    _no_store(response)
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material = await _chat_material(request, material_id, owner_id)
    async with _chat_lock(request, material_id, owner_id):
        return await _generate_guide_chat_turn(
            material_id, payload, request, response, owner_id=owner_id, db=db
        )


async def _generate_guide_chat_turn(
    material_id: UUID,
    payload: GuideChatRequest,
    request: Request,
    response: Response,
    *,
    owner_id: UUID,
    db,
) -> GuideChatResponse:
    db, material, guide = await _ready_guide(request, material_id, owner_id)
    if payload.section_index >= len(guide.sections):
        raise ApiError(400, "invalid_section", "That guide section is unavailable.")
    section = guide.sections[payload.section_index]
    if payload.block_index is not None:
        blocks = getattr(section, "blocks", None)
        if not isinstance(blocks, list) or payload.block_index >= len(blocks):
            raise ApiError(400, "invalid_block", "That guide block is unavailable.")

    saved_messages, transcript_revision, _transcript_updated_at = await _load_chat_transcript(
        db, material_id, owner_id
    )
    if len(saved_messages) + 2 > MAX_SAVED_CHAT_MESSAGES:
        raise ApiError(
            409,
            "guide_chat_limit",
            "This chat reached its saved-message limit. Clear it to continue.",
        )

    limiter = request.app.state.study_tutor_rate_limiter
    retry_after = await limiter.check(str(owner_id))
    if retry_after is not None:
        raise ApiError(
            429, "guide_chat_rate_limited", "Too many guide chat requests. Try again shortly.",
            retry_after_seconds=retry_after,
        )

    if payload.scope == "section":
        selected_text = _section_text(section)
        if payload.block_index is not None:
            block = section.blocks[payload.block_index]
            block_data = block.model_dump(mode="json", exclude_none=True)
            selected_text += "\nSelected block: " + guide_block_fallback(block_data)
        guide_reference_text = selected_text[:MAX_GUIDE_TEXT_CHARS]
        relevant_citations = _citations(section)
    else:
        # Put the open section first so a long guide cannot truncate the focus.
        selected_text = _section_text(section)
        other_sections = [
            _section_text(other)
            for index, other in enumerate(guide.sections)
            if index != payload.section_index
        ]
        outline = "\n".join(str(item.heading) for item in guide.sections)
        guide_reference_text = (
            f"Current section:\n{selected_text}\n\nGuide outline:\n{outline}\n\n"
            "Other guide section text:\n" + "\n\n".join(other_sections)
        )[:MAX_GUIDE_TEXT_CHARS]
        if len(guide_reference_text) >= MAX_GUIDE_TEXT_CHARS:
            warnings_for_truncation = True
        else:
            warnings_for_truncation = False
        relevant_citations = [citation for item in guide.sections for citation in _citations(item)]

    warnings: set[str] = set()
    if material.get("sources_outdated"):
        warnings.add("source_changed")
    if material.get("sources_missing"):
        warnings.add("source_missing")
    if material.get("context_truncated"):
        warnings.add("source_context_truncated")
    if payload.scope == "guide" and warnings_for_truncation:
        warnings.add("source_context_truncated")

    linked_sources = await db.fetch(
        "SELECT DISTINCT n.id,n.class_id,n.title FROM study_material_sources s "
        "JOIN course_notes n ON n.id=COALESCE(s.source_note_id,s.note_id) "
        "WHERE s.material_id=?1 ORDER BY n.title COLLATE NOCASE,n.id LIMIT 128",
        material_id,
    )
    preferred_ids = [
        str(citation.get("upload_id"))
        for citation in relevant_citations
        if isinstance(citation, dict) and citation.get("upload_id")
    ]
    selected_block_text = ""
    if payload.block_index is not None:
        block_data = section.blocks[payload.block_index].model_dump(mode="json", exclude_none=True)
        selected_block_text = guide_block_fallback(block_data)
    retrieval_query = " ".join([
        str(section.heading), selected_block_text, payload.message,
    ]).strip()
    source_excerpts, references = await _authorized_source_context(
        db,
        owner_id=owner_id,
        material_id=material_id,
        linked_sources=[dict(row) for row in linked_sources],
        preferred_upload_ids=preferred_ids,
        query=retrieval_query or str(section.heading),
        warnings=warnings,
    )
    guide_editing_allowed = (
        material.get("resource_scope") == "personal"
        and str(material.get("owner_id") or "") == str(owner_id)
    )
    context = {
        "guide_title": str(material.get("title") or "Study guide")[:300],
        "scope": payload.scope,
        "focus_section": str(section.heading)[:300],
        "guide_editing_allowed": guide_editing_allowed,
        "editable_section_available": guide_editing_allowed,
        "guide_reference_text": guide_reference_text,
        "course_source_excerpts": source_excerpts,
        "source_warnings": sorted(warnings),
    }
    if guide_editing_allowed:
        context["editable_section"] = {
            "section_index": payload.section_index,
            "editable_fields": "heading, key_terms, and blocks only; the server preserves sources, section identity, and the saved mini-quiz",
            "section_json": _section_edit_basis(section, payload.section_index)[0],
        }
    if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        # Keep the guide and source contexts bounded even after JSON escaping.
        context["guide_reference_text"] = str(context["guide_reference_text"])[:8_000]
        context["course_source_excerpts"] = [
            {**item, "text": str(item["text"])[:1_600]}
            for item in source_excerpts[:4]
        ]
        warnings.add("source_context_truncated")
        context["source_warnings"] = sorted(warnings)
    if len(json.dumps(context, ensure_ascii=False)) > MAX_CONTEXT_CHARS:
        context.pop("editable_section", None)
        context["editable_section_available"] = False
        context["editing_unavailable"] = "The focused section is too large to safely propose edits."
    allow_edit = bool(
        context.get("guide_editing_allowed") is True
        and context.get("editable_section_available") is True
        and payload.intent != "practice"
        and (
            payload.intent == "adapt"
            or GUIDE_EDIT_REQUEST_RE.search(payload.message)
        )
    )

    service = getattr(request.app.state, "guide_chat_service", None)
    if service is None:
        raise ApiError(503, "guide_chat_unavailable", "Guide chat is unavailable. Try again shortly.")
    request_id = getattr(request.state, "request_id", "unknown")
    usage_token = bind_usage_context(
        operation_id=str(material_id),
        operation_kind="study_guide_chat",
        operation_label="Study guide chat",
    )
    try:
        edit_kwargs = {"allow_edit": True} if allow_edit else {}
        draft = await service.reply(
            context,
            _model_history(saved_messages),
            payload.message,
            intent=payload.intent,
            request_id=request_id,
            **edit_kwargs,
        )
    finally:
        reset_usage_context(usage_token)
    response_references = references[:8]
    practice_question = None
    if payload.intent == "practice" and draft.practice_question:
        practice_question = GuidePracticeQuestion(
            prompt=draft.practice_question,
            references=response_references,
        )
    edit_proposal = None
    reply = draft.reply
    if draft.edit_proposal is not None and allow_edit:
        try:
            retrieved_source_ids = list(dict.fromkeys(
                str(item.get("upload_id"))
                for item in source_excerpts
                if item.get("upload_id")
            ))
            edited_section = _build_edit_preview(
                section,
                section_index=payload.section_index,
                edit=draft.edit_proposal,
                retrieved_source_ids=retrieved_source_ids,
                section_citations=_default_section_citations(section),
            )
            edit_proposal = GuideChatEditPreview(
                section_index=payload.section_index,
                expected_fingerprint=guide_section_fingerprint(section),
                summary=draft.edit_proposal.summary,
                section=edited_section,
            )
        except (TypeError, ValueError, ValidationError) as exc:
            logger.info(
                "guide_chat_edit_rejected request_id=%s category=%s",
                request_id,
                type(exc).__name__,
            )
            reply = (
                reply.rstrip()
                + "\n\nI couldn't prepare a safe section preview, so the saved guide was not changed."
            )[:6000]
    user_message_id = uuid4()
    assistant_message_id = uuid4()
    user_created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    assistant_created_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
    user_entry = SavedGuideChatMessage(
        id=user_message_id,
        role="user",
        text=payload.message,
        section_index=payload.section_index,
        section_heading=str(section.heading)[:300],
        scope=payload.scope,
        block_index=payload.block_index,
        intent=payload.intent,
        created_at=user_created_at,
    )
    assistant_entry = SavedGuideChatMessage(
        id=assistant_message_id,
        role="assistant",
        text=reply,
        section_index=payload.section_index,
        section_heading=str(section.heading)[:300],
        scope=payload.scope,
        block_index=payload.block_index,
        intent=payload.intent,
        created_at=assistant_created_at,
        references=response_references,
        source_warnings=sorted(warnings),
        practice_question=practice_question,
        edit_proposal=edit_proposal,
        edit_state="pending" if edit_proposal else None,
    )
    saved = True
    try:
        await _save_chat_transcript(
            db,
            material_id=material_id,
            owner_id=owner_id,
            expected_revision=transcript_revision,
            messages=[*saved_messages, user_entry, assistant_entry],
        )
    except ApiError:
        raise
    except (sqlite3.Error, TypeError, ValueError) as exc:
        saved = False
        logger.warning(
            "guide_chat_transcript_save_failed material_id=%s category=%s",
            material_id,
            type(exc).__name__,
        )
    return GuideChatResponse(
        reply=reply,
        references=response_references,
        source_warnings=sorted(warnings),
        practice_question=practice_question,
        edit_proposal=edit_proposal,
        saved=saved,
        user_message_id=user_message_id if saved else None,
        assistant_message_id=assistant_message_id if saved else None,
    )


async def _guide_section_fingerprint(
    db,
    *,
    owner_id: UUID,
    material_id: UUID,
    section: object,
    section_index: int,
) -> tuple[str, str]:
    section_data = section.model_dump(mode="json", exclude_none=True)
    citations = _citations(section)
    upload_ids = list(dict.fromkeys(
        str(item.get("upload_id")) for item in citations if item.get("upload_id")
    ))[:MAX_SECTION_CITATIONS]
    current_sources: list[dict] = []
    if upload_ids:
        placeholders = ",".join(f"?{index + 2}" for index in range(len(upload_ids)))
        rows = await db.fetch(
            "SELECT n.id,n.class_id,n.created_by,n.publication_state,n.release_at,n.status,"
            "n.processing_revision,n.source_index_status,h.revision_id AS indexed_revision_id,"
            "h.processing_revision AS indexed_processing_revision "
            "FROM study_material_sources s LEFT JOIN course_notes n "
            "ON n.id=COALESCE(s.source_note_id,s.note_id) "
            "LEFT JOIN source_index_heads h ON h.note_id=n.id "
            "WHERE s.material_id=?1 AND COALESCE(s.source_note_id,s.note_id) IN ("
            + placeholders + ")",
            material_id,
            *upload_ids,
        )
        by_id = {str(row["id"]): row for row in rows if row.get("id")}
        class_access_cache: dict[str, object] = {}
        for upload_id in upload_ids:
            source = by_id.get(upload_id)
            if source is None:
                current_sources.append({"upload_id": upload_id, "state": "missing"})
                continue
            class_id = str(source.get("class_id") or "")
            try:
                access = class_access_cache.get(class_id)
                if access is None:
                    access = await class_access(db, class_id, owner_id, required="read")
                    class_access_cache[class_id] = access
                if not (
                    is_effectively_published(source.get("publication_state"), source.get("release_at"))
                    or can_view_unreleased(access, owner_id, source.get("created_by"))
                ):
                    raise ApiError(404, "not_found", "The requested source was not found.")
                indexed = (
                    source.get("status") == "ready"
                    and source.get("source_index_status") == "ready"
                    and source.get("processing_revision") == source.get("indexed_processing_revision")
                )
                current_sources.append({
                    "upload_id": upload_id,
                    "revision_id": source.get("indexed_revision_id") if indexed else None,
                    "processing_revision": source.get("processing_revision"),
                    "state": "ready" if indexed else "unindexed",
                })
            except ApiError:
                current_sources.append({"upload_id": upload_id, "state": "unavailable"})
    # This is a source/content fingerprint only. It contains no choice selected
    # by the learner and no chat or free-text response.
    section_id = f"{section_index}:{getattr(section, 'id', None) or 'legacy'}"
    canonical = json.dumps(
        {
            "section_id": section_id,
            "section": section_data,
            "current_sources": current_sources,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return section_id, fingerprint


async def _record_guide_check(
    db,
    *,
    owner_id: UUID,
    material_id: UUID,
    section_id: str,
    operation_id: UUID,
    content_fingerprint: str,
    correct: bool,
    attempted_at: str,
    next_due_at: str,
) -> dict:
    """Atomically apply a first submission or replay its durable receipt."""
    async with db.acquire() as connection:
        async with connection.transaction():
            receipt = await connection.fetchrow(
                "SELECT content_fingerprint,correct,attempt_count,attempted_at,next_due_at "
                "FROM guide_section_review_receipts WHERE owner_id=?1 AND material_id=?2 "
                "AND section_id=?3 AND operation_id=?4",
                owner_id,
                material_id,
                section_id,
                operation_id,
            )
            if receipt is not None:
                if receipt["content_fingerprint"] != content_fingerprint:
                    raise ApiError(
                        409,
                        "guide_check_operation_conflict",
                        "This check submission has already been used for different guide content.",
                    )
                return receipt

            current = await connection.fetchrow(
                "SELECT content_fingerprint,attempt_count FROM guide_section_review_state "
                "WHERE owner_id=?1 AND material_id=?2 AND section_id=?3",
                owner_id,
                material_id,
                section_id,
            )
            attempt_count = (
                int(current["attempt_count"]) + 1
                if current is not None and current["content_fingerprint"] == content_fingerprint
                else 1
            )
            await connection.execute(
                "INSERT INTO guide_section_review_receipts "
                "(owner_id,material_id,section_id,operation_id,content_fingerprint,correct,"
                "attempt_count,attempted_at,next_due_at) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)",
                owner_id,
                material_id,
                section_id,
                operation_id,
                content_fingerprint,
                int(correct),
                attempt_count,
                attempted_at,
                next_due_at,
            )
            await connection.execute(
                "INSERT INTO guide_section_review_state "
                "(owner_id,material_id,section_id,content_fingerprint,attempt_count,last_correct,"
                "last_attempt_at,next_due_at,last_operation_id) "
                "VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9) "
                "ON CONFLICT(owner_id,material_id,section_id) DO UPDATE SET "
                "content_fingerprint=excluded.content_fingerprint,"
                "attempt_count=excluded.attempt_count,last_correct=excluded.last_correct,"
                "last_attempt_at=excluded.last_attempt_at,next_due_at=excluded.next_due_at,"
                "last_operation_id=excluded.last_operation_id,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                owner_id,
                material_id,
                section_id,
                content_fingerprint,
                attempt_count,
                int(correct),
                attempted_at,
                next_due_at,
                operation_id,
            )
            return {
                "content_fingerprint": content_fingerprint,
                "correct": int(correct),
                "attempt_count": attempt_count,
                "attempted_at": attempted_at,
                "next_due_at": next_due_at,
            }


@router.get(
    "/api/study-materials/{material_id}/guide-progress",
    response_model=GuideProgressResponse,
)
async def get_guide_progress(
    material_id: UUID, request: Request, response: Response
) -> GuideProgressResponse:
    _no_store(response)
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material, guide = await _ready_guide(request, material_id, owner_id)
    saved = await db.fetch(
        "SELECT section_id,content_fingerprint,attempt_count,last_correct,last_attempt_at,next_due_at "
        "FROM guide_section_review_state WHERE owner_id=?1 AND material_id=?2",
        owner_id,
        material_id,
    )
    saved_by_section = {str(row["section_id"]): row for row in saved}
    sections: list[GuideSectionProgress] = []
    for index, section in enumerate(guide.sections):
        section_id = f"{index}:{getattr(section, 'id', None) or 'legacy'}"
        row = saved_by_section.get(section_id)
        if row is None or int(row["attempt_count"]) < 1 or row.get("last_correct") is None:
            continue
        _current_id, fingerprint = await _guide_section_fingerprint(
            db, owner_id=owner_id, material_id=material_id, section=section, section_index=index,
        )
        if row["content_fingerprint"] != fingerprint:
            continue
        if not row.get("last_attempt_at") or not row.get("next_due_at"):
            continue
        sections.append(GuideSectionProgress(
            section_index=index,
            attempt_count=int(row["attempt_count"]),
            last_correct=bool(row["last_correct"]),
            last_attempt_at=str(row["last_attempt_at"]),
            next_due_at=str(row["next_due_at"]),
        ))
    return GuideProgressResponse(sections=sections)


@router.post(
    "/api/study-materials/{material_id}/guide-sections/{section_index}/mini-quiz",
    response_model=GuideMiniQuizAttemptResponse,
)
async def submit_guide_mini_quiz(
    material_id: UUID,
    section_index: int,
    payload: GuideMiniQuizAttemptRequest,
    request: Request,
    response: Response,
) -> GuideMiniQuizAttemptResponse:
    _no_store(response)
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material, guide = await _ready_guide(request, material_id, owner_id)
    if not 0 <= section_index < len(guide.sections):
        raise ApiError(404, "guide_section_not_found", "That guide section is unavailable.")
    section = guide.sections[section_index]
    if not isinstance(section, StudyGuideSectionV2) or section.mini_quiz is None:
        raise ApiError(409, "guide_check_unavailable", "This section has no independent check yet.")
    section_id, fingerprint = await _guide_section_fingerprint(
        db, owner_id=owner_id, material_id=material_id, section=section, section_index=section_index,
    )
    quiz = section.mini_quiz
    if payload.choice_index >= len(quiz.choices):
        raise ApiError(400, "invalid_choice", "Choose one of the available answers.")
    correct = payload.choice_index == quiz.correct_index
    now = datetime.now(UTC)
    next_due = now + (timedelta(days=1) if correct else timedelta(minutes=10))
    last_attempt_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    next_due_at = next_due.isoformat(timespec="seconds").replace("+00:00", "Z")

    receipt = await _record_guide_check(
        db,
        owner_id=owner_id,
        material_id=material_id,
        section_id=section_id,
        operation_id=payload.operation_id,
        content_fingerprint=fingerprint,
        correct=correct,
        attempted_at=last_attempt_at,
        next_due_at=next_due_at,
    )
    return GuideMiniQuizAttemptResponse(
        correct=bool(receipt["correct"]),
        correct_choice=quiz.choices[quiz.correct_index],
        explanation=quiz.explanation,
        attempt_count=int(receipt["attempt_count"]),
        last_attempt_at=str(receipt["attempted_at"]),
        next_due_at=str(receipt["next_due_at"]),
        mastery_updated=False,
    )


@router.delete("/api/study-materials/{material_id}/guide-progress", status_code=204)
async def clear_guide_progress(
    material_id: UUID, request: Request
) -> Response:
    owner_id = await enforce_sqlite_api_policy(request, request.app.state.settings)
    db, _material, _guide = await _ready_guide(request, material_id, owner_id)
    async with db.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                "DELETE FROM guide_section_review_receipts WHERE owner_id=?1 AND material_id=?2",
                owner_id,
                material_id,
            )
            await connection.execute(
                "DELETE FROM guide_section_review_state WHERE owner_id=?1 AND material_id=?2",
                owner_id,
                material_id,
            )
    response = Response(status_code=204)
    _no_store(response)
    return response
