# Backend Design

**Project:** Screen Tutor  
**Status:** Design draft  
**Service:** Python and Pydantic

## Purpose and scope

The backend exists to protect the long-lived Gemini API key and issue short-lived Gemini Live credentials to the browser. The browser connects directly to the Gemini Live API so real-time screen and audio streams do not pass through or get stored by Screen Tutor's server. The server fixes the Live model's approved tool set and provides the bounded calculator function endpoint; Google Search runs as Gemini's built-in grounding tool.

For the MVP, the backend does not receive screen frames, microphone audio, tutor audio, transcripts, or annotation data. It does not proxy the Live WebSocket.

The backend also runs the **class library**: it stores each student's classes and uploaded course materials (notes, documents, syllabi, lecture slides, and video/audio lectures), extracts text and transcribes lectures with the Gemini API, and generates quizzes, practice tests, study guides, and flashcards from them. This part uses Supabase Auth for sign-in, Supabase PostgreSQL for application data and Auth sessions, and private file storage.

## Proposed backend stack

- Python 3.12+
- FastAPI for a small HTTP API and serving the built frontend on the same origin.
- Pydantic v2 for request, settings, and response validation.
- `pydantic-settings` for environment-based configuration, including local `.env` loading.
- Google's `google-genai` Python SDK to provision ephemeral Live API tokens.
- SymPy for exact numeric arithmetic and bounded symbolic simplification; user expressions are converted from a small Python AST allowlist rather than passed to SymPy's string parser.
- Uvicorn as the ASGI server.
- Supabase Auth and its managed PostgreSQL database. Supabase Auth owns login sessions; the app schema stores class-library data and refers to the Supabase Auth user ID for ownership. The initial schema is versioned in [the Supabase migration](../supabase/migrations/20261010120000_initial_class_library.sql); the FastAPI database connection and class-library endpoints will be added in a later implementation step.
- Private object storage for original uploaded files (local disk for a demo; S3-compatible or Supabase/Firebase storage for deployment).
- A background job runner for slow work such as lecture transcription and generation (FastAPI `BackgroundTasks` for the demo; a real queue such as RQ or Celery if needed later).
- File parsers: `pypdf` for PDFs, `python-docx` for DOCX, `python-pptx` for PPTX. Images, scanned PDFs, and video/audio go to Gemini.

### Supabase PostgreSQL schema

The Supabase migrations cover Auth user ownership, classes, course notes/materials, uploaded assessments, generated quizzes and practice exams (as well as the other planned study-material types), source-note links, and completed attempts. Class `owner_id` references `auth.users(id)` directly with `ON DELETE CASCADE`; the app does not keep a duplicate user identity table. Foreign keys cascade class deletion through its notes, generated materials, and attempts. Composite class keys prevent source-note link rows from crossing class boundaries; backend validation must also keep citations inside the generated JSON content consistent with those links. Deleting a source note deletes generated materials derived from it. Original file bytes remain in private object storage; PostgreSQL holds the storage key, extracted text, lecture transcript segments, and parsed syllabus outline. Live screen/audio media, live-session transcripts, annotation events, and tutoring conversations are not stored.

Uploaded existing tests are stored as course-note uploads plus an `uploaded_assessments` record, defined in [the uploaded assessment migration](../supabase/migrations/20261010150000_uploaded_assessment_workflow.sql). The course note retains the original file and extracted text; the assessment record retains parsed questions, detected student responses, and per-question grading details. A blank test is stored as an ungraded reference. A completed test with an answer key is graded against that key. Without a key, the backend can produce an explicitly estimated grade from the owning class's notes. If responses cannot be read reliably or there are no usable class notes for grading, the record moves to `needs_review` and has no score. Generated practice quizzes and exams remain separate `study_materials`; their source-note links can reference uploaded tests so later practice generation can use them.

### Supabase Auth sessions

Supabase Auth is the source of truth for browser sign-in sessions. It stores and manages those records in its protected `auth.sessions` table and issues a short-lived access-token JWT plus a rotating refresh token. Do not create a second `public.user_sessions` table, copy refresh tokens into application tables, or write directly to `auth.sessions`; use the Supabase Auth client/API for sign-in, refresh, and sign-out. The app schema only references the stable Supabase user UUID (`auth.users.id`) for ownership.

| Event | Session behavior |
| --- | --- |
| Sign-in | Supabase Auth creates the login session and returns access and refresh tokens to the browser Auth client. |
| API request | The browser sends the current access JWT to FastAPI; FastAPI verifies it and uses the verified `sub` UUID as the owner. |
| Token refresh | The Auth client exchanges the current refresh token for a new access/refresh pair; application tables are not involved. |
| Sign-out | The Auth client asks Supabase Auth to revoke the session and clears its local session state. An already-issued access JWT can remain valid until its `exp` claim. |

The browser uses the Supabase JavaScript Auth client to sign in and refresh its session. For every protected FastAPI request, it sends the current access token as `Authorization: Bearer <access-token>`. FastAPI verifies the JWT signature and validates its issuer, audience, expiration, and `sub` claim using the project's Supabase signing keys or a supported Supabase verifier. It treats the verified `sub` UUID as the caller identity; it never trusts a user ID sent in a request body or URL. The frontend's publishable key is safe for browser use; Supabase secret/service-role keys and the PostgreSQL connection string remain server-only.

The Supabase Auth project settings control access-token lifetime and supported session timeouts. Access tokens default to a one-hour lifetime; sessions otherwise remain active until sign-out or another configured termination event. Keep the default JWT lifetime unless deployment needs justify a change, and choose session timeout settings before public deployment. If a future high-risk operation requires immediate session revocation, additionally check the token's `session_id` against Supabase Auth session state for that operation; do not make every request depend on querying internal Auth tables.

Application tables have row-level security enabled with no browser-facing policies in the prototype. Class-library data is accessed through FastAPI, which verifies the caller and scopes every query by the verified owner UUID. Keep the database connection and any privileged Supabase key on the server. If the app later accesses application tables directly through Supabase's Data API, add and review user-scoped RLS policies before granting that access.

Keep schema changes in timestamped `supabase/migrations/` files and apply them through the Supabase CLI. Obtain `DATABASE_URL` from the Supabase project's Connect dialog and store it only on the server. A persistent backend can use the direct connection where its network supports IPv6 (or the project's IPv4 add-on); an IPv4-only backend can use the session pooler. Use the project's selected Postgres major version when configuring local Supabase development.

This login session is separate from a tutoring Live session. The browser owns the Gemini Live connection and any transient resume handle. Do not store Gemini ephemeral access tokens, Live session handles, media, transcripts, annotations, or tutoring conversations in Supabase Auth or the application tables.

The frontend can be plain HTML/CSS/JS bundled by Vite. In development, Vite proxies `/api` to FastAPI; in deployment, FastAPI serves the built static files or the host serves both behind one same-origin URL.

## Responsibilities

### Backend owns

- Load and validate `GEMINI_API_KEY` and the server-selected `GEMINI_MODEL`.
- Verify Supabase Auth access tokens and derive the caller's user ID from verified claims.
- Create ephemeral tokens with short session-start and token expiration windows.
- Lock token constraints to the approved Live model and the app's fixed session configuration, Google Search grounding, and function declarations where supported by the API.
- Execute only the bounded `calculate_expression` function through the calculator endpoint; the browser handles Live tool calls and annotation rendering.
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
    Browser->>App: POST /api/tools/calculate for calculator function calls only
    App-->>Browser: Bounded numeric result
    Browser->>Live: Function response with matching call ID
```

The server token request occurs after the student begins the session. It is independent of the media stream. The browser sends media directly to Gemini and handles all Live events.

## HTTP API

### `POST /api/live-token`

Requires a valid Supabase access token in the `Authorization: Bearer <access-token>` header. No caller-controlled model or tool configuration is accepted. The request body is empty (or `{}`), or contains only an optional `class_id`, so the server always chooses the model and configuration. When `class_id` is given, the server checks that the verified Supabase user owns the class and builds the session's system instructions from that class's materials on the server side; the client cannot supply its own instructions.

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

The request uses the same configured app authorization gate and allow-listed Origin check as token issuance. In the current token-service prototype, that is the optional development gate and the configured production demo gate; the planned Supabase integration will replace that gate with verified user authentication. It has a separate per-IP, per-process rate limit, with defaults in `.env.example`. The response never echoes the expression; the endpoint does not log or store expressions or results.

**Failure responses**

| Status | Meaning |
| --- | --- |
| `400` | Invalid syntax, unsupported expression, division by zero, or an expression limit was exceeded |
| `401` | App authentication required or expired |
| `403` | Request origin or policy rejected |
| `413` | Request body exceeds the configured limit (default 1,024 bytes) |
| `429` | Calculator rate limit reached |

Errors use the shared `{ "code", "message", "request_id" }` response shape. Calculator-specific codes are `invalid_expression` for a rejected expression and `calculator_rate_limited` for `429`; shared request codes include `invalid_request`, `request_too_large`, `authentication_required`, and `origin_not_allowed`.

Live reports custom calls in `toolCall.functionCalls[]`. Both custom functions are declared with `behavior: BLOCKING`, so Gemini waits for their responses. The browser should dispatch each call only by its approved name, then return the matching `functionResponses[]` with the original function-call ID and name. If one event contains several calls, return a response for each one in the same response message. Track call IDs so a cancellation event can stop an outstanding calculator fetch and prevent its late response from being sent. Never execute an unknown function name. For a failed calculator call, return only the sanitized `code` and `message` as a short structured function error so the tutor can ask for a clearer expression or explain the limit. If the HTTP request itself fails, use a generic `calculator_unavailable` error; do not retry in a loop. For success, return `{ "result": "37" }` as the function response. `google_search` is Gemini's built-in tool and does not use this custom function-call path. Live function calls are not executed automatically by the API; the client must execute and answer them.

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

Use the application's current authorization token in the example above; do not use the Gemini ephemeral token for the Screen Tutor HTTP API. Check the function name against the approved set before executing it. Handle `add_annotations` locally and leave `google_search` execution to Gemini's built-in tool service.

### Class library endpoints

All of these require a valid Supabase access token and only return the caller's own data. Responses use Pydantic models. FastAPI derives the owner ID from the verified JWT `sub` claim and applies that owner filter on every query; clients do not submit an authoritative owner ID.

| Method and path | Purpose |
| --- | --- |
| `GET /api/classes` · `POST /api/classes` | List or create classes |
| `PATCH /api/classes/{class_id}` · `DELETE /api/classes/{class_id}` | Rename or delete a class (delete removes its uploads, files, and study materials) |
| `POST /api/classes/{class_id}/uploads` | Multipart upload of one file (or pasted text) with an optional `kind`: `notes`, `document`, `syllabus`, `slides`, `lecture_video`, `lecture_audio`. Returns the upload with `status: processing` and starts a background job |
| `GET /api/classes/{class_id}/uploads` | List uploads with kind, status, and progress |
| `GET /api/uploads/{upload_id}` | Upload detail: extracted text, lecture transcript segments, and a short-lived signed URL for the original file |
| `PATCH /api/uploads/{upload_id}` · `DELETE /api/uploads/{upload_id}` | Rename, change kind, edit extracted text, or delete (removes file and text) |
| `POST /api/classes/{class_id}/assessments` | Upload an existing test. Creates the source upload and an assessment extraction/grading job; returns processing state |
| `GET /api/classes/{class_id}/assessments` · `GET /api/assessments/{id}` | List imported tests or fetch parsed questions, detected responses, grading mode, score, and per-question results |
| `GET /api/classes/{class_id}/syllabus` · `PUT …/syllabus` | Read or correct the parsed topic/exam outline |
| `POST /api/classes/{class_id}/study-materials` | Start generating a `quiz`, `practice_exam`, `study_guide`, or `flashcards` with a scope (all, upload IDs—including imported tests—syllabus topics, or an exam) and options. Returns `status: generating` |
| `GET /api/classes/{class_id}/study-materials` · `GET /api/study-materials/{id}` | List materials or fetch one (questions include source links: upload ID plus page, slide, or lecture timestamp) |
| `DELETE /api/study-materials/{id}` | Delete a material |
| `POST /api/study-materials/{id}/attempts` | Submit answers; the server scores them and returns the result with explanations |
| `POST /api/study-materials/{id}/flags` | Flag a question or card as wrong |

### Upload processing and imported test grading

| Kind | Processing |
| --- | --- |
| Notes / documents | Parse text directly (TXT/MD, DOCX, text-based PDF); send images, handwriting, and scanned PDFs to Gemini for text extraction |
| Syllabus | Extract text, then ask Gemini for a structured outline (topics, weeks, exams with dates and topics) and save it |
| Slides | Extract text per slide with `python-pptx`/`pypdf`; send image-heavy slides to Gemini |
| Video / audio lectures | Upload to the Gemini Files API, ask for a timestamped transcript, summary, and key topics (including what's written on the board or slides for video), then save segments |
| Existing tests | Preserve the original upload, extract questions and any answer key, and detect student responses. Store blank tests as ungraded references. Grade completed tests against a detected key; without one, use only that class's notes and mark the result as estimated. Send unreadable responses or unsupported grading cases to `needs_review` without inventing a score |

Keep the answer key, detected responses, and grading result in server-controlled storage. Return the student's uploaded assessment and score to its owner, but do not expose generated quiz/exam answer keys before an attempt is submitted. A generated practice assessment records its source-note links, including links to imported tests, and is saved as its own immutable material so later attempts retain a stable scoring basis.

Each job updates the upload's `status` (`processing → ready | failed`) and progress so the frontend can poll. Jobs must be safe to retry. Large lectures are the slowest and most expensive step, so transcribe each one once and reuse the transcript for every later generation and tutoring session.

Generation sends only the in-scope uploads' text to a standard (non-Live) Gemini model with a response schema per material type, then rejects output with missing answers, a bad structure, or sources outside the class. If a class's text is too large for one request, summarize or chunk it per upload first.

### `GET /healthz`

Returns only service health, for example `{"status":"ok"}`. It must not disclose environment variables, token status, or Gemini account details.

## Token provisioning policy

The backend creates ephemeral tokens through the Gemini API's Live token provisioning feature using the Google GenAI Python SDK. The token request should:

- Use `GEMINI_API_KEY` only on the server.
- Set `uses` to one for a new Live session and use short, explicit expiration values allowed by the API.
- Constrain the model to `GEMINI_MODEL` (default `gemini-3.8-live`).
- Constrain supported Live configuration to audio responses, output transcription, screen input, session resumption if enabled, the built-in `google_search` tool, and exactly the `calculate_expression` and `add_annotations` functions.
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
| `GEMINI_TEXT_MODEL` | Yes, for the class library | Standard Gemini model for text extraction, transcription, and study-material generation |
| `SUPABASE_URL` | Yes | Supabase project URL; used to identify the Auth issuer and signing-key endpoint |
| `SUPABASE_PUBLISHABLE_KEY` | Yes, for the frontend | Public project key used by the browser Supabase Auth client; never treat it as a user identity |
| `DATABASE_URL` | Yes, for the class library | Server-only connection string for the Supabase PostgreSQL database |
| `STORAGE_BUCKET` / `STORAGE_DIR` | Yes, for the class library | Where original uploaded files are stored |
| `MAX_UPLOAD_MB` / `MAX_LECTURE_MINUTES` | No | Upload size and lecture length limits |

Do not put the Gemini API key in frontend config, rendered HTML, client-side environment variables, source maps, or API responses. Use the repository's `.env.example` as the local template. Production deployment should inject secrets through its secret manager.

## Security and abuse controls

- Serve over HTTPS and use same-origin frontend/API deployment where possible.
- Allow only configured origins. Do not use wildcard CORS with credentials.
- Require an authenticated app session for public deployments. For a private hackathon demo without accounts, use a short-lived demo gate and strict IP/token rate limits; an unrestricted public token endpoint can be abused to incur API costs.
- Verify Supabase JWT signatures and standard claims before using `sub`; decoding a JWT without signature verification is not authentication. Never accept the Supabase publishable key as proof of a signed-in user.
- Apply limits to request body size, token issuance frequency, and simultaneous sessions per user/session.
- Do not log authorization headers, ephemeral tokens, API keys, request bodies containing credentials, or media (the backend should not receive media).
- Return generic upstream failures to the browser; log only a request ID, status, and safe error category.
- If abuse controls need counters, use short-lived in-memory counters for a single instance or a shared rate-limit store for multiple instances. Never store screen or microphone media as part of rate limiting.
- Check ownership on every class, upload, and study-material request. Keep uploaded files in private storage and serve them only through short-lived signed URLs.
- Keep Supabase secret/service-role keys and database credentials on the backend. The prototype enables RLS without browser-facing policies; do not expose class tables through the Data API until owner-scoped policies are in place.
- Check uploads by content type and size, not only by file extension. Limit upload size, lecture length, uploads per class, and generation requests per student.
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
- Every Live token uses the server-approved Google Search, calculator, and annotation tool set; the caller cannot expand it.
- Google Search grounding is available for fact checking, and the client can display returned citation metadata without routing search results through the backend.
- Calculator expressions are parsed with a bounded AST allowlist into SymPy; exact numeric and symbolic results are supported without evaluating user-supplied code.
- Invalid or over-limit calculator expressions return sanitized errors, and submitted expressions are not logged or stored.
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
