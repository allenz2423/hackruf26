# Agent Instructions

These instructions apply to all work in this repository. Read the relevant design doc before changing implementation. The frontend and backend designs are the current source of truth for their respective areas.

## Project boundaries

- Screen Tutor is a browser application. Marks are drawn over the shared-screen preview inside the page; do not implement a system-wide desktop overlay.
- The browser captures the display and connects directly to Gemini Live using a short-lived ephemeral token.
- The backend issues that token and protects `GEMINI_API_KEY`. Screen frames, microphone audio, model audio, transcripts, and annotation events must not pass through or be stored by the backend.
- The only model-requested visual action is `add_annotations`. The model must not get general mouse, keyboard, shell, or application-control tools.
- Do not add media recording, screenshot persistence, or conversation storage without an explicit product decision.

## Shared integration contract

- Frontend implementation belongs in `frontend/`; backend implementation belongs in `backend/`.
- The backend exposes `POST /api/live-token`. The frontend sends an empty JSON object and receives `access_token`, `model`, and `expires_at`.
- The backend chooses the model and tool configuration. The frontend must not send a model name, system instructions, or tools to expand its permissions.
- The frontend uses the returned ephemeral token for one Live session. Never expose or bundle the long-lived Gemini API key in browser code.
- An annotation references a `frame_id` and uses normalized coordinates from 0 to 1 relative to that exact captured frame. The frontend validates geometry and renders it only over the corresponding preview.
- Keep the HTTP API same-origin in production where possible. If either side needs to change the shared contract, update the relevant design docs and coordinate with the other role before changing code.

## Ownership

- A frontend agent owns `frontend/**` and [the frontend design](docs/frontend-design.md). Do not modify backend implementation or server-only secrets from that role.
- A backend agent owns `backend/**` and [the backend design](docs/backend-design.md). Do not modify frontend implementation or UI behavior from that role.
- Both roles may propose changes to [the product overview](docs/design.md), [the README](README.md), and the shared HTTP/tool contracts. Keep those changes limited to what the task requires and communicate interface changes clearly.
- Keep changes within the active role's ownership. If a task requires crossing the boundary, describe the needed contract change and leave the other role's implementation untouched.

## Stack and configuration

- Frontend: HTML, CSS, vanilla JavaScript modules, Vite, and Google's `@google/genai` JavaScript SDK.
- Backend: Python, FastAPI, Pydantic v2, `pydantic-settings`, Google's `google-genai` Python SDK, and Uvicorn.
- Gemini Live model default: `gemini-3.8-live`, selected by the backend.
- `GEMINI_API_KEY` is server-only. Use `.env.example` as the template; `.env` is local and must remain untracked.
- Follow the active task's requested verification scope. Do not broaden work into unrelated features.

## Read these before implementation

- [Product and system design](docs/design.md)
- [Frontend design](docs/frontend-design.md)
- [Backend design](docs/backend-design.md)
