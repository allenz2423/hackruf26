"""Same-origin WebSocket entry point for server-owned tutor sessions."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket

from app.api.dependencies import origin_is_allowed
from app.services.live_sessions import LiveSessionTicketStore

router = APIRouter()
TICKET_PROTOCOL_PREFIX = "ticket."
WEBSOCKET_PROTOCOL = "screen-tutor.v1"


@router.websocket("/api/live")
async def live_session(websocket: WebSocket) -> None:
    """Consume a one-use ticket and bridge this browser to a private Live session."""
    settings = websocket.app.state.settings
    if not origin_is_allowed(websocket.headers.get("origin"), settings):
        await websocket.close(code=1008)
        return

    offered_protocols = websocket.scope.get("subprotocols", [])
    ticket = next(
        (
            protocol[len(TICKET_PROTOCOL_PREFIX) :]
            for protocol in offered_protocols
            if protocol.startswith(TICKET_PROTOCOL_PREFIX)
        ),
        None,
    )
    if not ticket or WEBSOCKET_PROTOCOL not in offered_protocols:
        await websocket.close(code=1008)
        return

    store: LiveSessionTicketStore = websocket.app.state.live_session_ticket_store
    session_ticket = await store.consume(ticket)
    if session_ticket is None:
        await websocket.close(code=1008)
        return

    await websocket.accept(subprotocol=WEBSOCKET_PROTOCOL)
    if not await store.register_active(session_ticket, websocket):
        await websocket.close(code=1008)
        return
    try:
        await websocket.app.state.live_session_service.run(
            websocket,
            session_ticket.class_context,
            mode=session_ticket.mode,
            language=session_ticket.language,
            memory_target=session_ticket.memory_target,
        )
    finally:
        await store.unregister_active(session_ticket, websocket)
