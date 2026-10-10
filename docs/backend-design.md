# Backend Design

**Project:** Screen Tutor
**Status:** Backend API contract and implementation
**Service:** Python and Pydantic

## Purpose and scope

The backend exists to protect the long-lived Gemini API key and issue short-lived Gemini Live credentials to the browser. The browser connects directly to the Gemini Live API so real-time screen and audio streams do not pass through or get stored by Screen Tutor's server. The server fixes the Live model's approved tool set and provides bounded calculator and Lean proof-check endpoints; Google Search runs as Gemini's built-in grounding tool.

For the MVP, the backend does not receive screen frames, microphone audio, tutor audio, transcripts, or annotation data. It does not proxy the Live WebSocket.

The backend also runs the **class library**: it stores each student's classes and uploaded course materials (notes, documents, syllabi, lecture slides, and video/audio lectures), extracts text and transcribes lectures with the Gemini API, and generates quizzes, practice tests, study guides, and flashcards from them. Supabase Auth handles browser sign-in; SQLite stores backend application data, and private local storage holds original files.

## Backend stack

- Python 3.12+
- FastAPI for a small HTTP API and serving the built frontend on the same origin.
- Pydantic v2 for request, settings, and response validation.
- `pydantic-settings` for environment-based configuration, including local `.env` loading.
- Google's `google-genai` Python SDK to provision ephemeral Live API tokens.
- SymPy for exact numeric arithmetic and bounded symbolic simplification; user expressions are converted from a small Python AST allowlist rather than passed to SymPy's string parser.
- Uvicorn as the ASGI server.
- Supabase Auth for browser identity and sessions. The verified Supabase user UUID is stored as text in SQLite for per-user ownership checks. Application schema is defined in `backend/app/services/sqlite_schema.sql` and initialized on startup.
- SQLite through `aiosqlite`, using one connection serialized within the backend process, foreign-key enforcement, and WAL journaling. This is a single-instance hackathon setup; do not run multiple workers against the same SQLite file.
- Private local file storage for the single-instance hackathon backend (`backend/.local/private_uploads` by default), with short-lived signed download URLs. Deployments on ephemeral hosts need a persistent private volume or a private object-storage adapter.
- In-process FastAPI `BackgroundTasks` for slow upload and generation work in this hackathon build. Jobs are not durable across restarts; use a persistent queue such as RQ or Celery before multi-instance or production deployment.
- File parsers: `pypdf` for PDFs, `python-docx` for DOCX, `python-pptx` for PPTX. Images, scanned PDFs, and video/audio go to Gemini.

### SQLite application schema

The schema file defines classes, course notes, uploaded assessments, generated study materials, source-note links, completed attempts, and student error flags. Supabase Auth owns accounts and sign-in sessions; its verified UUID is stored as `TEXT` in SQLite because SQLite cannot enforce foreign keys across the Auth service boundary. Class deletion cascades through notes, generated materials, and attempts. Composite class keys prevent source-note links from crossing classes; backend validation checks citations inside generated JSON. Deleting a source note removes generated materials derived from it. SQLite stores storage keys, extracted text, lecture transcript segments, syllabus outlines, and generated study data. Original files live in private storage outside the static web root. Live screen/audio media, live-session transcripts, annotation events, and tutoring conversations are not stored.

The former PostgreSQL migrations are retained under `docs/archive/postgresql-migrations/` as historical records only. They are not applied by the app or Supabase CLI. There is no automatic import from a Supabase Postgres database: a new SQLite file starts empty.

Uploaded assessments store parsed questions, detected student responses, grading mode, confidence, and results. The backend accepts private test uploads, extracts questions and answer keys, and processes completed tests in a background task. A blank test is stored as an ungraded reference. A completed test uses a complete detected answer key when available; otherwise, the backend estimates against ready course notes that are not themselves uploaded assessments. Estimated grades include per-question source citations and an overall confidence. If responses are unreadable, notes are unavailable, or grading is uncertain, the assessment is marked `needs_review` without a score. Generated practice quizzes and exams remain separate `study_materials` and can cite an imported test's course-note row as a source.

### Supabase Auth sessions

Supabase Auth is the source of truth for browser sign-in sessions. It stores and manages those records in its protected session store and issues a short-lived access-token JWT plus a rotating refresh token. Do not create a second SQLite user-session table, copy refresh tokens into application tables, or access Supabase's internal session store directly; use the Supabase Auth client/API for sign-in, refresh, and sign-out. SQLite stores the verified Supabase user UUID as the class owner ID; email and login credentials remain in Supabase Auth.

| Event | Session behavior |
| --- | --- |
| Sign-in | Supabase Auth creates the login session and returns access and refresh tokens to the browser Auth client. |
| API request | The browser sends the current access JWT to FastAPI; FastAPI verifies it and uses the verified `sub` UUID as the owner. |
| Token refresh | The Auth client exchanges the current refresh token for a new access/refresh pair; application tables are not involved. |
| Sign-out | The Auth client asks Supabase Auth to revoke the session and clears its local session state. An already-issued access JWT can remain valid until its `exp` claim. |

The browser uses the Supabase JavaScript Auth client to sign in and refresh its session. For every protected FastAPI request, it sends the current access token as `Authorization: Bearer <access-token>`. FastAPI verifies the JWT signature and validates its issuer, audience, expiration, and `sub` claim using the project's Supabase signing keys or a supported Supabase verifier. It treats the verified `sub` UUID as the caller identity; it never trusts a user ID sent in a request body or URL. The frontend's publishable key is safe for browser use; Supabase secret/service-role keys remain server-only.

Email/password signup does not require email confirmation for the hackathon. Local Supabase CLI development sets `auth.email.enable_confirmations = false` in `supabase/config.toml`; this setting applies only to the local Supabase stack. For the hosted project, turn off **Confirm email** in the Email provider settings under **Authentication → Sign In / Providers**, or set `mailer_autoconfirm: true` through the Supabase Management API. With confirmation disabled, Supabase treats signup email addresses as confirmed and returns an authenticated session immediately. The frontend can use that session for its normal signed-in flow. This is a project-level Auth setting, not a FastAPI endpoint or database migration.

The Supabase Auth project settings control access-token lifetime and supported session timeouts. Access tokens default to a one-hour lifetime; sessions otherwise remain active until sign-out or another configured termination event. Keep the default JWT lifetime unless deployment needs justify a change, and choose session timeout settings before public deployment. If a future high-risk operation requires immediate session revocation, additionally check the token's `session_id` against Supabase Auth session state for that operation; do not make every request depend on querying internal Auth tables.

All class-library data goes through FastAPI; each query scopes data to the verified owner UUID. SQLite schema changes belong in `backend/app/services/sqlite_schema.sql` and are initialized when the backend starts. Set `SQLITE_PATH` to choose the database file. Keep that file and `STORAGE_DIR` on persistent private storage and back them up together. Supabase's Postgres Data API and database connection are not used by the backend application.

This login session is separate from a tutoring Live session. The browser owns the Gemini Live connection and any transient resume handle. Do not store Gemini ephemeral access tokens, Live session handles, media, transcripts, annotations, or tutoring conversations in Supabase Auth or SQLite.

The frontend can be plain HTML/CSS/JS bundled by Vite. In development, Vite proxies `/api` to FastAPI; in deployment, FastAPI serves the built static files or the host serves both behind one same-origin URL.

## Responsibilities

### Backend owns

- Load and validate `GEMINI_API_KEY` and the server-selected `GEMINI_MODEL`.
- Verify Supabase Auth access tokens and derive the caller's user ID from verified claims.
- Create ephemeral tokens with short session-start and token expiration windows.
- Lock token constraints to the approved Live model and the app's fixed session configuration, Google Search grounding, and function declarations where supported by the API.
- Execute only the bounded `calculate_expression` and `verify_lean_proof` functions through their tool endpoints; the browser handles Live tool calls and annotation rendering.
- Apply request-size limits, origin checks, rate limits, and sanitized errors.
- Serve the frontend and a health endpoint, if the selected deployment uses a same-origin host.
- Supabase Auth sign-in/session management, and scoping every class, upload, and study material to the verified owner's Supabase user ID.
- Receiving course-material uploads, validating type and size, and storing the original file.
- Extracting text from documents and slides, transcribing video/audio lectures with timestamps, and parsing syllabi into a topic/exam outline.
- Generating quizzes, practice tests, study guides, and flashcards from a class's materials with a structured-output schema, and validating the result before saving it.
- Scoring quiz and practice-test attempts.
- Adding the selected class's materials (or a summary of them) to the Live session's system instructions when issuing a token for that class.

### Backend does not own

- Browser screen selection or media capture.
- Live audio/video forwarding, model response playback, or annotation rendering.
- General purpose model proxying, screenshot persistence, or Live conversation storage.
- Any tool that clicks, types, or controls the student's desktop.

## Request flow

```mermaid
sequenceDiagram
    actor Student
    participant Browser as HTML/CSS/JS client
    participant App as FastAPI + Pydantic
    participant Google as Gemini token service
    participant Live as Gemini Live API

    Student->>Browser: Press Share and select screen
    Browser->>App: POST /api/live-token
    App->>Google: Provision constrained ephemeral token
    Google-->>App: Token resource + expiration
    App-->>Browser: Validated short-lived token response
    Browser->>Live: Connect using token and fixed Live config
    Browser->>Live: Send frames, optional mic audio, and questions
    Live-->>Browser: Audio, transcript, search grounding, and function calls
    Browser->>App: POST /api/tools/calculate or /api/tools/verify-lean for custom math calls
    App-->>Browser: Bounded calculation or Lean verification result
    Browser->>Live: Function response with matching call ID
```

The server token request occurs after the student begins the session. It is independent of the media stream. The browser sends media directly to Gemini and handles all Live events.

## HTTP API

### Frontend integration status

The frontend on `main` was reviewed at commit `06b4493`. Its shared `apiFetch` client in [`frontend/src/api.js`](../frontend/src/api.js) attaches the current Supabase access token and calls the Live token, calculator, class, upload, syllabus, study-material, attempt, and flag routes. The tutor passes its selected class ID to the Live token request, and Vite proxies same-origin `/api` calls to FastAPI. This class-ID request is currently supported by the backend, although the repository's shared integration contract says the frontend should send `{}`; align that contract before removing either behavior. Vite does not proxy `/healthz`, but `checkBackendHealth()` requests that path, so the development health check hits Vite rather than FastAPI and can report the backend as unreachable. Add a `/healthz` proxy or use FastAPI's same-origin static serving for local verification.

The backend implements `GET /healthz`, `POST /api/live-token`, `POST /api/tools/calculate`, `POST /api/tools/verify-lean`, and the class-library routes below. With `SUPABASE_URL` configured, it verifies Supabase access-token signatures and claims and requires the verified user for API calls. Without Supabase configured, the standalone token, calculator, and Lean proof-check endpoints require `DEMO_GATE_TOKEN`; protected requests return `503` if neither authentication option is configured. Class-library routes require Supabase Auth and private storage; SQLite is initialized automatically at startup.

Before protected API calls can work, the frontend must initialize Supabase Auth and send `Authorization: Bearer <current-access-token>` on each `/api/*` request. Supabase Auth itself owns sign-in, refresh, and sign-out; FastAPI does not add duplicate auth-session endpoints. The tutor screen must also pass its selected `class_id` to `/api/live-token` if the model should receive class materials.

The current `GeminiLiveController` dispatches `calculate_expression` to `/api/tools/calculate`, returns its response using the original function-call ID, and handles `add_annotations` locally. It does not yet dispatch `verify_lean_proof`; that call currently receives an `unsupported_tool` response, so Lean verification is not usable from the UI until the frontend adds a `verifyLeanProof` API helper and dispatch branch. `google_search` stays inside Gemini and does not use the custom function-call path.

### Shared API conventions

- Use same-origin `/api` requests and JSON unless the endpoint is a file upload.
- The API's CORS allow-list must permit `GET`, `POST`, `PATCH`, `PUT`, `DELETE`, and `OPTIONS` for the configured frontend origins; do not enable wildcard credentialed CORS.
- Class-library routes require a valid Supabase access token. Token and tool routes use the same verified Supabase identity when configured, or the private demo gate when Supabase is not configured. `GET /healthz` is public. The short-lived signed file URL is a bearer link and does not require a Supabase header. FastAPI derives the owner from the verified JWT and checks ownership for every class, upload, and material ID. Never accept `owner_id` from the client.
- IDs are UUID strings. Enum values in requests and responses use lowercase `snake_case`; the frontend can map them to its title-case labels (for example, `lecture_video` → “Lecture”).
- A non-UUID `class_id` in a route path returns `400` with `code: "invalid_class_id"`; create or sync the class and its source uploads in the backend before calling class-library generation routes. A local demo class such as `math-151` is not a backend class, so this request is rejected before any Gemini call.
- Collection responses use `{"items": [...]}`. Mutations return the created or updated resource unless the contract says `204 No Content`.
- Errors use `{"code": "...", "message": "...", "request_id": "..."}`. Messages are safe to show to a student and do not include raw upstream errors or submitted file/text contents.
- Return `404` for both missing and non-owned resources so IDs cannot be used to discover another student's data.

### `POST /api/live-token`

When `SUPABASE_URL` is configured, this requires a valid Supabase access token in the `Authorization: Bearer <access-token>` header. In no-Supabase demo mode, the configured `DEMO_GATE_TOKEN` is required and `class_id` cannot be used. No caller-controlled model or tool configuration is accepted. The request body is empty (or `{}`), or contains only an optional `class_id`, so the server always chooses the model and configuration. When `class_id` is given, the server checks that the verified Supabase user owns the class and builds the session's system instructions from that class's materials on the server side; the client cannot supply its own instructions.

**Success response**

```json
{
  "access_token": "ephemeral-token-value",
  "model": "gemini-3.8-live",
  "expires_at": "2026-10-10T18:30:00Z"
}
```

`access_token` is the short-lived token resource returned by Google's token provisioning API. It is a bearer credential: return it only to the requesting app session, never log it, persist it, or send it to analytics. The browser passes it to the Google GenAI JavaScript SDK for a Live API session.

**Pydantic contracts**

- `LiveTokenRequest`: optional `class_id` only; reject unexpected fields so callers cannot select a different model or expand tool access.
- `LiveTokenResponse`: required `access_token`, literal/configured `model`, and timezone-aware `expires_at`.
- `ErrorResponse`: stable error code and short user-safe message; never include upstream credentials or raw exception text.

**Status codes**

| Status | Meaning |
| --- | --- |
| `200` | Token issued |
| `400` | Malformed request |
| `401` | App authentication required or expired |
| `403` | Request origin or policy rejected |
| `429` | Token issuance rate limit reached |
| `503` | Server is missing Gemini configuration |
| `502` | Gemini token provisioning failed |

### Approved Live tools

The backend creates one immutable tool set inside the server-owned `LiveConnectConfig` and locks it into the ephemeral token where supported:

| Tool | Type | Purpose and execution |
| --- | --- | --- |
| `google_search` | Gemini built-in grounding tool | Verify current or uncertain facts. Gemini performs the search and returns grounded response content and source metadata over the direct Live connection; there is no backend search proxy or search-results database. |
| `calculate_expression` | Blocking custom function declaration | Evaluate exact arithmetic or bounded symbolic expressions. The Live API sends the function call to the browser; the browser calls `POST /api/tools/calculate` and returns the API result to the same Live session using the original function-call ID and name. Gemini waits for the response before continuing. |
| `verify_lean_proof` | Blocking custom function declaration | Check whether a formal Lean 4 proposition follows from a supplied tactic proof using the pinned Mathlib project. The browser calls `POST /api/tools/verify-lean` and returns `verified`, `rejected`, or `timed_out` plus bounded diagnostics. Acceptance checks only the formal proposition, not whether it accurately represents the student's natural-language question. |
| `add_annotations` | Blocking custom function declaration | Request a temporary mark over the matching shared-screen frame. This remains the only visual action. The browser validates and renders the call over its in-page preview, then replies to the matching function call. Gemini waits for the response before continuing. |

The function-call declarations and their schemas are fixed by the backend. The caller cannot add tools, change descriptions or schemas, or send tool configuration with `/api/live-token`. Google Search grounding is for fact checking and current information; it does not grant general browsing, arbitrary URL fetching, or application control. The Live service performs Search internally. It does not send search queries or results through the Screen Tutor backend.

The frontend receives grounded answer parts and any grounding metadata directly from Gemini. A Live `serverContent` event may include `groundingMetadata`; accumulate its `groundingChunks` in order across the turn because later streamed metadata can contain only newly added chunks. Use `groundingSupports[].groundingChunkIndices` to associate supported text segments with their source chunks. Show source titles and links using the source URIs supplied by Gemini. Follow the API's grounding attribution requirements, including its Search Suggestions entry point when one is returned. If metadata is absent, do not invent citations from spoken text. Search metadata and citations remain in frontend session state only and are discarded when the tutoring session ends.

### `POST /api/tools/calculate`

This endpoint is the executor for the `calculate_expression` Live function. The browser calls it only after receiving that function call from Gemini, then sends the returned `result` object back through the same Live session. It accepts a JSON body with one expression and does not accept model names, tool declarations, or instructions.

**Request**

```json
{
  "expression": "factor(x**2 - 1)"
}
```

**Success response**

```json
{
  "result": "(x - 1)*(x + 1)"
}
```

The result is an exact numeric or symbolic string (up to 512 characters), so fractions and radicals do not lose precision through JavaScript floating-point conversion. Numeric literals are converted to exact SymPy rationals. The parser supports named symbols (up to 16), `pi`, `e`, parentheses, unary `+`/`-`, `+`, `-`, `*`, `/`, `//`, `%`, and powers using `**`. It supports `sqrt`, `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sinh`, `cosh`, `tanh`, `exp`, `log`, `abs`, `simplify`, `factor`, and `expand`. Use `simplify(expression)`, `factor(expression)`, or `expand(expression)` for those explicit symbolic transformations; ordinary arithmetic remains exact. This provides symbolic expression manipulation, but does not solve equations or accept arbitrary SymPy/Python syntax.

Inputs are limited to 256 characters, 64 AST nodes, 32 AST levels, and 16 distinct symbols. Numeric literals and all-numeric intermediate results must be finite and within magnitudes `1e-100` through `1e100`, with zero allowed; numeric exponents are limited to absolute value 100. Results are capped at 128 operations and 512 characters. Function calls are accepted only when the function name is in the explicit allowlist, with no keyword arguments. Attribute access, imports, strings, containers, and all other Python syntax are rejected. The service constructs SymPy expressions from the validated AST; it does not pass user strings to SymPy's `parse_expr` or execute submitted code.

The request uses the same configured app authorization gate and allow-listed Origin check as token issuance. With Supabase configured, the gate is a verified access token; otherwise it is `DEMO_GATE_TOKEN`. It has a separate per-IP, per-process rate limit, with defaults in `.env.example`. The response never echoes the expression; the endpoint does not log or store expressions or results.

**Failure responses**

| Status | Meaning |
| --- | --- |
| `400` | Invalid syntax, unsupported expression, division by zero, or an expression limit was exceeded |
| `401` | App authentication required or expired |
| `403` | Request origin or policy rejected |
| `413` | Request body exceeds the configured limit (default 1,024 bytes) |
| `429` | Calculator rate limit reached |

Errors use the shared `{ "code", "message", "request_id" }` response shape. Calculator-specific codes are `invalid_expression` and `calculator_rate_limited`; Lean-specific codes are `invalid_lean_input`, `lean_rate_limited`, `lean_busy`, `lean_disabled`, and `lean_unavailable`. Shared request codes include `invalid_request`, `request_too_large`, `authentication_required`, and `origin_not_allowed`.

Live reports custom calls in `toolCall.functionCalls[]`. The calculator, Lean verifier, and annotation function declarations use `behavior: BLOCKING`, so Gemini waits for their responses. The browser should dispatch each call only by its approved name, then return the matching `functionResponses[]` with the original function-call ID and name. If one event contains several calls, return a response for each one in the same response message. Track call IDs so a cancellation event can stop outstanding calculator or Lean HTTP requests and prevent late responses from being sent. Never execute an unknown function name. For failed HTTP calls, return only the sanitized `code` and `message` as a short structured function error. Use a generic tool-unavailable code when the request itself fails; do not retry in a loop. For calculator success, return `{ "result": "37" }`; for Lean success or proof rejection, return its `{ "status", "diagnostics" }` response. Handle `add_annotations` locally. `google_search` is Gemini's built-in tool and does not use this custom function-call path. Live function calls are not executed automatically by the API; the client must execute and answer them.

The browser's execution outline is:

```javascript
const response = await fetch("/api/tools/calculate", {
  method: "POST",
  headers: {
    "Authorization": `Bearer ${appAccessToken}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ expression: functionCall.args.expression }),
});

const result = await response.json();
session.sendToolResponse({
  functionResponses: [{
    id: functionCall.id,
    name: functionCall.name,
    response: response.ok
      ? result
      : { error: { code: result.code, message: result.message } },
  }],
});
```

### `POST /api/tools/verify-lean`

This endpoint executes the `verify_lean_proof` Live function using the backend's pinned Lean 4/Mathlib project. The model supplies a one-line formal proposition and its tactic proof body; the backend wraps them in a theorem declaration, so callers cannot supply imports or top-level Lean commands.

**Request**

```json
{
  "proposition": "forall n : Nat, n + 0 = n",
  "proof": "induction n with\n| zero => rfl\n| succ n ih => simp [ih]"
}
```

`proof` omits the leading `by`. The server rejects command, metaprogramming, `sorry`, `admit`, and comment syntax. Both fields have strict character limits. The local Lean process has a wall-clock timeout, CPU and file-size limits where `prlimit` is available, and bounded concurrency and output.

**Response**

```json
{
  "status": "verified",
  "diagnostics": "Lean accepted the formal proposition and proof."
}
```

`status` is `verified`, `rejected`, or `timed_out`. Rejected proofs return truncated Lean diagnostics so the model can revise its proof. A timeout is not reported as a failed theorem. Lean verifies only the supplied formal proposition; it cannot confirm that the proposition faithfully expresses the student's natural-language problem.

The request uses the same app authorization and Origin checks as calculator calls. Defaults are 3 requests per authenticated user (or socket peer in demo mode) per minute, 12 seconds per check, and a 65,536-byte body limit. Proof source and diagnostics are not logged or persisted. The local runner is suitable for a controlled, single-instance hackathon deployment; it is not a full OS sandbox. Keep it behind authenticated access and add process isolation before exposing proof checks to untrusted public traffic.

If `LEAN_TOOL_ENABLED=true` (the default), startup requires the configured Lean/Mathlib project to be installed and ready. Set it to `false` to omit the tool from new Live token configurations; the endpoint then returns `503`.

Live function-call handling follows the same call-ID contract as calculation: dispatch `verify_lean_proof` to this endpoint and return the JSON response to the same Live session under the original function-call ID and name. For an HTTP error, send a short structured error with the API's safe `code` and `message`; do not retry automatically.

Use the application's current authorization token in the example above; do not use the Gemini ephemeral token for the Screen Tutor HTTP API. Check the function name against the approved set before executing it. Handle `add_annotations` locally and leave `google_search` execution to Gemini's built-in tool service.

### Class library endpoints

These routes provide the persistent data currently represented by frontend-local arrays. Class colors and initials are presentation details, not stored class data; the frontend derives them from the class name unless a later product decision makes them user-editable.

#### Classes

`ClassSummary` is:

```json
{
  "id": "9f66d1a2-4b9f-4ae2-bc26-38df0fb6b4dd",
  "name": "Math 151",
  "created_at": "2026-10-10T18:00:00Z",
  "updated_at": "2026-10-10T18:00:00Z",
  "upload_count": 3,
  "material_count": 2
}
```

| Method and path | Contract |
| --- | --- |
| `GET /api/classes` | Return `{"items": [ClassSummary, ...]}` for the caller, newest or recently updated first. Empty accounts return an empty array; do not seed the frontend's demo classes on the server. |
| `POST /api/classes` | Accept `{"name": "Math 151"}`; trim and validate a non-empty name up to `MAX_CLASS_NAME_LENGTH` characters (100 by default). Return `201` and the new `ClassSummary`. |
| `PATCH /api/classes/{class_id}` | Accept `{"name": "Math 152"}` and return the updated `ClassSummary`. Reject unknown fields. |
| `DELETE /api/classes/{class_id}` | Delete the class and cascade its database rows and private files. Return `204`. |

#### Uploads and course materials

`POST /api/classes/{class_id}/uploads` accepts `multipart/form-data`. For the current file picker, send one `file` and optional lowercase `kind` and `title` fields. If the paste-text UI is connected, send `text`, `title`, and optional `kind` instead of `file`; exactly one of `file` or `text` is allowed. A missing file `title` defaults to the original filename. The backend validates the extension, checks a file signature for supported binary formats, enforces the configured size limit regardless of client-side checks, writes original bytes to private storage, creates a note row, queues processing, and returns `202 Accepted`.

```json
{
  "id": "0ff95e83-aea8-4fe7-9d01-1f0c01afde01",
  "class_id": "9f66d1a2-4b9f-4ae2-bc26-38df0fb6b4dd",
  "title": "Week 3 notes.pdf",
  "kind": "notes",
  "source_type": "file",
  "original_filename": "Week 3 notes.pdf",
  "mime_type": "application/pdf",
  "file_size_bytes": 82731,
  "status": "processing",
  "processing_progress": 0,
  "failure_code": null,
  "created_at": "2026-10-10T18:04:00Z",
  "updated_at": "2026-10-10T18:04:00Z"
}
```

`kind` is one of `notes`, `document`, `syllabus`, `slides`, `lecture_video`, or `lecture_audio`. Map the frontend's displayed categories (“Notes”, “Document”, “Syllabus”, “Slides”, “Lecture”) to these values. The frontend's current 250 MiB check must be kept in sync with `MAX_UPLOAD_MB`; the backend limit is authoritative. The backend accepts one file or pasted-text item per request and caps each class at `MAX_UPLOADS_PER_CLASS` (100 by default; `409 class_upload_limit_reached` at the cap). Client-side byte-transfer progress is not a server status. Once upload completes, poll `GET /api/uploads/{upload_id}` for processing status; lecture processing may be labeled “transcribing” in the UI while the API status remains `processing`.

| Method and path | Contract |
| --- | --- |
| `GET /api/classes/{class_id}/uploads` | Return `{"items": [UploadSummary, ...]}` newest first. Summaries include IDs, title, kind, original filename when applicable, file size, status, processing progress, safe failure code, duration, and timestamps. |
| `GET /api/uploads/{upload_id}` | Return the summary fields plus `extracted_text`, `summary`, `key_topics`, `transcript_segments`, `syllabus_outline`, and a five-minute `file_url` with `file_url_expires_at`. The signed URL is issued only after ownership validation and is omitted for pasted text. `GET /api/uploads/{upload_id}/file` serves the original bytes through that short-lived HMAC-signed bearer URL; it does not require a Supabase header. |
| `PATCH /api/uploads/{upload_id}` | Accept any non-empty subset of `title`, `kind`, and `extracted_text`; return the updated summary or detail. Do not permit client changes to IDs, ownership, status, storage keys, progress, or generated transcript fields. |
| `DELETE /api/uploads/{upload_id}` | Remove the note, original private file, and derived materials that cite it. Return `204`. |
| `POST /api/uploads/{upload_id}/retry` | Retry a failed processing job only; return `202` with status reset to `processing` and progress reset to `0`. Return `409` if the upload is already processing or ready. |

Upload `status` is `processing`, `ready`, or `failed`. `processing_progress` is an integer from 0 to 100. `failure_code` is a safe stable code, not a stack trace. List endpoints must not return extracted text or signed file URLs; fetch detail only when the student opens an upload.

#### Syllabus outline

`GET /api/classes/{class_id}/syllabus` returns the parsed outline for the class's ready syllabus upload, or `{"note_id": null, "topics": [], "exams": []}` when none exists. If a class has more than one syllabus, return the selected/latest outline and include its `note_id` so the UI can identify the source.

```json
{
  "note_id": "0ff95e83-aea8-4fe7-9d01-1f0c01afde01",
  "topics": [{"id": "topic-1", "title": "Derivatives", "week": 3}],
  "exams": [{"id": "exam-1", "name": "Midterm 1", "date": "2026-10-28", "topics": ["topic-1"]}]
}
```

`PUT /api/classes/{class_id}/syllabus` accepts corrected `topics` and `exams` arrays for that class's syllabus and returns the saved outline. Validate IDs and lengths, and do not allow a syllabus outline to reference another student's uploads. The frontend design includes editing this outline; it is not present in the pulled UI yet.

#### Study materials and attempts

The API uses `kind: quiz | practice_exam | study_guide | flashcards`. Map the current UI labels “Quiz”, “Practice test”, “Study guide”, and “Flashcards” to those values. Generation requests use an explicit scope and options:

```json
{
  "kind": "quiz",
  "scope": {"kind": "all"},
  "options": {
    "count": 10,
    "difficulty": "mixed",
    "question_types": ["multiple_choice", "short_answer"]
  }
}
```

`scope` is one of `{"kind":"all"}`, `{"kind":"uploads","upload_ids":[...]}`, `{"kind":"topics","topics":[...]}`, or `{"kind":"exam","exam_name":"Midterm 1"}`. Every selected upload/topic/exam must belong to the requested class. `options.count` is an integer from 4 to 50; `difficulty` is `mixed`, `easier`, or `harder`; `time_limit_minutes` is an optional bounded positive integer for practice exams. For quizzes and practice exams only, optional `question_types` selects one or more of `multiple_choice`, `true_false`, and `short_answer`; the output is restricted to those kinds and includes at least one of each selected kind. When omitted, all three kinds are allowed and generated. The current Study form has no question-type selector yet. It displays `all`, `notes`, and `syllabus` for source scope, but its submit handler always sends `{"kind":"all"}`. `notes` and `syllabus` are not API scope kinds: map selected uploads to `uploads`, and provide topic or exam selections for `topics` and `exam`, or adjust the UI to match the supported scopes.

| Method and path | Contract |
| --- | --- |
| `POST /api/classes/{class_id}/study-materials` | Validate that the class has at least one ready source upload and the requested scope is non-empty. Start generation and return `202` with a `MaterialSummary` whose `status` is `generating`. |
| `GET /api/classes/{class_id}/study-materials` | Return `{"items": [MaterialSummary, ...]}` newest first. Include latest attempt score/time when available. |
| `GET /api/study-materials/{material_id}` | Return material metadata and its ready content. For quizzes and practice exams, omit answer keys and grading explanations before submission. Include citations as `{"upload_id", "page?", "slide?", "timestamp_seconds?"}`; each source must belong to this material's class and selected source set. |
| `DELETE /api/study-materials/{material_id}` | Delete the material and its attempts. Return `204`. |
| `POST /api/study-materials/{material_id}/attempts` | Accept `{"answers":{"question-id":"choice-id-or-short-answer", ...}}`; validate question IDs and answer types, score on the server, save the attempt, and return its result. The response includes `attempt_id`, `score_percent`, `correct_count`, and per-question submitted/correct answers, explanations, and sources. Short answers use Unicode-normalized, case-insensitive exact matching against server-held accepted answers; there is no semantic or AI grading. Do not accept a client-supplied score or answer key. |
| `POST /api/study-materials/{material_id}/flags` | Accept `{"question_id":"question-id","reason":"optional short note"}` and record a student report that an item may be wrong. Return `201`, or `{"status":"already_flagged"}` if this student already flagged the item. The table is created by `20261010170000_study_material_flags.sql`. |

`MaterialSummary` includes `id`, `class_id`, `kind`, `title`, `scope`, `options`, `status` (`generating`, `ready`, `failed`), `item_count`, `failure_code`, `created_at`, `generated_at`, and optional `latest_attempt: {score_percent, completed_at}`. The frontend can derive its display label from `kind` and count questions/cards from `item_count`.

Each generation call also receives a fixed, server-owned system prompt selected by `kind` from `backend/app/services/study_prompts.py`. The system prompts define the authoring role, source-grounding and citation rules, and the behavior expected for that material format. Request-specific values and source records are sent in the user content; clients cannot replace the system prompt. Gemini receives a Pydantic structured-output schema selected from the requested `kind`; the response must include a matching `format` discriminator. The schemas are distinct for `quiz`, `practice_exam`, `study_guide`, and `flashcards`, so an output for one kind cannot pass validation as another. Quiz and practice-exam question items use the same question fields, but practice exams additionally return `instructions`. The requested time limit remains authoritative in `options` and is included in the practice-exam prompt when supplied. The backend validates the generated item count exactly against `options.count` and verifies question IDs, requested question kinds, answer references, and source citations before saving.

The response content shapes are `{format: "quiz", questions: [...]}`, `{format: "practice_exam", instructions: "...", questions: [...]}`, `{format: "study_guide", sections: [...]}`, and `{format: "flashcards", cards: [...]}`. `options.count` means question count for quizzes and practice exams, section count for study guides, and card count for flashcards. Keep answer keys and scoring rubrics server-side until an attempt is submitted. Return a failed material with a safe `failure_code` so the UI can show a retry/regenerate action; never return model traces or raw exception text.

The Study page has API helpers for listing, creating, loading, and deleting generated material, submitting attempts, and flagging questions. It polls newly created materials and can load remote guides and flashcards. The remote quiz flow is connected but currently violates the response contract in several places:

- Public quiz questions contain `choices: [{"id":"...","text":"..."}]`; the backend deliberately removes `answer`, `accepted_answers`, `rubric`, and `explanation` from quiz detail responses. The frontend currently passes choice objects to a renderer that treats each choice as text and assigns every question `answerIndex: 0`. It must render `choice.text`, retain `choice.id`, and must not infer an answer key in the browser.
- The attempt request expects `answers` keyed by question ID. For multiple-choice questions the value must be the selected choice ID; for true/false and short-answer questions it must be the selected/typed answer. The current player submits the selected numeric index as a string, which the backend cannot match to a choice ID.
- The attempt response includes `score_percent`, `correct_count`, and `question_results` with each question's correctness, correct answer, explanation, and sources. The current player reads only `score_percent`, then renders local results from the fabricated `answerIndex`; remote review labels and correct-count display are therefore wrong. Remote result rendering should use `question_results`. Local/sample materials may continue to use local answer keys.
- The backend supports a `question_types` option for quiz/practice-exam requests, but the current Study form does not expose it. The current player only supports multiple-choice-style answers, so the frontend should either add true/false and short-answer rendering or request `question_types: ["multiple_choice"]` until those inputs are supported.
- Material summaries expose only `latest_attempt: {score_percent, completed_at}`. There is no endpoint to retrieve attempt history or a prior attempt's per-question results; add one only if the product needs that history after navigation or reload.
- On generation or attempt failures, the current UI can fall back to a local sample or local score while the user is signed in. Keep those states clearly marked as local and do not present them as backend-generated or backend-verified results.
- `syncClassesFromApi()` preserves browser-local classes that have not been created in the backend. This includes seeded examples and user-created classes such as Bio 170; their local IDs are not backend UUIDs, and their uploads may exist only in browser storage. Before enabling remote generation for one of these classes, create its backend class, migrate each local upload or pasted-text source through the upload API, and use the returned backend class UUID. Do not resolve a local class by name or submit generation until the class and its ready source uploads exist remotely. A request using a local ID returns `400 invalid_class_id` before it reaches Gemini.

Flashcard and guide rendering is wired to the backend content shapes (`cards: [{id, front, back, sources}]` and `sections: [{heading, body, ...}]`).

#### Imported existing assessments

The backend implements imported-assessment storage, extraction/grading, and these authenticated endpoints. They are separate from the generic course-upload flow so a test is analyzed as a submitted assessment rather than only as study notes:

| Method and path | Target contract |
| --- | --- |
| `POST /api/classes/{class_id}/assessments` | Accept one `file` as `multipart/form-data` (optional `title`), create a normal course-note source and `uploaded_assessments` row, and return `202` with a summary in `processing` state. Accept PDF, DOCX, PPTX, TXT, Markdown, PNG, or JPEG; apply the normal upload size and per-class limits. The source file remains private. |
| `GET /api/classes/{class_id}/assessments` | Return `{"items": [...]}` with summaries for assessments in the caller's class, newest first. |
| `GET /api/assessments/{assessment_id}` | Return the owned assessment's parsed questions, detected responses and answer key, grading mode, optional score/confidence, per-question grading result, status, safe failure code, review reason, and a five-minute signed URL for the original file. |

The assessment summary has `id`, `class_id`, `note_id`, `title`, `original_filename`, `status`, `has_student_responses`, `question_count`, `grading_mode`, nullable `score_percent`, nullable `grade_confidence`, nullable `failure_code`, `created_at`, `updated_at`, and nullable `processed_at`. `status` is `processing`, `ready`, `needs_review`, or `failed`. A blank test is a ready, ungraded reference (`grading_mode: not_graded`, no score). A completed test uses `answer_key` only when a key is detected for every question; if the key is missing or incomplete, grading from ready notes in the same class is labeled `class_notes_estimated` and includes confidence and citations. Unreadable responses, absent usable notes, or uncertain grading result in `needs_review` and no score; `review_reason` is included in the detail response. Processing failures use the safe `failure_code: processing_failed`. The owner may view the answer key and grading result for their own uploaded test. Never treat an imported test's key as the answer key for a separately generated quiz.

Each detail `questions` item contains `id`, `prompt`, `choices`, `points`, `student_response`, `response_status`, `answer_key`, and `answer_key_status`. The `grading_result.questions` array contains `question_id`, `earned_points`, `possible_points`, `correct_answer`, `feedback`, `confidence`, and `sources` (empty for key-based grading; class-note citations for estimated grading). Blank responses receive zero points. The worker updates the linked `course_notes` record too, so its extracted text is available as a normal selected source for future study-material generation. It excludes other uploaded assessments from the note context used for estimated grades.

Imported-test source rows are normal course notes and can be selected through the existing `uploads` study-material scope. Generated quizzes and practice exams remain separate materials with immutable content and source links. The current frontend has no assessment API helpers, import action, assessment list/detail view, or processing/result UI; its generic Library uploader sends these files through `/api/classes/{class_id}/uploads`, which does not invoke the imported-assessment grading worker. Add an assessment-specific upload/list/detail flow using the endpoints above. Poll detail while `status` is `processing`, and render `ready`, `needs_review`, and `failed` distinctly. The available API is upload, list, and detail only; there is no correction, manual-grade, or delete assessment endpoint at present.

### Upload processing

| Kind | Processing |
| --- | --- |
| Notes / documents | Parse text directly (TXT/MD, DOCX, text-based PDF); send images, handwriting, and scanned PDFs to Gemini for text extraction |
| Syllabus | Extract text, then ask Gemini for a structured outline (topics, weeks, exams with dates and topics) and save it |
| Slides | Extract text per slide with `python-pptx`/`pypdf`; send image-heavy slides to Gemini |
| Video / audio lectures | Upload to the Gemini Files API, ask for a timestamped transcript, summary, and key topics (including what's written on the board or slides for video), then save segments |

Each job updates the upload's `status` (`processing → ready | failed`) and progress so the frontend can poll. Jobs run as in-process FastAPI background tasks and can be interrupted by a server restart; a failed upload can be retried through the API. Lecture duration is checked against `MAX_LECTURE_MINUTES` using the generated transcript timestamps, so an over-limit lecture is marked failed after transcription. Large lectures are the slowest and most expensive step, so transcribe each one once and reuse the transcript for every later generation and tutoring session. The original lecture file is sent to Gemini Files API temporarily and deleted after processing when possible; only the course transcript and derived summary/topics are kept in the app database.

Generation sends the selected or syllabus-scoped uploads' text (capped at 250,000 characters per request) to `GEMINI_TEXT_MODEL`, a standard Generate Content model separate from Live, with a strict Pydantic response schema selected by material type. The default is `gemini-3.8-flash`. It rejects output with a mismatched format tag, missing answers, the wrong item count, a bad structure, duplicate item IDs, or citations outside the selected class sources. If a class's text exceeds the request cap, the backend truncates source excerpts to the cap; chunked retrieval is a future improvement.

### `GET /healthz`

Returns only service health, for example `{"status":"ok"}`. It must not disclose environment variables, token status, or Gemini account details.

## Token provisioning policy

The backend creates ephemeral tokens through the Gemini API's Live token provisioning feature using the Google GenAI Python SDK. The token request should:

- Use `GEMINI_API_KEY` only on the server.
- Set `uses` to one for a new Live session and use short, explicit expiration values allowed by the API.
- Constrain the model to `GEMINI_MODEL` (default `gemini-3.8-live`).
- Constrain supported Live configuration to audio responses, output transcription, screen input, session resumption if enabled, the built-in `google_search` tool, and the server-approved `calculate_expression`, optional `verify_lean_proof`, and `add_annotations` functions.
- Reject or ignore client attempts to provide a different model, arbitrary instructions, or additional tools.

Keep the token configuration in one server-side module so frontend and backend settings do not drift. The browser must send the matching setup configuration when it connects. Verify all constraint fields against the current [ephemeral token documentation](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens) during implementation.

## Configuration and secrets

Read these values from the process environment, with `pydantic-settings` loading the ignored local `.env` file during development:

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Server-only credential for token provisioning |
| `GEMINI_MODEL` | No | Allowed Live model; defaults to `gemini-3.8-live` |
| `APP_ORIGINS` | Deployment-dependent | Allowed browser origins for API requests |
| `TOKEN_RATE_LIMIT` / `TOKEN_RATE_WINDOW_SECONDS` | No | Token requests per socket peer/window; defaults to 5 per 60 seconds per process |
| `CALCULATOR_RATE_LIMIT` / `CALCULATOR_RATE_WINDOW_SECONDS` | No | Calculator requests per socket peer/window; defaults to 30 per 60 seconds per process |
| `LEAN_TOOL_ENABLED` | No | Advertise the Lean proof-check tool; defaults to `true` and requires the Lean/Mathlib project at startup |
| `LEAN_RATE_LIMIT` / `LEAN_RATE_WINDOW_SECONDS` | No | Lean proof checks per user/window; defaults to 3 per 60 seconds per process |
| `LEAN_TIMEOUT_SECONDS` | No | Maximum wall time per proof check; defaults to 12 seconds |
| `LEAN_REQUEST_MAX_BYTES` | No | Maximum request body for proof checks; defaults to 65,536 bytes |
| `LEAN_PROJECT_DIR` | No | Lean/Lake project containing the pinned Mathlib dependency; defaults to `backend/lean` |
| `LEAN_LAKE_EXECUTABLE` | No | Lake executable name or path; defaults to `lake` |
| `GEMINI_TEXT_MODEL` | No | Standard Gemini model for text extraction, transcription, syllabus parsing, and study-material generation; defaults to `gemini-3.8-flash` |
| `SUPABASE_URL` | Yes, for the class library and signed-in API | Supabase project URL; used to identify the Auth issuer and signing-key endpoint |
| `SUPABASE_PUBLISHABLE_KEY` | Yes, for the frontend | Public project key used by the browser Supabase Auth client; never treat it as a user identity |
| `SQLITE_PATH` | No | Local SQLite database file; defaults to `backend/.local/screen_tutor.sqlite3` and its parent directory is created at startup |
| `STORAGE_DIR` | No | Private local upload directory; defaults to `backend/.local/private_uploads`. Use a persistent private volume or object-storage adapter in deployment. |
| `FILE_URL_SIGNING_SECRET` | Yes, for the class library | Server-only secret of at least 32 characters for five-minute signed file URLs |
| `MAX_UPLOAD_MB` / `MAX_LECTURE_MINUTES` | No | Upload size and lecture length limits |
| `MAX_UPLOADS_PER_CLASS` | No | Upload cap for each class; defaults to 100 |
| `UPLOAD_RATE_LIMIT` / `UPLOAD_RATE_WINDOW_SECONDS` | No | Upload requests per user/window; defaults to 10 per hour per process |
| `GENERATION_RATE_LIMIT` / `GENERATION_RATE_WINDOW_SECONDS` | No | Study-material generation requests per user/window; defaults to 10 per hour per process |
| `LIBRARY_JSON_BODY_MAX_BYTES` | No | Maximum JSON body size for library endpoints; defaults to 1,100,000 bytes |
| `GEMINI_CONTENT_TIMEOUT_SECONDS` | No | Timeout for the separate Gemini content-generation client; defaults to 600 seconds |
| `DEMO_GATE_TOKEN` | Yes when Supabase Auth is not configured | Private demo authorization for token and tool endpoints; protected requests fail closed if neither auth option exists |

Do not put the Gemini API key in frontend config, rendered HTML, client-side environment variables, source maps, or API responses. Use the repository's `.env.example` as the local template. Production deployment should inject secrets through its secret manager.

## Security and abuse controls

- Serve over HTTPS and use same-origin frontend/API deployment where possible.
- Allow only configured origins. Do not use wildcard CORS with credentials.
- Require an authenticated app session for public deployments. For a private hackathon demo without accounts, use a short-lived demo gate and strict IP/token rate limits; an unrestricted public token endpoint can be abused to incur API costs.
- Verify Supabase JWT signatures and standard claims before using `sub`; decoding a JWT without signature verification is not authentication. Never accept the Supabase publishable key as proof of a signed-in user.
- Apply limits to request body size, token issuance frequency, upload volume per class, and upload/generation request frequency per user.
- Keep Lean proof checks behind the same authenticated app gate, limit source size and output, rate-limit requests, and stop long-running Lean process groups. The local runner has resource limits but is not an OS sandbox; isolate it before public multi-tenant use.
- Do not log authorization headers, ephemeral tokens, API keys, request bodies containing credentials, or media (the backend should not receive media).
- Return generic upstream failures to the browser; log only a request ID, status, and safe error category.
- If abuse controls need counters, use short-lived in-memory counters for a single instance or a shared rate-limit store for multiple instances. Never store screen or microphone media as part of rate limiting.
- Check ownership on every class, upload, and study-material request. Keep uploaded files in private storage and serve them only through short-lived signed URLs.
- Keep Supabase secret/service-role keys on the backend. Class-library access goes through FastAPI, which scopes SQLite queries to the verified Supabase user ID; do not route class-library access through Supabase's Postgres Data API.
- Check uploads by allowed extension and file signature, and enforce size limits. Limit upload size, lecture length, uploads per class, and generation requests per student.
- Never log uploaded file contents, extracted text, or transcripts.

## Runtime behavior and errors

- Validate settings at startup; fail closed if the API key is missing rather than serving an endpoint that appears to work.
- Apply a short timeout to token provisioning and map upstream timeouts/errors to a `502` response.
- Keep token issuance idempotency unnecessary: retries may issue another short-lived token, so rate limits must account for retries.
- Do not try to recover Live WebSocket sessions on the backend. The browser owns session resumption, reconnect UI, media stop behavior, and transcript continuity.
- Keep structured logs free of secrets and student content.

## Backend acceptance criteria

- The frontend can request a constrained ephemeral token and connect to `gemini-3.8-live` without exposing the long-lived API key.
- Invalid requests cannot choose another model or add tools.
- Every Live token uses the server-approved Google Search, calculator, Lean (when enabled), and annotation tool set; the caller cannot expand it.
- Google Search grounding is available for fact checking, and the client can display returned citation metadata without routing search results through the backend.
- Calculator expressions are parsed with a bounded AST allowlist into SymPy; exact numeric and symbolic results are supported without evaluating user-supplied code.
- Invalid or over-limit calculator expressions return sanitized errors, and submitted expressions are not logged or stored.
- The Live model can request formal proof verification; Lean accepts valid proofs, rejects invalid or unsafe snippets, and applies time/output/resource bounds without persisting submissions.
- Missing configuration, Google API failure, rate limiting, and origin rejection return stable, sanitized errors.
- Screen/audio streams never transit the backend and are never stored by it.
- Same-origin static frontend and API routes work over HTTPS in the deployed environment.
- A signed-in student can create classes and upload notes, documents, a syllabus, slides, and a video/audio lecture; each reaches `ready` with extracted text, an outline, or a timestamped transcript.
- The backend generates a valid quiz, practice test, study guide, and flashcard set from a class's uploads, with source links, and scores quiz/test attempts.
- A Live token issued for a class includes only that class's materials, and one student can never read another student's classes or files.
- Deleting an upload or class removes its file, text, and derived data.

## Official references

- [Gemini Live API getting started](https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk)
- [Gemini Live API tools](https://ai.google.dev/gemini-api/docs/live-api/tools)
- [Gemini 3.8 Live model capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
- [Gemini 3.8 Flash model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- [Live WebSockets server messages and grounding metadata](https://ai.google.dev/api/live.md)
- [Google Search grounding and attribution metadata](https://ai.google.dev/gemini-api/docs/generate-content/google-search)
- [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens)
- [Live API session management](https://ai.google.dev/gemini-api/docs/live-api/session-management)
- [FastAPI](https://fastapi.tiangolo.com/)
- [Pydantic](https://docs.pydantic.dev/latest/)
- [SymPy parsing](https://docs.sympy.org/latest/modules/parsing.html)
- [SymPy simplification](https://docs.sympy.org/latest/tutorials/intro-tutorial/simplification.html)
- [Google Gen AI Python SDK](https://github.com/googleapis/python-genai)
- [Supabase Auth sessions](https://supabase.com/docs/guides/auth/sessions)
- [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts)
- [Supabase user data and `auth.users` references](https://supabase.com/docs/guides/auth/managing-user-data)
