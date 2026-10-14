# Agent Instructions

These instructions apply to all work in this repository. Read the relevant design doc before changing implementation. The frontend and backend designs are the current source of truth for their respective areas.

## Project boundaries

- Screen Tutor is a browser application. Marks are drawn over the shared-screen preview inside the page; do not implement a system-wide desktop overlay.
- The browser captures and previews the display, then relays sampled frames and enabled microphone audio through a same-origin backend WebSocket. Live media is handled in memory and never recorded or stored by Screen Tutor.
- The backend owns all provider SDK use, `GEMINI_API_KEY`, model selection, instructions, session configuration, and tool execution. The browser must not contain provider SDKs, model identifiers, prompts, tool declarations, or provider credentials.
- The only model-requested visual action is `add_annotations`. The model must not get general mouse, keyboard, shell, or application-control tools.
- Do not add media recording, screenshot persistence, or conversation storage without an explicit product decision.

## Shared integration contract

- Frontend implementation belongs in `frontend/`; backend implementation belongs in `backend/`.
- Accounts are backend-owned: `POST /api/auth/signup`, `POST /api/auth/login`, `POST /api/auth/logout`, and `GET /api/auth/me` (`{"user": null}` for a guest). The session is an HttpOnly cookie; the frontend never reads or stores a token. Class-library endpoints and class tutoring always require a logged-in account (`401` otherwise). Quick tutoring, calculator/Lean tools, and local study pages require an account unless `ALLOW_GUEST_ACCESS=true`. New account signup is closed by default (`SIGNUP_MODE=closed`); use `open` for immediate registration or `approval` for administrator-reviewed requests.
- The backend exposes `POST /api/live-sessions`. The frontend sends `{}` or an optional `class_id` (with optional `upload_ids` to limit the class context to chosen uploads) and receives a short-lived, one-use WebSocket ticket and expiry; it receives no provider credential or model metadata.
- The frontend connects to same-origin `WS /api/live` using that ticket as a WebSocket subprotocol. The backend chooses the provider, model, tools, and configuration; the client cannot expand permissions.
- The backend forwards live media in memory and never logs or stores it. The frontend validates annotation geometry against its local frame map and renders marks only over the matching preview.
- An annotation references a `frame_id` and uses normalized coordinates from 0 to 1 relative to that exact captured frame. The frontend validates geometry and renders it only over the corresponding preview.
- Keep the HTTP API same-origin in production where possible. If either side needs to change the shared contract, update the relevant design docs and coordinate with the other role before changing code.

## Ownership

- A frontend agent owns `frontend/**` and [the frontend design](docs/frontend-design.md). Do not modify backend implementation or server-only secrets from that role.
- A backend agent owns `backend/**` and [the backend design](docs/backend-design.md). Do not modify frontend implementation or UI behavior from that role.
- Both roles may propose changes to [the product overview](docs/design.md), [the README](README.md), and the shared HTTP/tool contracts. Keep those changes limited to what the task requires and communicate interface changes clearly.
- Keep changes within the active role's ownership. If a task requires crossing the boundary, describe the needed contract change and leave the other role's implementation untouched.

## Stack and configuration

- Frontend: HTML, CSS, vanilla JavaScript modules, Vite, and a provider-neutral backend WebSocket client.
- Backend: Python, FastAPI, Pydantic v2, `pydantic-settings`, Google's `google-genai` Python SDK, and Uvicorn.
- Gemini Live model default: `gemini-3.8-live`, selected and used only by the backend.
- `GEMINI_API_KEY` is server-only. Use `.env.example` as the template; `.env` is local and must remain untracked.
- Follow the active task's requested verification scope. Do not broaden work into unrelated features.

## Read these before implementation

- [Product and system design](docs/design.md)
- [Frontend design](docs/frontend-design.md)
- [Backend design](docs/backend-design.md)
