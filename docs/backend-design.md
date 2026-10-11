# Backend Design

**Project:** Screen Tutor  
**Status:** Backend API contract and implementation  
**Service:** Python, FastAPI, Pydantic, SQLite

## Purpose and Scope

The backend owns all model-provider integration: it protects the long-lived Gemini API key, selects models, defines instructions and tools, and maintains each Live session. The browser captures and previews media locally, then relays sampled frames and enabled microphone audio through a same-origin WebSocket. The backend forwards them in memory and does not record live media, transcripts, or conversations.

The class library stores classes, uploaded course materials, extracted text, lecture transcripts, generated study materials, quiz attempts, and flags in SQLite. Original uploaded files live in private local storage outside the static web root. Rows are owned by the logged-in account (see [Accounts and sessions](#accounts-and-sessions)). `LOCAL_OWNER_ID` is only the legacy owner that `seed_user --adopt-local-data` can migrate.

## Stack

- Python 3.12+
- FastAPI and Uvicorn
- Pydantic v2 and `pydantic-settings`
- Google's `google-genai` Python SDK
- SQLite through `aiosqlite`
- SymPy for bounded calculator execution
- Local private file storage under `backend/.local/private_uploads` by default
- In-process FastAPI background tasks for hackathon upload processing and generation jobs
- Tectonic for saved LaTeX/PDF study documents (local executable; no Docker)

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Yes | Server-only credential for Live sessions and content processing |
| `GEMINI_MODEL` | No | Live model, defaults to `gemini-3.8-live` |
| `GEMINI_TEXT_MODEL` | No | Text extraction and generation model, defaults to `gemini-3.8-flash` |
| `STUDY_PDF_EXECUTABLE`, `STUDY_PDF_TIMEOUT_SECONDS` | No | Tectonic executable (PATH or `backend/.local/bin/tectonic`) and bounded PDF compilation timeout, default 60 seconds |
| `APP_ORIGINS` | Deployment-dependent | Allowed browser origins |
| `DEMO_GATE_TOKEN` | Production | Optional private bearer token gate for session-ticket/tool endpoints; required in production |
| `LIVE_SESSION_RATE_LIMIT`, `LIVE_SESSION_RATE_WINDOW_SECONDS` | No | Session-ticket issuance limits |
| `LOCAL_OWNER_ID` | No | Legacy owner UUID; only used to migrate pre-account data |
| `AUTH_RATE_LIMIT` / `AUTH_RATE_WINDOW_SECONDS` | No | Sign-up and login attempts per client and window (default 10 per 300 s) |
| `SESSION_TTL_DAYS` | No | Login session lifetime (default 30) |
| `SQLITE_PATH` | No | SQLite database path, defaults to `backend/.local/screen_tutor.sqlite3` |
| `STORAGE_DIR` | No | Private upload storage path |
| `FILE_URL_SIGNING_SECRET` | Yes | HMAC secret for short-lived private file links |
| `LEAN_TOOL_ENABLED` | No | Enables the local Lean proof-check tool |
| `LEAN_TIMEOUT_SECONDS` | No | Proof-check wall-clock limit, defaults to 30 seconds |
| `MAX_UPLOAD_MB`, `MAX_UPLOADS_PER_CLASS`, `MAX_LECTURE_MINUTES` | No | Course-material limits |

## Request Policy

API and WebSocket requests are origin-checked. Class-library requests require a logged-in account. Production must configure a high-entropy `DEMO_GATE_TOKEN` for session-ticket and tool endpoints. The backend never accepts `owner_id` from the client.

Class-library routes always scope reads and writes to the configured owner UUID. This keeps the SQLite schema ready for future multi-owner deployments without depending on an external account service in the hackathon build.

## HTTP API

- `GET /healthz`
- `POST /api/live-sessions` with `{}` or optional `class_id` (UUID) and `mode` (`conversation` or `check_steps`); returns a one-use connection ticket and expiry
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
- `GET /api/study-materials/{material_id}/files/{format}` (`pdf` or `latex`)
- `POST|GET /api/uploads/{upload_id}/document`
- `GET /api/uploads/{upload_id}/document/files/{format}` (`pdf`, `latex`, or `original_text`)
- `POST /api/study-materials/{material_id}/attempts`
- `POST /api/study-materials/{material_id}/flags`

Errors use `{"code": "...", "message": "...", "request_id": "..."}` with user-safe messages.

## Live Session Flow

1. The student starts screen sharing explicitly in the browser.
2. The frontend calls `POST /api/live-sessions`; the backend checks origin, access policy, rate limit, and optional class ownership, then issues a random in-memory ticket that expires after 45 seconds and can be consumed once.
3. The browser opens same-origin `WS /api/live` and presents the ticket as a WebSocket subprotocol. The backend checks the origin and consumes the ticket before opening the session.
4. The backend opens a Live API connection with its server-only key and fixed server-owned model, instructions, class context, modalities, and tool declarations.
5. The browser sends typed questions, JPEG frames (at most one per second), and enabled 16 kHz PCM audio. The backend forwards them in memory and streams audio/transcript events back. No media, transcript, or session history is logged or persisted.

The SDK's `session.receive()` iterator ends after each completed model turn. The backend starts another receive iteration for the next turn, keeping the same Live connection open until the student ends it or a real disconnection occurs.

If `class_id` is supplied, the backend checks ownership in SQLite and builds class context from ready uploads. The client cannot choose the provider, model, system instructions, modalities, or tool declarations. Tickets contain no provider credential and do not grant direct model access.

### Check my steps mode

The browser may select `mode: "check_steps"` when requesting a ticket. Omitted mode defaults to `conversation`. This selects a predefined backend coaching policy; it accepts no client instructions or additional permissions. The mode is bound to the one-use ticket and fixed for that connection. End the session before changing it.

In coaching mode, the tutor checks the shared screen automatically, including when the microphone is off and the student has not asked a question. A server task submits the latest stable snapshot for review through the same Live connection at most once every five seconds. It skips unchanged snapshots, stale or paused sharing, active student speech, and an unfinished model turn. After feedback, it waits ten seconds before another automatic review. A bounded set of image hashes suppresses duplicate reviews; images and hashes remain in active session memory only.

The fixed review instruction permits a brief spoken hint only for a clear mistake in a completed, readable step. Correct, incomplete, or ambiguous work should produce silence. The tutor avoids repeating an earlier warning or solving the remaining problem. Check my steps enables the Live provider's proactive-audio configuration so it can abstain from responding. The Live SDK client uses the documented `v1beta` endpoint; the separate content-generation client retains its existing configuration. No Flash evaluator or additional model connection is used for screen checks.

Student questions take priority over automatic checks. Student speech can interrupt playback, and automatic reviews wait for a pause rather than speaking over uninterrupted speech. If a reviewed image changes or sharing stops before feedback finishes, the backend suppresses remaining automatic feedback and rejects its visual marks. Review annotations reference the exact reviewed frame. An unfinished review blocks further checks until completion; all review tasks and memory are cleared on disconnect or Reset memory. Error detection and silence remain model judgments, not guarantees.

Automatic speech detection uses a 900 ms silence window in this mode to allow short thinking pauses. Provider voice-activity events and a bounded PCM energy check prevent automatic reviews during speech; microphone silence alone does not block screen checks. The existing `interrupted` event clears browser playback. Coaching adds no browser protocol events, recordings, stored transcripts, extra model connections, or new tools. Normal conversation retains its existing speech detection configuration. See [Live speech detection and proactive audio](https://ai.google.dev/gemini-api/docs/live-api/capabilities).

### Class Tutor integration

The class Tutor sends the backend class UUID from the persisted `backendId` mapping, resolved by `getBackendClassId(klass)`, to `POST /api/live-sessions`. Its browser route slug is not a backend ID. Before opening Live, `prepareTutorClass()` reconciles the class and actual source files through the existing class/upload APIs and waits for processing to finish. Empty classes are created without requiring an upload. Navigation or End cancels client preparation and prevents a late connection. Quick Tutor omits `class_id` and works without a class. Both paths send the selected predefined mode. The backend retains UUID validation and ownership checks.

### Changing screen content and corrections

Screen frames are changing snapshots, not permanent facts. The backend instructions require the newest readable frame and the student's latest correction to take precedence over older visual observations and assistant claims. Erased, replaced, or corrected work must not remain a current error. Class reference material supplies methods and definitions; it is not evidence that an old equation still appears on screen.

The Live configuration explicitly includes all incoming video between spoken turns, including screen changes made during silence. For a typed question, the backend includes its latest JPEG snapshot with the question in one ordered content update when the frame is at most ten seconds old. Only one bounded JPEG is retained in active session memory; it is replaced on the next frame and cleared on screen pause or disconnect. No images, corrections, or conversations are saved or logged.

The browser sends `screen_paused` whenever sharing stops while Live remains connected. The backend invalidates the current frame and adds a fixed server-owned context notice; on the next frame it signals that a new current screen is available. Annotation requests are rejected without a frame or when its age exceeds ten seconds.

**Reset tutor memory** closes the existing gateway connection and requests a new one-use ticket for the same predefined mode and class. The new provider connection receives current class context but no old frames, turns, transcript, or resumption handle. Screen capture remains under the student's existing permission; a new frame is sampled after readiness. The browser clears its transcript and marks, stops queued speech, and turns off the microphone until the student enables it again. Normal ticket rate limits and failure cleanup still apply. Prompting improves grounding but cannot guarantee that a model will never make another error; reset provides an explicit way to discard the previous conversation.

### WebSocket events

The browser may send only `text`, `audio`, `audio_end`, `frame`, `screen_paused` (no additional fields), and `annotation_result` events. Event payloads are size-limited and validated; frame payloads must be JPEG and are rate-limited. The backend emits `ready`, `assistant`, `annotation_request`, and sanitized `error` events. WebSocket media stays in active session memory only.

The frontend caps the longest JPEG frame dimension at 1280 pixels to stay within the backend's 2,200,000-byte decoded frame limit, including detailed 4K displays. Original source dimensions stay in the browser's frame map for annotation alignment and source-resize validation. The browser rejects annotation references older than ten seconds and caps active marks at eight across requests. The HTTP and WebSocket formats remain unchanged.

## Approved Live Tools

- `google_search`: provider-managed grounding, configured by the backend.
- `calculate_expression`: evaluated by the backend's bounded symbolic parser.
- `verify_lean_proof`: checked by the backend's bounded Lean runner when enabled.
- `add_annotations`: the backend binds the request to the latest forwarded frame; the browser validates and renders temporary marks over the in-page preview, then returns a bounded result.

No tool may click, type, control the desktop, run shell commands, or draw outside the web page.

Live calculator and Lean calls validate their arguments with the same `CalculatorRequest` and `LeanProofRequest` contracts as their HTTP endpoints. The calculator parser returns a string, which is sent to the model as `{"result":"..."}`. Invalid or missing arguments return bounded `invalid_expression` or `invalid_proof` tool errors without ending the Live session.

The native Lean runner imports the fixed `Mathlib.Tactic` prelude for readiness checks and submitted proofs. Loading the entire Mathlib umbrella module exceeded the local deadline even for `True`; the tactic prelude supports the common arithmetic/algebra tactics within the 30-second default limit. The tool declaration tells the model which prelude is available. Definitions from other Mathlib modules are not guaranteed to be present, and unsupported propositions return `rejected`. Input, heartbeat, process, and output bounds remain enforced.

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

Structured content requests use `response_json_schema` with a compact schema derived from the Pydantic model, plus `response_mime_type: application/json`. This preserves JSON Schema keywords such as `additionalProperties` instead of converting them to the legacy provider schema's unsupported `additional_properties` field. The provider schema inlines local references, converts constant values to enums, normalizes nullable types, and omits length/range/format metadata to avoid rejection of complex nested question schemas. Types, required fields, enums, and object structure remain explicit. The same path handles upload summaries, syllabus extraction, lecture transcription, assessments, and generated study materials. Returned JSON is still validated locally with the full original Pydantic model and the existing count/answer/citation checks before saving; provider simplification does not relax backend validation. See [Gemini structured-output documentation](https://ai.google.dev/gemini-api/docs/generate-content/structured-output).

Background jobs are in-process for the hackathon build. On startup, the single backend process marks uploads and imported assessments left in `processing` as `failed` with `processing_interrupted`, and study materials left in `generating` as `failed` with `generation_interrupted`. Completed records stay unchanged. Retry failed uploads through the existing retry endpoint; request a new study material for interrupted generation. This prevents indefinite polling after a crash or restart. Processing completion and failure updates apply only to the active attempt, so an older upload job cannot overwrite a newer edit. Production should use a durable queue before running multiple instances or workers; startup recovery assumes this documented single-process deployment.

## Quiz Generation Integration for the Frontend Agent

### Saved study-guide documents

Every newly generated study guide saves structured JSON, a LaTeX `.tex` file, and a compiled PDF. SQLite's `study_material_files` table stores file bytes privately under the material ID. Content and files commit together before the guide becomes `ready`. PDFs include a title, reference ID, contents, numbered sections, key terms, and source references with page/slide/timestamp locators. Ready guide detail includes `download_urls.pdf` and `download_urls.latex`, pointing to `/api/study-materials/{id}/files/pdf` and `/files/latex`. Downloads check app policy, ownership, kind, and readiness, and return attachments with private/no-store headers. Existing guides export from stored content on first download without another Gemini request. Deletion cascades to files.

Run `backend/.venv/bin/python backend/scripts/install-study-pdf.py` once. It installs pinned Tectonic under ignored `backend/.local/`, checks its release checksum, and warms packages/fonts with synthetic content. Compilation then uses cached resources offline, server-owned templates, escaped text, allow-listed math, `--untrusted`, a timeout, serialized compilation, and bounded output. Malformed equations fall back to literal notation without discarding valid guide content. Arbitrary model-generated TeX programs are never executed. Tectonic is an external runtime dependency, separate from Python requirements. See [Tectonic installation](https://tectonic-typesetting.github.io/en-US/install.html).

### Upload companion documents

Every successfully extracted upload (including pasted notes, scans/images, documents, syllabi, slides, lecture transcripts, and imported tests) automatically starts a **balanced** companion. Upload readiness is independent; a companion failure cannot block other study generation or overwrite original files/text, transcripts, assessment results, or syllabi. Companion PDFs have separate explanation sections and a **full original extracted-text appendix**, with a verbatim UTF-8 source-text download as well. Extraction may miss visual details; the original file remains authoritative and available.

`upload_documents` caches `concise`, `balanced`, and `detailed` versions independently. Every 40,000-character source chunk is analyzed; later pages are never silently truncated. Prompts require source facts, quantities, dates, notation, and uncertainty to be preserved, and every section must cite the actual upload ID. Validated JSON, PDF, LaTeX, and a full source snapshot persist in SQLite. Job IDs and source revisions prevent stale jobs from replacing later edits or retries. Source text/kind/title changes invalidate companions; upload/class deletion cascades them. Startup marks interrupted companion jobs failed and preserves completed documents.

| Endpoint | Contract |
| --- | --- |
| `GET /api/uploads/{id}/document?detail_level=balanced` | Ownership checked. Returns `upload_id`, `detail_level`, `status`, `progress`, `content`, `failure_code`, `download_urls`. Status: `not_created`, `generating`, `ready`, `failed`. Ready content: `format: upload_document`, selected `detail_level`, and `sections` with heading/body/key_terms/sources. |
| `POST /api/uploads/{id}/document` | JSON `{ "detail_level": "concise" }`, balanced default, HTTP 202. Creates missing/retries failed versions; reuses ready/generating ones. Requires ready source, available services, allowed origin, and generation rate limit. Poll GET sequentially. |
| `GET /api/uploads/{id}/document/files/{format}?detail_level=balanced` | Downloads saved `pdf`, `latex`, or `original_text`; returns 409 until ready. Checks app policy and ownership. |

Upload detail additionally includes balanced `document` state. The Library preview refreshes detail, shows separate Study document / Original file / Original text views, and offers detail selection, downloads, and retry. Existing uploads create companions on first open. Changing levels or closing stops client polling while the server saves requested versions. Generation and tutor grounding continue using original extracted sources.

### SQLite access and existing browser data

Class-library APIs require a logged-in account: the browser sends the HttpOnly `jot_session` cookie automatically on same-origin requests, and a missing or ended session returns `401 authentication_required`. Browser mutations must carry an allowed `Origin`; a normal same-origin browser `fetch` supplies it. The backend derives the owner from the session and never accepts a client-supplied owner.

The earlier browser implementation gated study generation on a nonempty token and called `createLocalMaterial()` instead. Its three generic question strings produced the reported “Quick check on New class workspace” local draft, without calling Gemini. That fallback has been removed. Generation, remote material loading/deletion, scoring, and remote flags now call the SQLite APIs without a token prerequisite.

`frontend/src/course-sync.js` performs source reconciliation when the student generates a material or starts class tutoring. It retains the browser's class/upload IDs and persists `backendId` mappings in local class metadata. New classes and uploads call the SQLite APIs without a token prerequisite. Existing browser-only records, including files retained after a failed upload, synchronize through this preparation path. Original files remain retrievable under their existing IndexedDB IDs. Class listing, rename, deletion, library refresh, and syllabus loading likewise use backend UUID mappings without token gates; refresh preserves local IDs and source content. Creating a class with an existing local name reopens that class instead of resetting its saved uploads/materials. Pasted notes accept multiline content up to the backend's 1,000,000-character bound.

Before requesting generation, the frontend must reconcile its browser-only records with the backend:

1. Create a backend class using `POST /api/classes` with `{"name":"Bio 170"}` and retain the returned UUID. A browser slug such as `bio-170-<timestamp>` is not a backend class ID. Preserve any local IDs needed for routing, and persist the mapping to the backend UUID.
2. Submit the actual course files to `POST /api/classes/{class_id}/uploads`. For an existing local upload, retrieve the original `File` from `frontend/src/file-store.js` using its local upload ID. A filename, class title, or local `ready` label supplies no course content. If the original is unavailable, ask the student to upload it again.
3. Send multipart `file` with optional `kind` and `title`, or multipart `text`, `kind`, and `title` for pasted notes. File kinds are `notes`, `document`, `syllabus`, `slides`, `lecture_audio`, and `lecture_video`; text uploads accept only `notes`, `document`, or `syllabus`. Do not set a JSON `Content-Type` on multipart requests.
4. Persist the returned upload UUID mapping immediately and reuse it on retries. Poll `GET /api/uploads/{upload_id}` until every selected upload is `ready`; `failed` requires an error and the existing retry action. Browser-only upload metadata does not establish backend readiness.
5. Generate using those backend IDs. During migration, prefer an explicit `uploads` scope containing the uploads currently selected in the UI, so removed local sources do not reappear through an older backend copy. An `all` scope includes only uploads already ready on the backend and silently excludes processing or failed uploads.

The Study tab now replaces the coarse scope dropdown with a checklist of actual uploaded filenames, kinds, and statuses. Students can select one file or any combination, or use Select all, Clear, Notes only, and Syllabus only shortcuts. The client passes explicit local IDs as `selectedUploadIds` to `prepareStudySources()`; only selected originals are synchronized and polled, then their deduplicated backend UUIDs become `scope.upload_ids`. Empty or stale selections never widen to the whole class. An unselected failed upload cannot block generation. No new HTTP endpoint or request field is needed: the existing `uploads` scope already validates class membership and readiness and restricts both generation context and citations to the selected uploads. Saved material cards retain the selected source filenames.

### Generation request and completion

`POST /api/classes/{class_id}/study-materials` accepts:

```json
{
  "kind": "quiz",
  "scope": {
    "kind": "uploads",
    "upload_ids": ["<backend-upload-uuid>"]
  },
  "options": {
    "count": 10,
    "difficulty": "mixed",
    "question_types": ["multiple_choice", "true_false", "short_answer"]
  }
}
```

Use actual UUID values in requests. Supported material kinds are `quiz`, `practice_exam`, `study_guide`, and `flashcards`. `count` must be 4–50; difficulty must be `mixed`, `easier`, or `harder`. `question_types` is optional and applies only to quizzes and practice exams; when omitted, all three question kinds are requested. `time_limit_minutes` is optional, must be 1–300, and applies only to `practice_exam`. Extra request fields are rejected.

The response is HTTP `202` with a material summary containing `id`, `class_id`, `kind`, `status`, `item_count`, `scope`, and `options`. It acknowledges a background job; it does not include completed questions. Poll `GET /api/study-materials/{id}` without a token prerequisite. Use sequential polling to avoid overlapping requests:

| Status | Required client behavior |
| --- | --- |
| `generating` | Keep the pending material ID and show progress; allow the student to return later. |
| `ready` | Render the typed `content` returned by the detail endpoint and mark it as AI-generated. |
| `failed` | Show the failure and allow a new generation request. Do not save a template as a successful quiz. |
| Network error or client polling deadline | Retain the pending ID and offer another status check; a polling deadline does not mean the server job failed. |

The backend's `_create_material_job()` calls `GeminiContentService.generate_material()` with the ready source text, server-owned system prompt, and a response schema for the requested format. It validates the exact item count, unique question IDs, requested question kinds, answer keys, and source citations before saving `ready`. Failure saves `failed` with `failure_code: "generation_failed"`; the backend never creates placeholder quizzes.

### Quiz rendering and scoring

For `quiz` and `practice_exam`, public material detail contains `content.format` and `content.questions`. Each question has a stable `id`, `kind`, `prompt`, optional choices as `{"id":"a","text":"..."}`, and source citations using backend `upload_id` values. Preserve those IDs when displaying questions and selections.

Answer keys, accepted answers, rubrics, and explanations are intentionally omitted from public quiz detail. Submit remote quizzes through `POST /api/study-materials/{id}/attempts`, with an `answers` object mapping question IDs to selected choice IDs, `"true"`/`"false"`, or the student's short-answer text. This call also does not require an auth token. The response provides `score_percent`, `correct_count`, and `question_results` with the correct answers, explanations, and sources for review. Do not score backend quizzes using missing client-side answer keys. Remote flags use `POST /api/study-materials/{id}/flags` without a token prerequisite as well.

Short-answer scoring always accepts the canonical answer as well as the generated accepted alternatives. Matching normalizes Unicode, letter case, and repeated whitespace while preserving punctuation, signs, decimal points, and mathematical operators: `-1` must not match `1`, `1.0` must not match `10`, and `x+y` must not match `xy`. Multiple-choice submissions must match the exact choice ID after trimming surrounding whitespace. This also applies to already saved quizzes; generating a new quiz is not required for the scoring correction.

### Errors and recovery

| Error | Recovery |
| --- | --- |
| `invalid_class_id` | Create or reconcile the backend class, then use its UUID. |
| `no_ready_uploads` | Upload actual source content and wait for processing. |
| `uploads_not_ready` | Wait for the explicitly selected sources to finish, or retry a failed upload. |
| `not_found` | Reconcile stale class/upload IDs and use uploads belonging to the selected class. |
| `invalid_scope` / `syllabus_not_ready` | Use a valid topic/exam from a processed class syllabus. |
| `invalid_request` / `invalid_options` | Correct field names, option ranges, and format-specific options. |
| `content_processing_unavailable` / `database_unavailable` | Show the service error and offer retry; retain existing local source files. |

Removing the generation token gate alone is insufficient for existing browser-only classes: their class IDs and upload IDs must be reconciled first. The implemented generation path performs that reconciliation, respects the individual upload checklist and selection shortcuts, persists pending generation IDs, and polls sequentially. Navigation stops client polling while the server job remains available through its saved ID. The Library preview, retry, and delete actions resolve mapped upload IDs; retry polling persists processing status. Generation and scoring failures show an error and retain recoverable state instead of inventing questions or scores.

Existing local drafts remain labeled as local drafts; generate a new material from the real sources to replace them. Remote quizzes are rendered by the quiz player and scored through the attempts endpoint. The application callback updates a material by its backend ID, so its transition from `generating` to `ready` does not create duplicate saved entries.

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
- The backend initializes SQLite automatically and scopes class-library data to the logged-in account.
- A student can create classes, upload notes, generate study materials, and submit attempts through the FastAPI API.
- Screen/audio streams transit the backend only in active session memory and are never stored by it.
- Uploaded files and generated study materials can be deleted by the student.

## Accounts and sessions

| Endpoint | Purpose |
| --- | --- |
| `POST /api/auth/signup` | Create an account (`username`, `password`, optional `email` and `display_name`) and log in. `409 username_taken` / `email_taken` if already used. |
| `POST /api/auth/login` | Log in with `identifier` (username or email) and `password`. Always `401 invalid_credentials` with one generic message on failure. |
| `POST /api/auth/logout` | End the session and clear the cookie. Safe when already logged out. |
| `GET /api/auth/me` | `{"user": {...}}` for a logged-in account, `{"user": null}` for a guest. The user includes `is_demo`. |
| `PATCH /api/auth/me` | Change `username`, `email` (send `null` or `""` to remove it), and/or `avatar`. Only sent fields change. `409 username_taken` / `email_taken` if already used; a display name that was just the old username follows the new one. |
| `POST /api/auth/password` | Body `current_password`, `new_password` (8 to 128 characters). `400 incorrect_password` if the current one is wrong, `400 password_unchanged` if the new one is the same. On success every other session of the account is ended and this one stays; responds `204`. Rate limited like login. |

- Usernames are 3 to 32 characters (letters, numbers, `.`, `_`, `-`) and unique ignoring case. Passwords are 8 to 128 characters and stored only as salted scrypt hashes.
- The session cookie is `jot_session` (HttpOnly, SameSite=Lax, `Secure` in production). The database stores only a SHA-256 hash of the token. Logout deletes the server-side session, so a copied cookie stops working.
- **Guest boundary:** with no session, `POST /api/live-sessions` works only without `class_id`; with `class_id` it returns `401`. A `class_id` owned by another account returns `404`. All `/api/classes`, upload, study-material, and attempt endpoints return `401`.

### Demo account

`users.is_demo` marks the single demo account (create or update it with `python -m app.scripts.seed_user NAME PASSWORD --demo`). The flag is returned by `/api/auth/me` and login so the client knows whether to show the built-in sample classes. The server never creates classes for any account; sample content is client-side and only for the demo account. Databases created before the flag existed are upgraded in place at startup.

### Avatar ("Create your own Jot")

`users.avatar` stores a customised Jot as JSON with exactly three ids: `color` (9 choices), `face` (5), and `accessory` (6, including `none`). The server validates them against fixed lists (`Avatar` in `schemas.py`), rejects anything else with `400`, and returns the avatar as `user.avatar` (`null` until one is saved). `PATCH /api/auth/me` with `{"avatar": null}` removes it. The browser draws the character from those ids; no image or free text is stored. Keep the lists in step with `frontend/src/avatar.js`.
