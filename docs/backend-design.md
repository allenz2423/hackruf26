# Backend Design

**Project:** Screen Tutor  
**Status:** Backend API contract and implementation  
**Service:** Python, FastAPI, Pydantic, SQLite

## Purpose and Scope

The backend owns all model-provider integration: it protects the long-lived Gemini API key, selects models, defines instructions and tools, and maintains each Live session. The browser captures and previews media locally, then relays sampled frames and enabled microphone audio through a same-origin WebSocket. The backend forwards them in memory and does not record live media, transcripts, or conversations.

The class library stores classes, uploaded course materials, extracted text, lecture transcripts, generated study materials, quiz attempts, and flags in SQLite. Original uploaded files live in private local storage outside the static web root. Local/demo row ownership is scoped by `LOCAL_OWNER_ID`, a stable UUID configured through the environment.

## Stack

- Python 3.12+
- FastAPI and Uvicorn
- Pydantic v2 and `pydantic-settings`
- Google's `google-genai` Python SDK
- SQLite through `aiosqlite`
- SymPy for bounded calculator execution
- Local private file storage under `backend/.local/private_uploads` by default
- In-process FastAPI background tasks for hackathon upload processing and generation jobs

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Server-only credential for Live sessions and content processing |
| `GEMINI_MODEL` | No | Live model, defaults to `gemini-3.8-live` |
| `GEMINI_TEXT_MODEL` | No | Text extraction and generation model, defaults to `gemini-3.8-flash` |
| `APP_ORIGINS` | Deployment-dependent | Allowed browser origins |
| `DEMO_GATE_TOKEN` | Production | Optional private bearer token gate for session-ticket/tool endpoints; required in production |
| `LIVE_SESSION_RATE_LIMIT`, `LIVE_SESSION_RATE_WINDOW_SECONDS` | No | Session-ticket issuance limits |
| `LOCAL_OWNER_ID` | No | UUID owner namespace for SQLite rows |
| `SQLITE_PATH` | No | SQLite database path, defaults to `backend/.local/screen_tutor.sqlite3` |
| `STORAGE_DIR` | No | Private upload storage path |
| `FILE_URL_SIGNING_SECRET` | Yes | HMAC secret for short-lived private file links |
| `LEAN_TOOL_ENABLED` | No | Enables the local Lean proof-check tool |
| `MAX_UPLOAD_MB`, `MAX_UPLOADS_PER_CLASS`, `MAX_LECTURE_MINUTES` | No | Course-material limits |

## Request Policy

Development API and WebSocket requests are origin-checked and use `LOCAL_OWNER_ID` for class-library ownership. Production must configure a high-entropy `DEMO_GATE_TOKEN` for session-ticket and tool endpoints. The backend never accepts `owner_id` from the client.

Class-library routes always scope reads and writes to the configured owner UUID. This keeps the SQLite schema ready for future multi-owner deployments without depending on an external account service in the hackathon build.

## HTTP API

- `GET /healthz`
- `POST /api/live-sessions` with `{}` or optional `{"class_id":"<uuid>"}`; returns a one-use connection ticket and expiry
- `WS /api/live` using the ticket as a WebSocket subprotocol
- `POST /api/tools/calculate`
- `POST /api/tools/verify-lean`
- `GET|POST /api/classes`
- `PATCH|DELETE /api/classes/{class_id}`
- `POST|GET /api/classes/{class_id}/uploads`
- `GET|PATCH|DELETE /api/uploads/{upload_id}`
- `POST /api/uploads/{upload_id}/retry`
- `GET|PUT /api/classes/{class_id}/syllabus`
- `POST|GET /api/classes/{class_id}/study-materials`
- `POST /api/classes/{class_id}/assessments`
- `GET /api/classes/{class_id}/assessments`
- `GET /api/assessments/{assessment_id}`
- `GET|DELETE /api/study-materials/{material_id}`
- `POST /api/study-materials/{material_id}/attempts`
- `POST /api/study-materials/{material_id}/flags`

Errors use `{"code": "...", "message": "...", "request_id": "..."}` with user-safe messages.

## Live Session Flow

1. The student starts screen sharing explicitly in the browser.
2. The frontend calls `POST /api/live-sessions`; the backend checks origin, access policy, rate limit, and optional class ownership, then issues a random in-memory ticket that expires after 45 seconds and can be consumed once.
3. The browser opens same-origin `WS /api/live` and presents the ticket as a WebSocket subprotocol. The backend checks the origin and consumes the ticket before opening the session.
4. The backend opens a Live API connection with its server-only key and fixed server-owned model, instructions, class context, modalities, and tool declarations.
5. The browser sends typed questions, JPEG frames (at most one per second), and enabled 16 kHz PCM audio. The backend forwards them in memory and streams audio/transcript events back. No media, transcript, or session history is logged or persisted.

If `class_id` is supplied, the backend checks ownership in SQLite and builds class context from ready uploads. The client cannot choose the provider, model, system instructions, modalities, or tool declarations. Tickets contain no provider credential and do not grant direct model access.

### WebSocket events

The browser may send only `text`, `audio`, `audio_end`, `frame`, and `annotation_result` events. Event payloads are size-limited and validated; frame payloads must be JPEG and are rate-limited. The backend emits `ready`, `assistant`, `annotation_request`, and sanitized `error` events. WebSocket media stays in active session memory only.

## Approved Live Tools

- `google_search`: provider-managed grounding, configured by the backend.
- `calculate_expression`: evaluated by the backend's bounded symbolic parser.
- `verify_lean_proof`: checked by the backend's bounded Lean runner when enabled.
- `add_annotations`: the backend binds the request to the latest forwarded frame; the browser validates and renders temporary marks over the in-page preview, then returns a bounded result.

No tool may click, type, control the desktop, run shell commands, or draw outside the web page.

## SQLite Data Model

The schema in `backend/app/services/sqlite_schema.sql` defines:

- `course_classes`
- `course_notes`
- `study_materials`
- `study_material_sources`
- `study_material_attempts`
- `uploaded_assessments`
- `study_material_flags`

Class deletion cascades through notes, generated materials, attempts, and related records. Original files are stored privately and served only through short-lived signed URLs after ownership validation.

## Processing Jobs

Uploads are validated by extension, signature, size, and kind. Text files, DOCX, PPTX, and text-based PDFs are parsed locally where possible. Images, scanned PDFs, and lectures use Gemini content processing. Lecture transcripts and generated study materials are saved in SQLite and reused.

Background jobs are in-process for the hackathon build. A restart can interrupt active processing, so production should use a durable queue before running multiple instances or workers.

## Security Boundaries

- Keep `GEMINI_API_KEY` server-side only.
- Do not log tickets, authorization headers, API keys, uploaded content, extracted text, transcripts, screen media, or submitted proof text.
- Live media and transcripts may transit the backend only in active session memory; do not record, buffer to disk, or persist them.
- Apply origin checks, request-size limits, and rate limits.
- Keep uploaded files outside the static web root.
- Use HTTPS outside localhost.
- SQLite is configured for a single backend process; use a shared database before multi-instance deployment.

## Acceptance Criteria

- The frontend can request and consume a one-use session ticket without receiving a provider credential, model name, or provider SDK configuration.
- The backend owns the Live connection, model, instructions, class context, approved tools, and tool execution.
- The backend initializes SQLite automatically and scopes class-library data to `LOCAL_OWNER_ID`.
- A student can create classes, upload notes, generate study materials, and submit attempts through the FastAPI API.
- Screen/audio streams transit the backend only in active session memory and are never stored by it.
- Uploaded files and generated study materials can be deleted by the student.
