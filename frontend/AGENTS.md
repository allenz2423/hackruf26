# Frontend Agent Instructions

Read the repository [AGENTS.md](../AGENTS.md) and [frontend design](../docs/frontend-design.md) before changing the client.

## Scope

- Own the browser UI and client behavior under `frontend/`.
- Use HTML, CSS, and JavaScript modules with Vite.
- Do not add provider SDKs, model identifiers, prompts, tool declarations, or provider credentials to the browser. The backend owns all provider integration and tool execution.
- Coordinate shared WebSocket/API changes with the backend role; do not modify Python implementation or server-only secrets from a frontend-only task.

## Required behavior

- Start `getDisplayMedia()` only from the student's explicit Share action and let the browser show its source picker.
- Draw annotations over the shared-screen preview inside this page. Never claim to draw over another desktop app.
- Request a one-use ticket from `POST /api/live-sessions`, then connect to same-origin `WS /api/live` using the ticket subprotocol.
- Relay sampled frames, typed questions, and enabled microphone audio only for the active session. The backend does not record or store live media.
- Validate annotation requests against the referenced local frame, coordinate bounds, allowed shapes, and reasonable label/count limits before rendering.
- Stop capture and audio immediately when the student ends or pauses the corresponding input. Clear in-memory media and annotations at session end.
- Keep sharing, microphone, connection, and error states visible and accessible.

## Shared API contract

`POST /api/live-sessions` accepts `{}` or an optional class ID and returns a short-lived, one-use WebSocket ticket and expiry. The ticket grants access only to `WS /api/live`; it is not a provider credential. The browser sends generic session events and never receives model configuration.
