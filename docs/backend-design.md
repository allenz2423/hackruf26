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
| `STUDY_TUTOR_RATE_LIMIT`, `STUDY_TUTOR_RATE_WINDOW_SECONDS` | No | In-page quiz review chat requests per account or guest IP, default 30 per 60 seconds |
| `STUDY_PDF_EXECUTABLE`, `STUDY_PDF_TIMEOUT_SECONDS` | No | Tectonic executable (PATH or `backend/.local/bin/tectonic`) and bounded PDF compilation timeout, default 60 seconds |
| `APP_ORIGINS` | Deployment-dependent | Allowed browser origins |
| `DEMO_GATE_TOKEN` | Production | Optional private bearer token gate for session-ticket/tool endpoints; required in production |
| `LIVE_SESSION_RATE_LIMIT`, `LIVE_SESSION_RATE_WINDOW_SECONDS` | No | Session-ticket issuance limits |
| `LIVE_REVIEW_TIMEOUT_SECONDS` | No | Total automatic-review deadline, including tools; defaults to 60 seconds, valid range 1–180 |
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
- `POST /api/live-sessions` with `{}` or optional `class_id` (UUID), optional `upload_ids` (up to 100 upload UUIDs; requires `class_id`; only ready uploads in that class are used, and an empty list means no upload context), and `mode` (`conversation` or `check_steps`); returns a one-use connection ticket and expiry
- `WS /api/live` using the ticket as a WebSocket subprotocol
- `GET|PATCH|DELETE /api/tutor-memory` for opted-in saved recaps; optional `class_id` scopes GET/DELETE, PATCH supplies `class_id` and `enabled`
- `POST /api/tools/calculate`
- `POST /api/tools/verify-lean`
- `GET|POST /api/classes` (create takes `name` and optional `color`/`icon`)
- `PATCH|DELETE /api/classes/{class_id}` (PATCH changes any of `name`, `color`, `icon`; omitted fields stay, `null` resets the look to the default)
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
- `POST|GET /api/study-materials/{material_id}/runs`
- `GET /api/study-materials/{material_id}/runs/{run_id}`
- `POST /api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/reasoning`
- `POST /api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/reasoning/retry`
- `POST /api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/skip`
- `POST /api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/answer`
- `POST /api/study-materials/{material_id}/runs/{run_id}/questions/{question_id}/feedback/retry`
- `POST /api/study-tutor` for in-page quiz/practice-test review hints and follow-up explanations
- `POST /api/study-materials/{material_id}/flags`

Errors use `{"code": "...", "message": "...", "request_id": "..."}` with user-safe messages.

### In-page study tutor

`POST /api/study-tutor` returns `{"reply": "..."}` using the server's `GEMINI_TEXT_MODEL` (default `gemini-3.8-flash`). No Live connection or media is involved. The existing adaptive tutoring and answer-withholding policy applies: focused hints, prerequisite explanations, and analogous examples; avoid supplying the student's nontrivial next step or final answer.

Remote reviews send `material_id`, `question_id`, `attempt_id`, and `messages`. Require a cookie-authenticated owner, a ready quiz/practice exam, a question in that material, and a saved attempt belonging to it. Load the question/key and submitted answer from SQLite, rather than trusting client-supplied answer keys. Include bounded excerpts only from that material's remaining ready sources in the same class; disclose missing, changed, or shortened source context to the model.

Local/sample reviews omit those IDs and send `question: {prompt, choices, submitted_answer, correct_answer, explanation}` with `messages`. Treat this object as untrusted reference data; it grants no private class access. Guests follow the existing app/demo-gate policy. `choices` is an array of display strings (at most 8, 1000 characters each); prompt, answers, and explanation have 5000-character limits.

`messages` is an alternating list of `{role: "user" | "assistant", text}`, beginning and ending with a user message: at most 15 entries, 6000 characters each, 24000 characters total; user messages are at most 2000 characters. The client keeps complete recent exchanges when trimming history. The request body is limited to 262144 bytes. Replies are bounded to 6000 characters, requests have a 45-second deadline and a service-wide concurrency limit of two, and rate limits use their own budget. Failures use sanitized 429/503/504 errors. Responses are private/no-store; neither side persists chat history, and logs contain no question, answer, reference, or provider response text.

## Live Session Flow

1. The student starts screen sharing explicitly in the browser.
2. The frontend calls `POST /api/live-sessions`; the backend checks origin, access policy, rate limit, and optional class ownership, then issues a random in-memory ticket that expires after 45 seconds and can be consumed once.
3. The browser opens same-origin `WS /api/live` and presents the ticket as a WebSocket subprotocol. The backend checks the origin and consumes the ticket before opening the session.
4. The backend opens a Live API connection with its server-only key and fixed server-owned model, instructions, class context, modalities, and tool declarations.
5. The browser sends typed questions, JPEG frames (at most one per second), and enabled 16 kHz PCM audio. The backend forwards them in memory and streams audio/transcript events back. No media or raw transcript is logged or persisted. Opted-in accounts may save a bounded session recap as described below.

The SDK's `session.receive()` iterator ends after each completed model turn. The backend starts another receive iteration for the next turn, keeping the same Live connection open until the student ends it or a real disconnection occurs.

If `class_id` is supplied, the backend checks ownership in SQLite and builds class context from ready uploads. The client cannot choose the provider, model, system instructions, modalities, or tool declarations. Tickets contain no provider credential and do not grant direct model access.

### Check my steps mode

See the [proactive coaching and barge-in design](proactive-coaching-design.md) for the implemented backend policy, frontend handoff, acceptance scenarios, and live verification results.

The browser may select `mode: "check_steps"` when requesting a ticket. Omitted mode defaults to `conversation`. This selects a predefined backend coaching policy; it accepts no client instructions or additional permissions. The mode is bound to the one-use ticket and fixed for that connection. End the session before changing it.

In coaching mode, the tutor checks the shared screen automatically, including when the microphone is off and the student has not asked a question. A server task wakes each second and submits a current snapshot through the same Live connection at most once every five seconds. It prefers 1.5 seconds of unchanged pixels, but admits a fresh snapshot after five seconds of continued changes so typing and cursor blinking cannot starve reviews. The snapshot must be at most 2.5 seconds old. Paused sharing, student speech, an unfinished response, or pending tools block reviews. A deque of at most 16 image hashes suppresses identical completed reviews; no images or hashes are persisted.

Every automatic review contains an unpredictable 32-character `review_id`. Before speaking or marking, the model calls the bounded `report_screen_review` control tool with that ID and `mistake`, `no_issue`, `incomplete`, or `uncertain`. The backend suppresses automatic speech and text until it accepts `mistake`. Other outcomes finish silently through a nonblocking function response with `scheduling: SILENT` and `will_continue: false`; this prevents a silent review from waiting forever for a provider completion event that may never arrive. An empty completion event closes the browser buffer without creating a transcript entry. Error detection remains the model's judgment; the tool does not independently verify correctness.

The review prompt allows at most two short sentences about a definite mistake in a completed, readable step. It ignores unfinished expressions, discourages repeated warnings, and leaves the remaining solution to the student. The three-second feedback cooldown starts after the backend finishes publishing spoken output, rather than after a silent decision. Browser playback has no completion acknowledgement, so this cooldown is not measured from the last audible sample. Explicit questions bypass the automatic-review cooldown.

Student questions take priority. Typed questions, provider speech-start events, and provider interruptions flush browser playback through the existing `assistant.interrupted` event. Input transcription provides a fallback for speech arriving after provider generation has ended while browser audio is still buffered; its contents are handled only in memory. Automatic VAD uses a 900 ms silence window in coaching mode. A bounded PCM energy check postpones reviews, but never creates artificial speech turns. Ordinary conversation retains its existing VAD settings. See [Live speech detection](https://ai.google.dev/gemini-api/docs/live-api/capabilities#voice-activity-detection-vad).

Tool work runs in at most four cancellable tasks while the receive loop continues processing speech, interruption, and cancellation events. Generation checks discard late results; an interrupted old completion cannot release a newer student response. Review admission requires completion, no pending tools, and no `IN_PROGRESS` provider status. `waiting_for_input` alone is insufficient. A valid quiet control response can explicitly close a silent review; a matching stale outcome can close the old review as `uncertain`, but cannot complete a newer question.

The review retains its original frame ID, digest, dimensions, and a 32×18 luminance signature. Cursor changes and local typing preserve spoken feedback; source pause, changed dimensions, expired sharing, or a broad visual change invalidate it. The thumbnail heuristic identifies large changes, not mathematical correctness or small edits. Newer-frame and correction prompts remain necessary. Automatic marks reference the original reviewed capture, at most ten seconds old, and require an accepted mistake decision. The browser compares the marked target with its current preview; unrelated pixel changes no longer cause the backend to reject every mark. Coordinates are never rebound to another frame. Other hints can continue without a mark.

An unfinished review, including tools, has a total deadline of `LIVE_REVIEW_TIMEOUT_SECONDS`. Expiry sends a safe `screen_review_timeout` error and ends the connection; the backend never clears a busy flag and overlaps another review. Disconnect and Reset memory cancel tasks and clear frames, hashes, reviews, and pending annotation replies.

The Live SDK uses `v1beta`. Its setup omits `proactivity`, `thinking_config`, and affective-dialog fields. [Gemini 3.8 Live enables proactivity permanently](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live#migrating-from-gemini-31-flash-live); a live check also confirmed that this endpoint rejects a `proactivity` setup field. Application mode controls scheduling and instructions. Production uses one Live connection with no Flash evaluator or additional speech service. Existing event shapes remain unchanged, with the ticket-bound quiz transcript event documented above. Frontend copy and source-lifecycle requirements are documented as a handoff in the proactive design.

### Class Tutor integration

The class Tutor sends the backend class UUID from the persisted `backendId` mapping, resolved by `getBackendClassId(klass)`, to `POST /api/live-sessions`. Its browser route slug is not a backend ID. Before opening Live, `prepareTutorClass()` reconciles the class and actual source files through the existing class/upload APIs and waits for processing to finish. Empty classes are created without requiring an upload. Navigation or End cancels client preparation and prevents a late connection. Quick Tutor omits `class_id` and works without a class. Both paths send the selected predefined mode. The backend retains UUID validation and ownership checks.

### Changing screen content and corrections

Screen frames are changing snapshots, not permanent facts. The backend instructions require the newest readable frame and the student's latest correction to take precedence over older visual observations and assistant claims. Erased, replaced, or corrected work must not remain a current error. Class reference material supplies methods and definitions; it is not evidence that an old equation still appears on screen.

The Live configuration explicitly includes all incoming video between spoken turns, including screen changes made during silence. For a typed question, the backend includes its latest JPEG snapshot with the question in one ordered content update when the frame is at most ten seconds old. Only one bounded JPEG is retained in active session memory; it is replaced on the next frame and cleared on screen pause or disconnect. No images, corrections, or conversations are saved or logged.

The browser sends `screen_paused` whenever sharing stops while Live remains connected. The backend invalidates the current frame and adds a fixed server-owned context notice; on the next frame it signals that a new current screen is available. Annotation requests are rejected without a frame or when its age exceeds ten seconds.

**Restart conversation** closes the existing gateway connection and requests a new one-use ticket for the same predefined mode and class with `use_saved_memory: false`. The new provider connection receives current class context but no saved recaps, old frames, turns, transcript, or resumption handle. Screen capture remains under the student's existing permission; a new frame is sampled after readiness. The browser clears its transcript and marks, stops queued speech, and turns off the microphone until the student enables it again. Normal ticket rate limits and failure cleanup still apply. Saved recaps remain available to later normal sessions and can be deleted separately through Clear saved recaps. Prompting improves grounding but cannot guarantee that a model will never make another error; restart provides an explicit way to discard the previous conversation.

### WebSocket events

The browser may send only `text`, `audio`, `audio_end`, `frame`, `screen_paused`, `end_session` (the last two have no additional fields), and `annotation_result` events. Event payloads are size-limited and validated; frame payloads must be JPEG and are rate-limited. The backend emits `ready` (with memory-enabled state and loaded recap count), `assistant`, `annotation_request`, `session_ended` (with recap outcome), and sanitized `error` events. In the ticket-bound `quiz_reasoning` mode only, finalized `server_content.input_transcription` events are returned as `student_transcript` with a per-event ID; interim transcription and model speech are excluded. The browser scopes these events to the active quiz question and submits them as reasoning turns. Quiz voice uses no class context, saved memory, or tools. WebSocket media stays in active session memory only.

The frontend caps the longest JPEG frame dimension at 1920 pixels, initially at quality 0.85, reducing quality or dimensions to stay within the backend's 2,200,000-byte decoded frame limit. Pillow validates JPEGs before forwarding and enforces a 4096-pixel dimension and 4,194,304-pixel area limit before decoding. The backend retains only the latest compressed frame and tiny comparison signature. Original source dimensions and local grayscale comparison data stay in the browser's frame map for annotation alignment and source/content validation. The browser rejects annotation references older than ten seconds and caps active marks at eight across requests. The HTTP and WebSocket formats remain unchanged.

### Higher visual detail and browser capture

The backend now sets `media_resolution: MEDIA_RESOLUTION_HIGH` for new Live connections in both modes. This requests the provider's high visual token budget; it does not set a pixel dimension or recover detail already discarded by browser downscaling. Provider settings remain server-owned. See [Live media resolution](https://ai.google.dev/gemini-api/docs/live-api/capabilities#media-resolution).

With the student's authorization, `MAX_FRAME_DIMENSION` in `frontend/src/screen-capture.js` is now **1920**, preserving aspect ratio without upscaling smaller sources, initially at **0.85 JPEG quality**. A full-HD screen sends 1920×1080 instead of 1280×720; a 4K screen downscales to 1920×1080. The one-frame-per-second limit and exact captured-frame annotation geometry remain. Oversized JPEGs try quality 0.75 and 0.65 before reducing dimensions.

Existing backend limits already permit 1920×1080, portrait equivalents, and 1920×1920 frames. The encoded message must still fit the 3,200,000-byte WebSocket limit and the decoded JPEG must be no larger than 2,200,000 bytes. The frontend should reduce JPEG quality or dimensions before sending an oversized frame rather than ending the session. Higher detail can increase token use, latency, bandwidth, and decode work; it does not guarantee that every coding error is detected. Start a fresh Live session to pick up the backend setting.

### Annotation accuracy investigation (2026-10-11)

This investigation measured the existing implementation without changing annotation behavior or retaining live user media. It used synthetic content only. The checks below are diagnostic samples, not a localization accuracy benchmark.

**Browser scaling:** An isolated Chromium instance loaded the current `AnnotationController` and stylesheet. Known normalized rectangles were measured against the centered `object-fit: contain` content transform for full-HD, 4K, portrait, square, mobile-sized, and zoomed preview layouts, at device pixel ratio 2. Source dimensions were supplied synthetically; this did not exercise a user's real display-capture permissions or decoder. All six layouts agreed within 0.001 CSS pixels. No general error in normalization, letterbox offsets, or SVG scaling was reproduced.

**Static model localization:** Three independent Live connections used the current server-owned conversation configuration, HIGH media resolution, and annotation tool, with Lean omitted as in the running development server. Each received one synthetic 1280×720 JPEG at quality 0.72 and an explicit request to mark a known target. Measurements below are in transmitted-image pixels, before browser preview scaling. Text targets use the font's ink bounding rectangle as the reference.

| Synthetic target | Returned box center error | Box intersection over union |
| --- | --- | --- |
| Large colored rectangle | 0.7 px | 0.982 |
| Code statement at 18 px font size | 3.0 px | 0.799 |
| Code statement at 9 px font size | 5.3 px | 0.461 |

The model placed the large target accurately in this sample. Smaller text produced less precise boxes; these three observations do not establish a general error rate or reproduce the student's specific screen.

Confirmed implementation gaps:

- **Shape rendering:** The frontend creates the same filled SVG `rect` for `rectangle`, `underline`, `pointer`, and `note`. Shape-specific classes have no distinct geometry or styles. A requested underline or pointer therefore appears as a highlighted box.
- **Content movement:** A mark can remain for twelve seconds over a continuously updating video. Rendering checks source dimensions, but does not compare current content with the referenced frame or track the marked target. Scrolling, editing, or moving a window can leave a previously correct mark at the old location. The isolated browser reproduced retention after a new frame was added.
- **Ordinary turn binding:** Outside an automatic review, the backend attaches `frames.latest_frame_id` when executing the annotation call. It does not bind coordinates to the immutable snapshot supplied with a typed question. Frames arriving during generation can change the attached ID. This is a correspondence risk; the student's specific failure was not captured or reproduced.
- **Coordinate instructions:** The model tool specifies numeric `0..1` bounds, but its individual fields do not describe top-left origin, horizontal/vertical axes, or width/height as extents instead of end coordinates. Google's [image-understanding examples](https://ai.google.dev/gemini-api/docs/image-understanding#object-detection) use a different convention, `[ymin, xmin, ymax, xmax]` on `0..1000`. The current custom convention is usable, as the static samples demonstrate, but should be described explicitly.
- **Source detail:** Browser capture still caps the long dimension at 1280 with JPEG quality 0.72. HIGH provider resolution does not restore text detail removed by that downscaling. The higher capture setting above remains a frontend handoff.

Recommended follow-up within the existing contract:

1. Backend: explicitly describe each coordinate field and bind ordinary marks to the snapshot actually supplied for that turn; reject marks when that snapshot no longer corresponds to current content. Audio turns also need a defined frame association.
2. Frontend owner: implement distinct underline/pointer geometry and invalidate marks when their target content moves or changes. Keep normalized coordinates relative to the exact referenced frame; preserve the existing letterbox transform.
3. Frontend owner: apply the pending higher capture detail change for dense text.
4. If precise word-level marking is required, evaluate locating the selected text with OCR and deriving its box from that locator. A locator would still need exact frame association and current-content validation.

The investigation itself changed no annotation runtime behavior. Its findings describe the implementation before the corrections below; those diagnostic measurements have not been rerun against the new prompt or renderer.

## Approved Live Tools

### Annotation correction and adaptive instruction (2026-10-11)

The student authorized the frontend integration changes for this task. Ordinary annotations now use an immutable turn frame reference rather than the frame arriving at tool execution. Typed questions pin the JPEG included with that question. Voice turns pin the freshest forwarded frame at speech start and refresh it at detected speech end only before output or tool execution; input transcription is the fallback when voice activity events are absent. Automatic checks retain their original reviewed frame. Pause, source changes, expiry, and turn completion invalidate these references. Voice/video ordering is approximate at the provider; a browser content comparison supplies the final guard against moved targets. The server does not claim pixel-perfect localization.

`add_annotations` keeps its visual permission and browser event format. Its optional provider argument `frame_id` must match the active turn reference if supplied; IDs are included with ordered question/review snapshots. A missing argument uses only the pinned turn reference, never the newest frame at execution. The provider schema and instructions use `box_2d = [ymin, xmin, ymax, xmax]` scaled 0–1000 relative to that exact frame. The backend converts finite boxes to normalized `region` XYWH coordinates and validates `x + width <= 1`, `y + height <= 1`. Coordinates outside the provider scale are clamped inside the frame; inverted or empty boxes are rejected. Backend validation also rejects unknown fields, unsupported kinds, counts outside one to five, and labels longer than eighty characters before browser execution. A malformed item rejects the request rather than silently dropping marks. Automatic marks still require an accepted mistake decision, while local content validation permits incidental changes away from the target.

The frontend captures up to 1920 pixels at JPEG quality 0.85, reduces quality or dimensions to meet the existing byte limit, and preserves aspect ratio without upscaling. Its eight-frame map holds bounded grayscale comparison data in memory. Before admitting a mark and while displaying it, the browser compares the referenced target region with the current preview, removing changed targets rather than moving old coordinates. This is a conservative visual heuristic, not OCR or target tracking. Underlines and pointers have distinct SVG geometry; rectangles and notes retain their own styles. No additional model tool, persistence, or desktop control is introduced.

The backend's fixed tutor instruction now selects help using current conversational evidence: a concise hint for a plausible next step, a direct explanation for a missing prerequisite, or a small analogous worked example with different values after two unsuccessful hints. Two hints is a starting heuristic, not a research-derived threshold or permission to reveal the student's answer. It uses task-specific feedback, occasional self-explanation and retrieval questions, bounded independent attempts, and fades support as progress returns. It does not rotate methods every frame or quiz a silent student during automatic checks. Scheduled checks keep their two-sentence mistake-only policy and three-second feedback cooldown. Conversation history remains ephemeral; this prompt does not implement spaced scheduling or stored student profiles.

The student requested that the tutor withhold both final answers and nontrivial intermediate step answers, including when directly asked for a solution. Hints, prerequisite explanations, and analogous examples leave the student's own step to complete; annotation labels and tool results must not leak the answer. A directly supplied intermediate step is allowed only when it is absolutely routine at the student's demonstrated level and is not the learning target (for example, basic arithmetic inside a calculus problem). Uncertain level defaults to a hint, and routine steps do not authorize a final answer. The tutor may confirm an answer the student independently proposes. A nontrivial worked step or final answer is reserved for clearly communicated severe overwhelm or imminent abandonment after simpler help has failed; the tutor first reduces the next action, then supplies only the help needed if the student still cannot continue and wants direct assistance. Silence, a single error, or asking for the answer alone does not trigger this exception. This is a provider instruction, not a deterministic output filter; no learner profile or emotional-state diagnosis is added. New Live connections receive the policy in both tutor modes, with no HTTP or WebSocket contract change.

- [Scaffolding review](https://link.springer.com/article/10.1007/s10648-010-9127-6)
- [Formative feedback research](https://myweb.fsu.edu/vshute/pdf/shute%202007_f.pdf)
- [Worked examples and fading](https://onlinelibrary.wiley.com/doi/full/10.1111/cogs.12086)
- [Learning technique review](https://pubmed.ncbi.nlm.nih.gov/26173288/)

- `google_search`: provider-managed grounding, configured by the backend.
- `calculate_expression`: evaluated by the backend's bounded symbolic parser.
- `verify_lean_proof`: checked by the backend's bounded Lean runner when enabled.
- `add_annotations`: the model supplies `box_2d = [ymin, xmin, ymax, xmax]` on a 0–1000 scale. The backend converts boxes to normalized 0–1 `region` coordinates and binds requests to the immutable question/review frame reference. The browser validates geometry and current target content, renders temporary marks over the in-page preview, then returns a bounded result. The browser event format is unchanged.
- `report_screen_review`: coaching-only control tool; accepts exactly a bounded active `review_id` and one decision enum, authorizes a hint or completes a review silently. It has no visual action or HTTP endpoint.

No tool may click, type, control the desktop, run shell commands, or draw outside the web page.

Live calculator and Lean calls validate their arguments with the same `CalculatorRequest` and `LeanProofRequest` contracts as their HTTP endpoints. The calculator parser returns a string, which is sent to the model as `{"result":"..."}`. Invalid or missing arguments return bounded `invalid_expression` or `invalid_proof` tool errors without ending the Live session.

The native Lean runner imports the fixed `Mathlib.Tactic` prelude for readiness checks and submitted proofs. Loading the entire Mathlib umbrella module exceeded the local deadline even for `True`; the tactic prelude supports the common arithmetic/algebra tactics within the 30-second default limit. The tool declaration tells the model which prelude is available. Definitions from other Mathlib modules are not guaranteed to be present, and unsupported propositions return `rejected`. Input, heartbeat, process, and output bounds remain enforced.

Startup readiness has a separate 60-second deadline to allow cold Mathlib imports on a loaded development machine. Submitted proofs still use `LEAN_TIMEOUT_SECONDS` (30 seconds by default). Startup continues to fail closed if the readiness proof is not verified.

## SQLite Data Model

The schema in `backend/app/services/sqlite_schema.sql` defines:

- `course_classes` (includes the card look: `color` and `icon`, ids from the fixed `ClassColor`/`ClassIcon` lists in `schemas.py`, or NULL for the default)
- `course_notes`
- `study_materials`
- `study_material_sources`
- `study_material_attempts`
- `quiz_runs` (the selected `standard` or `reasoning` mode is persisted per run)
- `quiz_question_attempts`, `quiz_reasoning_turns`, `quiz_reasoning_evaluations`, and `quiz_feedback_operations`
- `quiz_reasoning_metrics` (daily aggregates only; no student or question labels)
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

### Opt-in Reasoning Mode runs

The mode picker starts with Standard. Both modes create an owner-bound `quiz_runs` row; the existing Standard player and grading endpoint remain the Standard path. Reasoning Mode creates one `quiz_question_attempts` row per question and uses the run-scoped endpoints above. Local/sample quizzes do not create backend runs. Public material detail continues to omit answer keys, accepted answers, rubrics, and explanations.

Each deliberately submitted text or finalized voice turn is written to `quiz_reasoning_turns` before a provider call. Evaluation context is limited to the question, choices and instructional metadata, ordered turns, latest turn, and prior follow-up prompts; it never includes the answer key. Versioned evaluator output is schema-validated, and provider or schema failures leave the saved turn locked for a safe retry. The evaluator judges meaningful engagement rather than correctness. The first sufficient evaluation stores an immutable accepted turn number and snapshot hash. Further turns append without revoking permission. Final answer submission requires that accepted state, freezes the complete included turn snapshot and answer in one transaction, and reuses the deterministic quiz grader. Feedback runs afterward and cannot alter the grade or submitted snapshot. A feedback error can be retried without another grade or answer submission.

Skip is a separate terminal question state and is not returned as an incorrect submitted answer. Each operation is owner/run/question scoped and idempotent. Duplicate operation IDs with changed content conflict; a second distinct reasoning turn during an active evaluation gets a recoverable conflict. A confirmed skip supersedes a pending evaluation, and conditional state/snapshot updates discard late provider responses. Startup recovery moves abandoned evaluations and feedback jobs into retryable failed states. Retakes use new run IDs and never overwrite prior attempts. Standard attempts without a run ID continue to use the legacy endpoint for compatibility.

Reasoning evaluator and feedback calls use the configured text model, server-owned versioned prompts, bounded deadlines, and strict structured output. Logs contain only failure categories and aggregate measurements, not student text. Daily aggregates track first-attempt acceptance, follow-up turns, skips, failures, retries, and latency buckets. Inspect recent aggregates on the application database with `SELECT * FROM quiz_reasoning_metrics ORDER BY metric_date DESC LIMIT 30;`; these are diagnostic workflow metrics, not mastery scores.

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

## Saved sources, extraction, and context

- Saved study materials, PDF/LaTeX files, and attempts survive editing or deleting an individual source upload. Source links cascade when an upload is deleted, while material scope/content retain their original identifiers. Deleting the material or its entire class still deletes its saved files and attempts.
- Material summaries and details expose additive `source_warnings`: `changed` for edited source text/kind, `missing` for deleted uploads, and `truncated` when generation used partial source excerpts. Saved question keys and attempt scores remain unchanged. Renaming alone does not mark study sets outdated. SQLite upgrades existing databases in place and removes the old destructive source-deletion trigger.
- Upload, reprocessing, and imported-assessment jobs guard writes with `processing_revision`, independent of display metadata and `updated_at`. Source edits/retries/new job claims invalidate previous attempts; title-only updates preserve a running job. This guards success and failure writes alike.
- PDF readability checks count actual extracted page text rather than generated page markers. Scanned PDFs and mixed PDFs containing pages with less than 20 readable characters use Gemini file extraction. Readable PDFs retain native text and page boundaries. The existing 50 MiB vision-extraction limit remains in force.
- Class tutoring prefers original extracted text, falling back to summaries only when source text is absent. A 40,000-character text budget is shared across readable selected uploads; study generation similarly shares its 250,000-character budget. Short sources release unused space. Large sources receive beginning/end excerpts with omission markers and explicit coverage metadata; the model must not claim coverage of omitted text. The browser surfaces the generation warning.

## Saved tutoring recaps (approved product addition)

The student approved session memory on 2026-10-11. Memory is off by default and available only to signed-in accounts. A separate scope belongs to each account/class pair; quick tutoring uses its own general scope. Only short structured recaps persist. Frames, audio, full conversations, and raw transcriptions remain temporary and are never written to storage or logs.

- `GET /api/tutor-memory?class_id=<optional UUID>` returns `enabled`, `saved_count`, and up to five recent `items` (`id`, `created_at`, `content`). Omit `class_id` for general tutoring. Each content object has `summary`, `topics`, `observed_progress`, `practice_targets`, and `next_step`.
- `PATCH /api/tutor-memory` accepts `{ "class_id": null | UUID, "enabled": boolean }` and returns the same state. Disabling prevents new saving/loading but preserves existing recaps.
- `DELETE /api/tutor-memory?class_id=<optional UUID>` clears all recaps in that scope and preserves its preference. Clear and preference changes increment a revision, invalidating previously issued tickets and in-flight recap writes. Ownership, cookie authentication, browser origin checks, and private/no-store responses apply to all endpoints. Class deletion cascades memory.
- Live tickets bind any enabled memory scope and revision on the server. New connections receive up to three recent recaps as untrusted historical reference, separate from course source material. The tutor must check relevance, never infer mastery from a recap, and keep the existing answer-withholding policy. `use_saved_memory: false` in the ticket request skips loading for a fresh conversation; opted-in saving of that new conversation remains available.
- The browser explicitly sends `{ "type": "end_session" }` when the student presses End. Input/capture/playback stop immediately. The backend stops provider tasks and releases frames before generating a recap from at most 40,000 characters of in-memory text and voice transcription plus audible tutor text. At least one student utterance is required. Automated screen observations without student input are not saved.
- The text model returns a locally validated, bounded JSON recap describing observed work and tentative next steps. It must not copy full solutions, code, private credentials, emotional assessments, or unsupported learner characteristics. Summary generation has a 25-second deadline and bounded concurrency. No fallback invents a recap after failure.
- A terminal `{ "type": "session_ended", "memory_status": "saved" | "disabled" | "empty" | "failed" }` acknowledges completion. Provider failures and unexpected disconnects before End do not save. Once explicit End has been received, closing the browser does not cancel that requested save. Saving is idempotent for one session; at most ten recaps per scope persist. The browser waits at most 30 seconds for acknowledgement, then closes and reports that saving could not be confirmed.

## Security Boundaries
- Keep `GEMINI_API_KEY` server-side only.
- Do not log tickets, authorization headers, API keys, uploaded content, extracted text, transcripts, screen media, or submitted proof text.
- Live media and raw transcripts may transit the backend only in session memory; do not record, buffer to disk, or persist them. Only explicitly enabled derived tutoring recaps may persist.
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

### Tutor language lock

`POST /api/live-sessions` accepts an optional `language` from a fixed list (`TutorLanguage` in `app/live_modes.py`: `auto`, `en`, `es`, `fr`, `de`, `pt`, `it`, `zh`, `ja`, `ko`, `hi`, `ar`). Anything else is rejected with `400`, and the client cannot send its own instructions. The language travels on the one-use ticket into `live_connect_config`, which appends a fixed, server-written language-lock instruction (spoken replies, written replies, and mark labels in that language; quotes kept in their original language) ahead of any untrusted class material. `auto` adds nothing. The lock is enforced through the session's instructions, not a speech-model setting, so it is strong but not absolute; test the chosen model in each language before relying on it.
