# Frontend Design

**Project:** Screen Tutor  
**Status:** Design draft  
**Client:** HTML, CSS, and JavaScript

## Purpose and scope

The frontend is a browser tutor workspace. A student shares a display, window, or browser tab; asks a question by voice or text; and sees the tutor's reply beside a live preview of the shared content. The tutor can place temporary marks over that preview.

The frontend also has a **class library**: students create classes (Math, Biology, and so on), upload course materials into each one (notes, documents, syllabi, lecture slides, and video/audio lectures), and generate quizzes, practice tests, study guides, and flashcards from those materials. A tutoring session started inside a class uses that class's materials. See [Class library and study pages](#class-library-and-study-pages).

The annotations live inside the Screen Tutor page. A normal web page cannot draw over the student's other desktop applications. The frontend does not click, type, or otherwise control the shared application.

## Proposed client stack

- Semantic HTML for the page and controls.
- CSS for the responsive two-column workspace, preview overlays, and status indicators.
- Vanilla JavaScript ES modules for capture, audio, conversation UI, annotations, and backend session events.
- Vite for local development and static asset bundling; no UI framework.
- A provider-neutral WebSocket client for the backend. The frontend contains no provider SDK, model identifier, model instructions, tool declarations, or provider credentials.
- Browser APIs: `getDisplayMedia()` for screen capture, `getUserMedia()` and Web Audio for microphone capture/playback, Canvas for frame sampling, and an SVG/canvas layer for marks.
- File input, drag-and-drop, and `fetch`/`XMLHttpRequest` upload progress for course materials; `<video>`/`<audio>` for playing uploaded lectures.
- Simple hash-based routing (`#/classes`, `#/classes/:id/library`, …) so the library, study, and tutor pages stay in one static app.

The browser must be served from HTTPS in deployed environments. Screen capture must start from a user gesture and uses the browser's own source picker. Support should be verified on the selected desktop browsers before promising cross-browser compatibility.

## Workspace layout

```text
┌───────────────────────────────────────────────────────────────────┐
│ Screen Tutor     Screen: Sharing / Paused     Mic     End session │
├───────────────────────────────────────┬───────────────────────────┤
│                                       │ Tutor conversation        │
│   Shared screen preview               │                           │
│   + annotation layer                  │ Tutor transcript          │
│                                       │ Student questions         │
│   Pause / choose another source       │                           │
│                                       ├───────────────────────────┤
│                                       │ Ask a question…   Send 🎙 │
└───────────────────────────────────────┴───────────────────────────┘
```

On narrow viewports, stack the preview and conversation vertically. Keep the preview and active annotations visible together. Do not make color the only way to distinguish sharing, microphone, connection, or error states.

### Main UI elements

- **Header:** all class tabs and Quick tutor use compact app navigation and reduced top spacing. On Tutor pages, session, connection, screen-sharing, and microphone indicators share the navigation row on wide screens and wrap below it on narrower screens. Keep the class name, All classes link, and Library/Study/Tutor tabs in one compact row when space allows, with a smaller heading across every class tab. Class navigation wraps on mobile, and all status text remains visible. End session stays in the stage toolbar.
- **Screen stage:** video preview, annotation layer, source/re-share controls, and an explicit pause action.
- **Tutor settings:** a dedicated modal opened from Settings in the stage toolbar. Check my steps, language lock, Remember sessions, saved recaps, and Restart conversation live here. The conversation panel contains the buddy, heading, transcript, microphone status, and composer. Closing settings returns focus to its trigger; Escape closes it, navigation disposes it, and existing session locks remain in effect.
- **Conversation:** user questions, tutor text transcript, connection/retry notices, and a Clear conversation control.
- **Composer:** text question input, Send, and microphone toggle.
- **Annotation controls:** dismiss one mark and Clear marks. Marks expire automatically after a short interval.
- **Class selector:** shows which class's materials the tutor is using, or “No class” for a general session.
- **Check my steps:** an optional checkbox before Share. When enabled, the tutor gives short corrective hints at pauses while the student explains a solution. The control is locked during a session; End unlocks it for the next session.
- **Restart conversation:** discard a connected tutor's prior conversation and marks, skip saved recaps, keep the shared screen, and open a fresh session with the selected mode and class. Stop microphone input and queued speech; the student enables the microphone again afterward. Saved recaps remain available to later sessions and are deleted separately.

## Class library and study pages

### Page map

```text
Classes ─┬─ Math 151 ─┬─ Library   (uploads, bounded source search)
         │            ├─ Concepts  (source-backed class concept map and manager review)
         │            ├─ Study     (generate + open quizzes, practice tests, guides, flashcards)
         │            ├─ Tutor     (screen-share workspace using this class's materials)
         │            └─ People    (membership and invitations)
         └─ Biology ── …
```

**Classes page.** A grid of class cards plus **New class**. Each card has a band in the class color with the class badge (a subject icon or the class initial) hanging over its edge, then the name, upload and study-set counts ("No materials yet" when empty), and the next exam from the syllabus if known. A "⋯" options menu holds **Edit class** and **Delete**. Edit class changes the name, color (nine deep inks), and icon (sixteen subject icons or the initial) with a live badge preview. Color and icon are saved with the class on the account (`color` and `icon` ids from fixed lists in `class-style.js`), so every device shows the same card; classes styled before the backend stored the look upload it on the next sync. Delete asks for confirmation because it removes the class's uploads and study materials.

**Class page.** Server-backed classes show Library, Concepts, Study, Tutor, and People in the same compact header: All classes link, class badge and name, and class tabs in one row on wide screens, wrapping on mobile. Browser-only sample classes retain their existing tabs and do not show Concepts, since their sources are not in the backend index.

### Concepts tab (shared class map)

Concept cards keep reading content compact: alias creation and manager editing, deprecation, and merge actions stay behind closed disclosures until needed. Tighter card spacing preserves more concepts in view, and the grid reflows cleanly as the viewport narrows or browser zoom increases.

Members with class read access see the authorized active concepts, descriptions, aliases, source references, and validated typed relationships. The UI accepts the backend's `status` or `state` field and displays the concept state. The initial view uses responsive cards and lists rather than a graph-rendering dependency; prerequisite relationships are identified separately from related or applied-in relationships. Only owners and members with `manage_permissions` see manager controls. Managers can review source-backed concept, alias, and relationship proposals, open exact cited chunks and original files, create concepts and source-backed relationship proposals, edit names/descriptions/aliases, merge concepts with confirmation, and approve or reject proposals using optimistic versions. Unapproved aliases are labeled separately and are never shown as active to students. Managers can deprecate a validated concept after confirmation; this removes it from the active student map while keeping its references auditable. Extraction jobs expose pending, completed, and failed states with status retry; raw provider errors are not displayed. A failed graph or review request is shown as unavailable rather than as an empty map or queue. The frontend hides controls by capability for usability, while every operation remains enforced by the backend. The graph view contains no student mastery or assessment outcomes.

### Library tab (uploads)

Shared-resource release controls appear below the resource title and metadata. Their form width is capped, and they remain within the content column so expanded controls do not stretch the upload icon column or overflow at narrow widths and browser zoom.

The local SQLite workspace calls class/upload APIs without an access-token prerequisite. Existing browser route and IndexedDB IDs stay stable; a persisted `backendId` identifies the corresponding backend class or upload. Library refresh reconciles server-backed uploads by backend ID, removes stale mappings omitted from an authoritative remote list, and preserves browser-only uploads. New classes and uploads save through the API, with local files retained for recovery after network failures. Pasted notes use a multiline field with the backend's 1,000,000-character limit. Rename and deletion use the mapped backend UUID and surface API failures.

```text
┌────────────────────────────────────────────────────────────────────┐
│ Math 151      [Library]  Study   Tutor                             │
├────────────────────────────────────────────────────────────────────┤
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Drop files here or Browse   ·  Paste text                   │  │
│  │  Notes, docs, syllabus, slides, lecture video/audio          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  Filter: All | Notes | Docs | Syllabus | Slides | Lectures         │
│  ▸ Syllabus.pdf          Syllabus   Ready                          │
│  ▸ Week 3 notes.jpg      Notes      Ready                          │
│  ▸ Lecture 5.mp4  48:12  Lecture    Transcribing… 62%              │
│  ▸ Ch4 slides.pptx       Slides     Failed — Retry                 │
└────────────────────────────────────────────────────────────────────┘
```

- **Upload zone:** drag-and-drop or file picker (multiple files), plus a Paste text option. Accepted types: PDF, DOCX, PPTX, TXT/MD, PNG/JPEG, MP4/WebM/MOV, MP3/M4A/WAV. The app suggests a kind (notes, document, syllabus, slides, lecture) from the file type and name, and the student can change it before or after upload.
- **Progress and status:** each upload shows its upload progress, then a processing status from the backend (`uploading`, `processing`/`transcribing`, `ready`, `failed`). Poll the upload's status endpoint until it reaches `ready` or `failed`. Long lectures show progress and can be left to finish while the student keeps working.
- **Validation before upload:** reject unsupported types and files over the 100 MiB per-file limit with a clear message, and don't send them. The backend enforces the same default limit.
- **Upload detail view:** shows the original (PDF/slide viewer, image, or video/audio player) beside the extracted text. Lectures show a timestamped transcript; clicking a line seeks the player. The student can edit extracted text, rename, change kind, or delete.
- **Indexed source search:** saved classes expose a Library search for source material the current user is authorized to access. Search results show a bounded excerpt, material title, section hierarchy, page/slide/time locator, and immutable revision identifiers. Search results never contain a full source document or assessment answer data. Selecting one result requests that exact chunk revision from the backend and displays its text and provenance; the browser does not cache the full response in local storage, IndexedDB, or the shared class record. The selected chunk can open its original source through the authenticated upload-detail API and the existing citation preview: PDFs use page locators, audio/video seek to timestamps, and document/slide sources show the exact locator next to the original-text preview. Searching, exact-chunk reads, and original-source opens all rely on current server authorization and release checks.
- **Syllabus outline:** when a syllabus is ready, show its parsed topics and exam dates at the top of the library so the student can check and correct them.
- **Upload notice:** before the first upload, tell the student that uploaded content is stored in their account and may be sent to the deployment's configured AI provider for processing. Text generation uses the admin-selected compatible route; scanned-file OCR and lecture transcription still use Gemini Files.

### Study tab (generated materials)

- **Generate panel:** pick a type (Quiz, Practice test, Study guide, Flashcards); a scope (whole class, selected uploads, syllabus topics, or an upcoming exam such as “Midterm 1”); and options (4–100 questions, sections, or cards depending on the selected type; difficulty; and time limit for practice tests). Study guides show an optional name field; leaving it blank lets the backend suggest a title from the guide outline. They also offer multi-select Balanced, Visual overview, Text-dense explanations, Interactive learning, Worked examples, and Quick reference presentation styles; Balanced is selected by default and may be combined with the others. Quizzes and practice tests show an optional, 2,000-character **Additional instructions** field; guides and flashcards do not. The default practice-test timer scales at 45 seconds per item with a 15-minute minimum. Show a generating state and allow cancel.
- **Saved materials list:** every generated item with type, scope, and date; a score appears only when the associated run's feedback policy releases it. The student can open, retake, regenerate, or delete it.
- **Quiz / practice test player:** one question at a time or full-page mode, multiple-choice, true/false, and short/free-response inputs, an optional timer for practice tests, and Submit. Remote assessments start or resume from a stable operation ID and render a safe, immutable per-run question projection; reopening a completed run restores its released review, and a new run starts only after an explicit Retake action. Results show only fields the backend releases under the assessment feedback policy. Browser-local samples retain their existing local scoring. When released, explanations and source links (page/slide number or lecture timestamp) can be reviewed, and a missed question may offer **Ask tutor about this**.
- **Study guide view:** while a remote guide is generating, open a live preview that polls the authorized detail endpoint and replaces its cumulative section snapshot as draft and revised batches arrive. Label it “not checked”; explain that sections can change and downloads are available after deterministic document validation and export. The generation model is not independently reviewed for factual accuracy, so the saved guide remains AI-authored and may contain mistakes. Render math from standard delimiters, equation environments, bare TeX symbols/scripts, Markdown math fences and formula code spans, HTML-encoded math symbols, and Unicode operators/subscripts/superscripts in headings, prose, formula definitions, key terms, and explanations. Formula fields that already contain delimiters must not be wrapped a second time. Invalid notation remains visible as text. Provide full-screen viewing and an **Open in new tab** link for remote guides; the new tab reloads the guide through its authenticated detail endpoint so source links and interactive examples remain available. If native browser fullscreen is unavailable or blocked, expand the guide to fill the tab and explain that the browser toolbar remains visible. If generation fails, open the retained last snapshot as a clearly marked provisional preview, with no downloads, and show **Retry generation** in the preview and Saved materials. Retry reuses the saved guide ID, scope, options, and source associations; it preserves the last preview while generation runs and does not mark the guide ready until schema/size validation and export pass again. Only the owner of a personal guide, the class owner, or an authorized guide author can retry. If there is no snapshot, say so explicitly. Closing a generating preview stops browser polling but leaves the server job running; a generating or failed guide can be reopened from Saved materials. Do not persist partial preview content in browser class storage. Version-1 saved guides continue to render from their section text. Version-2 guides render ordered paragraph, formula, labeled-diagram, example-step, interactive-model, `svg_diagram`, and takeaway blocks. Fixed activities use the existing browser dispatcher; custom diagrams use a second XML sanitizer before insertion and can contain original subject-specific vector art and declarative SVG animation. App-owned activities include force components, a controllable linear graph, a first-order response plot, an ideal resistor divider, a binary-search trace, selectable atoms in a small reviewed 2D molecule catalog, and a source-selected animal-cell organelle map with fixed descriptions. Its schematic is not to scale, and the model selects only feature IDs supported by the source. The saved block contract remains bounded; its SVG block is general-purpose drawing data rather than a template selection. Linear graphs now render through JSXGraph, first-order response plots through Apache ECharts, process pathways through Mermaid, and reviewed molecule structures through SmilesDrawer; each package is lazy-loaded only when its matching block appears. Native SVG/HTML and text fallbacks remain for resilience and accessible alternatives. Other activities retain their app-authored renderers and deterministic local calculations, with paired numeric/range controls where applicable and keyboard or touch actions. Reset returns controls to authored defaults; closing the guide discards transient interaction state. Unsupported templates show their backend-derived text fallback; malformed blocks fall back to the section body. SVG drawing elements are inserted only after strict XML and attribute allowlists, ID-reference rewriting, and size/depth checks. SVG scripts, event handlers, CSS, foreign objects, images, links, and network references are removed. Animation playback uses browser-owned controls; no model-authored JavaScript, code, script URL, or provider configuration is executed.
- **Guide citations and export:** citations attached to sections and nested blocks use the existing authorized source-preview flow. Existing PDF and LaTeX downloads remain available; version-2 PDF output shows the authored default vector, formulas, units, and a textual component summary rather than a student's transient browser state.
- **Saved guide files:** remote guides offer Download PDF and Download LaTeX. Files remain saved with the guide; older guides export from stored content on first download. Practice tests display their generated instructions above the assessment form.
- **Guide viewer sizing and code examples:** study-guide dialogs open at a readable desktop size and can be resized from the lower-right corner; their content pane scrolls independently. On narrow screens they fill the available viewport. Fixed Python examples use a read-only, syntax-highlighted IDE-style editor with a filename/language bar and line numbers, plus a plain-text fallback.
- **Flashcards view:** flip card with keyboard support (Space to flip, arrows to move), “Got it” / “Study again” sorting, and shuffle.
- **AI-generated label and Flag:** every generated item is labeled as AI-generated. Flag actions are shown only where the backend can resolve a safe resource identifier; remote assessment questions with run-scoped opaque IDs do not call the legacy material-question flag endpoint.

### Upload document preview

Each processed upload automatically receives a balanced study companion. The preview shows separate **Study document**, **Original file**, and **Original text** views. Original files and complete extracted text remain intact. Study document offers **Concise**, **Balanced**, and **Detailed** explanations, cached independently, plus PDF, LaTeX, and verbatim source-text downloads. PDFs separate the explanation from a full original extracted-text appendix.

Refresh upload detail for current state and a fresh signed original URL. Poll `GET /api/uploads/{id}/document?detail_level=...` sequentially; create missing/retry failed versions with `POST /api/uploads/{id}/document` and `{detail_level}`. Show study-document progress/errors and Retry document independently from source-extraction status. If extraction is still running, poll the upload status and display its processing progress; if extraction failed, explain that state and offer Retry upload processing only to users authorized to edit the source. Never label a failed upload as still processing. Closing or switching levels aborts client polling without cancelling the server job. Existing uploads create companions on first open. Original previews use signed backend URLs if IndexedDB lacks a copy. See the backend design for the full contract.

### SQLite study generation integration

Class-library calls use same-origin HTTP without requiring a bearer token in local SQLite mode. The Study tab refreshes backend uploads without replacing their browser IDs or local files. A source checklist shows each filename, kind, and processing status, with Select all, Clear, Notes only, and Syllabus only shortcuts. All uploads are initially selected; students can select any individual upload or combination. A live count summarizes the selection, and generation is disabled when nothing is selected. Choices survive returning from a saved quiz or flashcard player and are locked while generation runs.

Before generation, `course-sync.js` accepts the explicit browser IDs as `selectedUploadIds`, validates that they still belong to the class, maps only those records to backend UUIDs, retrieves their original files from IndexedDB (or uses pasted source text), and waits only for those uploads to be `ready`. Empty or stale explicit selections fail without falling back to the whole class. Unselected uploads are neither submitted nor polled. Local IDs remain stable for routing and file previews; backend UUIDs are persisted as `backendId` fields. Saved generated materials show their selected source filenames.

Study generation submits an explicit upload scope, keeps the returned pending material ID, and polls status sequentially. Quiz and practice-test generation can select a question language, including the explicit `zh-Hans` Simplified Chinese tag; this generation option does not expand the live tutor's language-lock allow-list. Pending and failed items remain visible for recovery; generation errors do not create template quizzes. Navigation stops client polling without claiming to cancel the server job. Old browser-only templates remain labeled as local drafts.

For quizzes and practice tests, the optional **Additional instructions** textarea is shown only for those types and is limited to 2,000 characters. The client trims it and omits the request field when blank. These preferences guide topic emphasis and style without changing the selected source scope. The server stores them separately from public material options and does not return them in material summaries or detail responses.

Study guides send selected styles in the `guide_styles` request array. When omitted, the backend defaults to `balanced`; it stores all selections and applies them as combined lower-priority presentation guidance. The singular `guide_style` string remains accepted for older clients. Deterministic citation-ID, schema, size, and document-render checks remain in force, and diagrams or interactive models use only validated app-owned templates. There is no independent factual reviewer model; students should inspect the cited sources because the authoring model can make mistakes.

Quiz and practice-test generation also offers a **Question language** selector, initially set from the student's saved tutor-language preference; “Match source language” leaves the generation language adaptive. The selected language and a secure `operation_id` are sent with the generation request. The browser stores only a SHA-256 request fingerprint and operation ID in session storage so the same request can be retried after a network interruption without saving instruction text. A confirmed creation clears the pending operation. A definitive provider failure clears it so a new operation can be started; a retry while a server operation is still running reuses its ID.

The class library supports inviting and joining members, leaving a class, and owner/manager membership controls. Invitation creation exposes expiration, use limits, and permission choices; backend capabilities remain independent. `Delete own contributions` and `Delete other contributions` are separate grants, and the latter applies only to another member's published contribution. Resource cards show personal versus shared scope and publication state, with author-scoped edit/delete controls, explicit publishing, and scheduled release where authorized. Students can create personal quizzes from readable class uploads without publishing them. Their personal materials are fetched from the owner-only library and remain available after leaving the source class; they are not copied into the shared browser cache. The UI hides unavailable actions, while all access and release rules remain enforced server-side. Forking and leaderboards are deferred. See [Shared classes design](shared-classes-design.md).

Remote quiz and practice-test material detail is treated as metadata only; question prompts, choices, rubrics, explanations, and answer keys are not copied into the browser's material object or shared class cache. Starting either mode creates or resumes a run whose public DTO contains an immutable question snapshot, run-scoped opaque question IDs, and per-run opaque option IDs. The player drops unknown DTO fields and renders only prompt, allowed level/concept labels, interaction metadata, and option display text. It submits the opaque IDs and free-response text to the backend; it performs no correctness calculation for remote assessments. Run status and feedback review are rendered only from the backend, and answer keys, correctness, rubrics, explanations, and citations appear only when `feedback_policy.feedback_available` is true. If a run lacks the safe public DTO, the player fails closed and offers a return to Study rather than falling back to material detail. A submission failure keeps the student's answers available for retry instead of displaying a fabricated local score. Remote flags and mapped upload preview/retry/delete actions also call the backend without a token prerequisite. See [the backend integration contract](backend-design.md#quiz-generation-integration-for-the-frontend-agent) for request formats and recovery rules.

Remote quizzes open a mode picker with **Standard** selected by default. Standard and Reasoning Mode use the same backend-owned run question snapshot and opaque option mapping; Reasoning Mode adds its persisted reasoning gate and per-question state. Browser-local quizzes and sample assessments keep their existing local Standard flow. Reasoning Mode answer controls stay editable while reasoning is checked. Text turns are submitted deliberately, finalized student transcription from the existing Live connection can be checked automatically, and interim speech/model speech never enter reasoning history. Show the durable reasoning history, evaluator follow-up, and authoritative state; only enable final submission after backend acceptance. Acceptance does not submit the answer and later reasoning turns do not revoke it. Confirm skip with “Are you sure you want to skip this question?”; cancel leaves drafts untouched. After submission, show only review fields released by the backend and label skips separately from graded answers. Reasoning Mode is untimed for timed practice tests; selecting Standard keeps the existing countdown. Draft answers and unsent text use per-run session storage, while the backend remains authoritative.

Remote quiz completion can also show only the backend's allowlisted outcome projection: response counts, server-awarded XP, and released correctness. The player never derives XP or correctness from answers. Correct-count and per-question correct/incorrect states are discarded unless the run's feedback policy releases them; participation XP is rendered only when returned by the server.

The Concepts manager panel shows retained merge history from the manager-only, no-store endpoint; ordinary members never request or render that history. Proposed and validated relationship cards display a visible direction warning when the backend marks `opposite_prerequisite`, reminding managers to inspect the cited evidence before approval.

### Tutor tab

Review integration fixes: PDFs/images opened on the tutor stage render at up to 1920 pixels on their longest edge, matching screen capture. Screen samples are dropped when WebSocket buffers are congested, including a projected-size check, so stale frames do not accumulate without bound.

Quiz/practice-test review tutoring stays on the results page. **Ask tutor about this** opens an inline chat and requests the first hint immediately; follow-up messages refine the explanation. Use `POST /api/study-tutor` with bounded alternating user/assistant messages. Remote reviews send saved material, opaque run-question, and attempt IDs only when the run's feedback policy makes review available; the server rechecks current access and release policy before loading any private grading context. Local/sample reviews send a bounded question/choices/answer/explanation object instead and get no private class context. Model selection and tutoring instructions stay server-owned. Render replies as escaped text with LaTeX support, show loading/error/Retry states, and abort requests on close, retake, Back, or navigation. Chats live only in memory for this player and are never saved to IndexedDB or session memory. Older queued Live Tutor questions remain readable for compatibility.

Study sets display `source_warnings` for changed/deleted uploads and partial source coverage. Source citations in quizzes, study guides, and flashcards open the original upload preview. PDF references open at the cited page; lecture references seek to the cited timestamp; slide references retain their visible locator. Missing sources show a recoverable error and leave the saved study set usable. Opening a citation does not start companion generation unless the student selects Study document.

Practice-test scoring failures preserve answers and resume the countdown against its original deadline. If time has expired, inputs stay locked and Retry scoring resubmits the frozen answers; retries never extend the deadline. Navigation cancels the countdown and ignores late submission responses.

The screen-sharing workspace described above, with the class selected. The page tells the backend the class ID when starting the session so the tutor gets that class's materials. Tutor answers that rely on an upload show a source chip linking to it.

- **Tutor materials.** The "Using <class> · All materials" pill opens a checklist of the class's uploads. The chosen IDs go to `POST /api/live-sessions` as `upload_ids`; choosing every upload sends none, so new uploads join automatically. The choice is kept per class in this browser (`tutor-materials.js`) and is locked while a session runs.
- **Open material.** Next to Share screen, this shows one of the class's uploads on the stage instead of the screen: PDFs (pdf.js, loaded only when needed, with its worker bundled as a plain `.js` chunk), images, and text from notes, documents, and slides (paginated, one page per slide). `material-stage.js` draws each page into a canvas whose `captureStream()` takes the display stream's place, so frame sampling, the preview, and annotation validation are unchanged. A page bar turns pages (marks clear on each turn) and Close pauses the stage like Pause. The material on stage is always added to the tutor's context. The file comes from browser storage or, failing that, the upload's signed backend URL fetched same-origin; nothing is saved. Audio and video uploads are not offered. The student can switch between the screen and a material without pausing; cancelling the screen picker keeps the material on stage.

## Accounts, guests, and access

Guests and logged-in users never share a screen or a cache. The backend enforces this; the client only mirrors it.

| Route | Who | Behavior |
| --- | --- | --- |
| `#/home` | Everyone | Welcome page. Guests see Log in and the configured account-request/guest options; logged-in users see Welcome back and Go to your classes. |
| `#/login`, `#/signup` | Guests | Standalone Log in and account request/create screens with no app header. The server decides whether signup is closed, pending admin approval, or immediate. A pending request never creates a browser session. A visitor who is already logged in sees who they are logged in as, with Go to your classes and Log out. |
| `#/account` | Logged-in only | Change username and email, change password (current password required; other devices are logged out), and Log out. A guest is sent to `#/login`. |
| `#/progress` | Logged-in only | Private learning progress from the signed-in learner's session. Shows server-owned XP, selected-window accuracy, concept estimate/status, demonstrated and confirmed levels, evidence count, history peak, and review-due notice. A guest is sent to `#/login`. |
| `#/classes`, `#/classes/:id/:tab` | Logged-in only | Class library, study, and class tutor. A guest is sent to `#/login`. |
| `#/study`, `#/guest` | Guests only when enabled | Local quick study. A logged-in visitor is sent to their classes; if guest access is disabled, guests are sent to log in. |
| `#/tutor` | Logged-in users, or guests when enabled | Quick tutor without class materials. |
| `#/admin` | Administrators only | Configures the app-wide OpenAI-compatible text route and lists pending signup requests with approve/reject. The backend checks the account's admin flag on every operation. Provider keys are write-only in the UI: the saved key is never returned, displayed, or stored by the browser. |

The header's top-right control depends on who is looking:

- **Guest:** a **Join Jot** menu with *Log in* and the configured signup action. Guest links appear only when guest access is enabled.
- **Logged in:** the account name with a menu of *Account*, *Your classes*, and *Log out*; administrators also see *Admin*.

The Admin page accepts a provider base URL, model identifier, and secret key for the app-wide OpenAI-compatible Chat Completions route. Saving applies immediately; leaving the key field blank preserves the saved key, and an explicit checkbox clears it. Administrators can remove the saved override to return to environment settings. The browser sends the key only in the administrator's same-origin request and does not persist or receive it. The backend uses the selected route for study generation, reasoning evaluation, and quiz-review tutoring. Typed messages in live tutoring, Gemini Live voice, and Gemini Files media transcription remain server-configured separately.

The primary navigation for signed-in users includes **Progress**. Its request is `GET /api/learning/progress?window_days=...`; the backend derives the account from the session cookie and does not accept an owner selector. The page renders only the returned private projection and labels mastery values as estimates. “Latest demonstrated level” is descriptive; only the backend's `current_level` represents a threshold-confirmed level. Per-concept evidence sufficiency shows the backend's valid-observation and independent-family counts as descriptive diagnostics; the interface explicitly says these counts do not establish mastery. Review-due status is a reminder, not a demotion. Selecting a concept loads its owner-private evidence, current released source citations, validated graph relationships, and dated estimate history. The relationship view preserves incoming/outgoing direction so prerequisite and containment labels describe the selected concept accurately. The Practice suggestions section loads the learner's chosen goal from `/api/learning/preferences`; changing the accessible goal selector saves it with `PUT` and refreshes recommendations. Starting a suggestion records its `active` engagement state before navigating to the server-provided, class-matched study route; continuing an already active suggestion navigates directly. Completion and dismissal remain separate state actions. The browser does not calculate mastery, XP, accuracy, correctness, or recommendations. No teacher or class-member progress view is exposed.

The class Study tab includes an opt-in **Mastery Ladder** panel for validated concepts. Students choose concepts and an objective-only or reasoning-required progression policy before creation. `mastery-ladder.js` uses the backend's owner-scoped ladder snapshot, opaque question and option IDs, and server mutations for answer submission, separate reasoning-sufficiency and rubric grading statuses, recovery, skip, and progression. An insufficient sufficiency decision displays its targeted follow-up. A rubric-provider failure keeps the already submitted answer and reuses its saved operation and current ladder version for retry; it does not resubmit reasoning or create a second answer. The browser never receives answer keys or decides correctness or level advancement. A run ID is kept in session storage, scoped to the signed-in account and class, only to reopen the authoritative run after refresh. Unsent answer and reasoning drafts are also kept in session storage, scoped by account, class, ladder, question, and draft kind, with bounded values and storage entries. Typing only updates local storage; it never submits a response. Drafts remain available after failed requests and are cleared only after a successful response or confirmed terminal skip. Reasoning, answer, and skip operation IDs are retained separately so an ambiguous network failure can safely retry the same operation; after pending or failed reasoning, the client reloads the authoritative ladder version before retrying. While that refresh is unavailable, mutation controls remain locked and the learner can retry the refresh. The interface distinguishes a separate recovery question from a new level and announces the server's returned progression without deriving correctness or XP. Each selected concept progresses independently, and skip confirmation explains that skipping creates no incorrect evidence. Local/sample classes keep their existing workflow and do not show this server-backed panel.

Guest pages are hidden and rejected by the backend by default. `ALLOW_GUEST_ACCESS=true` opts into quick tutoring and local study pages. `SIGNUP_MODE` is `closed` by default; `approval` accepts requests for administrator review, and `open` enables immediate registration. The first administrator is created by an operator using `python -m app.scripts.create_admin USERNAME`; there is no public administrator bootstrap endpoint.

Only the demo account (`is_demo` from the server) starts with the built-in sample classes and study sets and sees *Restore sample data*. Every other account starts with an empty class list and a *Create your first class* empty state; any sample classes cached by earlier builds are removed from their view.

Log in accepts a username or an email. Anything cached for an account (classes, uploaded files) is cleared on log out, when the server ends the session, and when a different account logs in on the same browser.

## Create your own Jot

The Account page has a character builder: pick one of 9 body colors, 5 faces (happy, wink, sleepy, surprised, big grin), and an accessory (none, round glasses, graduation cap, headphones, party hat, crown). A live preview updates as you choose; *Surprise me* picks at random and *Save my Jot* stores it on the account (`PATCH /api/auth/me` with `avatar`). The saved Jot appears in the header account menu. `avatar.js` draws it from three ids as SVG (a tilted note-block like the mascot, with a paper-colored sticker outline on accessories so they read on any color and in both themes); the ids must match the server's allowed lists. Every choice is a radio button with a text name, and the preview caption is a polite live region.

## Your Jot in the live tutor

The person's own Jot (their saved avatar, or the default) sits at the top of the tutor conversation panel and reacts to the live session. `jot-buddy.js` decides one state at a time, in this priority: **error**, **speaking**, **thinking**, **listening**, then **ready**, **connecting**, or **offline**, and shows a short status line for it.

| State | When | Animation |
| --- | --- | --- |
| offline / connecting | No session yet, or starting | Slow breathing, a little dimmed |
| ready | Connected and idle | Breathing and an occasional blink |
| listening | Microphone on and the student's voice is above a small threshold | Leans in, eyes wide |
| thinking | From sending a typed question until the tutor starts answering (15 s timeout) | Slow wobble, eyes looking up and aside |
| speaking | While queued tutor audio is playing | The mouth opens and closes with the loudness of the real voice, and the body bounces with it |
| error | The session dropped | Dimmed |

### Arms, legs, and expressions

The live Jot is drawn at 136 px in a fixed-height row with no card behind it. Instead of a status line it has a **speech bubble**: live captions of what the tutor is saying while it talks (the latest sentence, up to three lines, math typeset), short words with animated dots while listening or thinking, "Lost the connection" on error, and nothing at rest. The bubble fades in place, so nothing shifts. It is hidden from screen readers, because the conversation list already announces the tutor's words; the state is kept in a visually hidden status for them. Only the animated version has doodled stick arms and legs, drawn in ink (`currentColor`, the page text color, so they show in both themes). Arms have a shoulder and an elbow; legs pivot at the hip. Poses follow the state: arms relaxed when ready, a hand cupped to the ear while listening, chin on a hand while thinking, and gesturing hands while talking. It waves hello when a session connects.

`expressionFor(text)` in `jot-buddy.js` picks an expression for about four seconds. It reacts **while the tutor's words stream in**: the live session reports the growing transcript (`onTutorText`), and the opening of each reply is checked, so "Exactly!" shows as the tutor says it rather than after a long explanation. Confusion is decided when the turn finishes, because a clarifying question often comes at the end. One reaction per turn; an interruption starts a fresh turn. Phrases are checked in this order, so "Not quite, but great effort" counts as concerned:

| Expression | Example phrases | Face and pose |
| --- | --- | --- |
| concerned | "not quite", "almost", "incorrect", "careful", "mistake", "try again" | Worried brows, hands together, knees in, a bead of sweat |
| celebrate | "exactly", "correct", "that's right", "that's it", "you've got it", or a reply opening with "Yes!" or "Right!" | Happy closed eyes, big grin, arms up, a hop with kicking legs, sparkles |
| proud | "great job", "well done", "nice work", "great observation", "good catch", or a reply opening with "Great!" or "Nice!" | Happy closed eyes, smug smile, hands on hips, a wide stance, sparkles |
| confused | "I'm not sure", "could you clarify", "can you share", "I can't see" | Raised and tilted brows, wavy mouth, head tilt, scratching its head, a "?" |

An expression can play while the tutor talks (the talking mouth takes over), a new one replaces the old, and losing the connection clears it. The phrases are English only; with a language lock on another language the Jot still animates by state but shows fewer expressions. The static Jot used everywhere else is unchanged.

### Microphone button

The mic button shows its state at a glance: a microphone with a red slash while it is off or muted, a solid primary-colored button while it is on, and a pulsing ring while the browser is asking for permission. The label and `aria-pressed` say the same thing for screen readers.

Loudness comes from `AudioController`, which routes tutor speech through an `AnalyserNode` on the way to the speakers (`getOutputLevel()` is 0 to 1, `isSpeaking()` is true while queued audio is playing). Barge-in (`interrupted`) stops the talking at once. CSS in `styles.css` does all the moving, using `--mouth` and `--bounce` custom properties that the controller updates only while speaking. With `prefers-reduced-motion: reduce`, breathing, blinking, leaning, wobbling, and bouncing stop, but the mouth still opens and closes because that carries information. The animation parts exist only when `avatarSvg(..., { animated: true })` is used; every other place draws the plain static Jot.

## Math in AI text

The tutor and generated study material sometimes write LaTeX (for example "the $r\theta$-plane"). `math.js` formats it at runtime wherever AI text is shown: tutor chat bubbles, quiz and practice-test questions, choices and explanations, flashcards, and study guides.

- **Delimiters and fallback detection:** `$...$` and `\(...\)` inline, `$$...$$` and `\[...\]` display, plus `math`, `equation`, `align`, `gather`, and matrix environments. Bare TeX commands, subscript/superscript identifiers such as `r_1`, and common Unicode math symbols are detected without delimiters. Simple unwrapped equations are recognized when their operators and surrounding tokens make them distinguishable from prose. KaTeX validates each candidate; invalid notation remains readable as its original text. Prices such as "$5 and $10" stay text.
- **Two steps:** `mathHtml(text)` returns safe HTML immediately (text escaped, math shown as its source); `typesetMath(root)` then loads KaTeX and replaces each source with rendered math plus MathML for screen readers. KaTeX is a separate lazy chunk, fetched only when a page has math.
- **Safe by construction:** all non-math text and all TeX are HTML-escaped, KaTeX runs with `trust: false` (no links, HTML, or images from TeX), expressions are capped at 2,000 characters and 200 per text, and anything KaTeX cannot parse stays as readable text.
- **Tests:** `frontend/tests/math.test.js` covers the parser, escaping, limits, and KaTeX rendering for unwrapped symbols copied from the active asa guide preview.

## Saved tutoring recaps

The student approved frontend integration for session memory on 2026-10-11. **Tutor settings** contains **Remember sessions**, off by default and persisted by the backend per account/class (general tutoring is separate). **Clear saved recaps** deletes that scope's history. Show recent recaps with their dates in an expandable list. Guests receive a sign-in explanation. Preference and deletion controls are locked during tutoring or saving. Save outcomes appear as notices when the settings modal is closed. Retry is visible only for a memory-loading error.

The account-scoped API is `GET|DELETE /api/tutor-memory?class_id=<optional backend UUID>` and `PATCH /api/tutor-memory` with `{class_id: null | UUID, enabled: boolean}`. Use cookie auth. Do not store recaps or preferences in browser persistence. Backend class IDs come from existing class mapping; API failures remain visible and do not block ordinary tutoring.

End stops capture, input, playback, and marks immediately, then sends `end_session` over the existing WebSocket. Show saving progress while waiting at most 30 seconds for `session_ended.memory_status`, then close. Report failed/unconfirmed saves honestly. Navigation without End and failed sessions close without saving; a save already requested by End may finish after navigation. No screenshot, audio, or full transcript is persisted.

The existing **Reset memory** control is renamed **Restart conversation** to distinguish ephemeral conversation reset from deleting saved recaps. It opens a fresh connection with `use_saved_memory: false`, keeping the preview and leaving the microphone off. Saved recaps remain available to later normal sessions. The restarted session can still save its own recap when ended if memory is enabled.

## Tutor language lock

Tutor settings has a **Language lock** picker under *Check my steps*: *No lock (match me)* or one of 11 languages (English, Spanish, French, German, Portuguese, Italian, Mandarin Chinese, Japanese, Korean, Hindi, Arabic). With a lock, the tutor speaks and writes only in that language, even when the student asks in another language or the screen shows one. Like *Check my steps*, it is chosen before a session and disabled while one runs. The choice is remembered in the browser (`localStorage["jot-tutor-language"]`, validated on load) and sent as `language` when a session starts; with no lock nothing is sent. `tutor-language.js` holds the list, which must match the server's.

## Light and dark mode

The theme is a choice, not just the system setting. `<html data-theme="light|dark">` selects the color tokens in `styles.css`. A small script in `index.html` applies the saved choice (`localStorage["jot-theme"]`) before first paint, so there is no flash; with no saved choice it follows `prefers-color-scheme`, including live changes, until the person picks one. `theme.js` owns the toggle: a sun/moon button in every header and floating in the corner of the welcome, login, and sign-up screens. It works if storage is blocked (the choice then lasts for the visit). Both themes keep text at 4.5:1 or better and control borders at 3:1 or better.

## Frontend modules

- `app.js` — page initialization, event wiring, top-level session state, and user-facing errors.
- `screen-capture.js` — browser permission flow, `MediaStream` lifecycle, preview sizing, and sampled JPEG frames.
- `live-session.js` — one-use backend ticket request, same-origin WebSocket lifecycle, media/input events, and server response events.
- `audio.js` — optional microphone input, tutor audio playback, interruption, and mute/pause behavior.
- `annotations.js` — tool argument validation, frame-to-preview coordinate mapping, SVG/canvas drawing, expiration, and dismissal.
- `api.js` — same-origin calls to the Python backend.
- `course-sync.js` — browser-to-backend class/upload ID mapping, source submission, and sequential upload/material status polling.
- `router.js` — hash routes for the classes, library, study, and tutor pages.
- `classes.js` — class list and class create/rename/delete.
- `library.js` — upload zone, file validation, upload progress, status polling, upload detail view, transcript player, and syllabus outline.
- `source-search.js` — safe rendering of bounded source search results and exact revision/chunk provenance in the class Library.
- `concept-graph.js` — read-only class graph, manager proposal/edit controls, extraction status polling, and exact cited-source preview.
- `mastery-ladder.js` — validated-concept selection, owner-scoped ladder create/resume, safe question rendering, and server-authoritative answer/reasoning/skip mutations.
- `progress.js` — private XP/accuracy/mastery projection, concept evidence and dated history, and private recommendation actions.
- `study.js` — generate panel, saved materials list, and generation status.
- `study-guide/normalize.js`, `study-guide/force-components.js`, and `study-guide/activity-blocks.js` — adapt legacy sections to readable blocks, validate the fixed template vocabulary, render/mount the force activity and the graph, response, circuit, molecule, animal-cell map, and process-step templates.
- `quiz-player.js` — quiz and practice test taking, timer, submission, and results.
- `flashcards.js` — flashcard deck view and sorting.

Keep the backend session in one controller so capture, audio, transcript, and annotation code share one WebSocket.

## Session flow

1. The student presses **Share screen**. In that click handler, call `navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })`. Let the browser choose the source; do not attempt to select one from JavaScript or reuse permission silently.
2. Show the returned stream in a `<video>` element using `srcObject`. The local preview does not pass through the backend.
3. Request a one-use ticket from `POST /api/live-sessions`, then open `WS /api/live` on the same origin using the ticket subprotocol. If ticket creation or connection fails, stop capture tracks and show a retry action.
4. While screen sharing is active, sample the preview to JPEG no more than once per second, scaling its longest dimension to at most 1920 pixels without enlarging small sources. Start at quality 0.85 and reduce quality or dimensions to fit the existing byte limit. Send each frame over the backend WebSocket with a client-generated `frame_id` and keep a short in-memory map of frame IDs to JPEG dimensions, original source dimensions, timestamps, and bounded grayscale comparison thumbnails. Stop clears the frame map and capture/comparison canvases.
5. Send typed questions as text. When the student enables the microphone, request microphone permission separately and stream audio over the same WebSocket. Screen-only plus typed input remains available when microphone permission is denied.
6. Play returned tutor audio and show transcript events as captions/conversation text. The backend translates provider events into a small, provider-neutral event protocol.
7. When the student ends screen sharing, stop the tracks, close the backend session, clear frame buffers, and remove annotations. When only the microphone is paused, stop sending audio while keeping text/screen tutoring available.

The browser-to-backend event contract is intentionally separate from provider payloads. The backend owns all provider connections, model configuration, instructions, and tool execution.

The session ticket request may include `mode: "conversation"` or `mode: "check_steps"`, alongside an optional backend class UUID. The default is conversation. The browser sends only the selected mode, never coaching instructions or speech detection settings. Coaching uses the same audio, frame, annotation, and interruption events. Resuming a paused screen retains the selected mode and existing connection.

When sharing is paused or the browser's track ends, send `{ "type": "screen_paused" }` through the existing gateway and clear local marks. The backend owns the context notice and invalidates its current frame. The browser contains no provider prompts. Restart conversation closes the old WebSocket, clears the in-memory frame map, transcript, and marks, and obtains a fresh ticket through the existing endpoint with `use_saved_memory: false`. It does not replay conversation history, saved recaps, or old frames. On readiness, sample the still-authorized preview again; no new screen picker or microphone request is started automatically. End and navigation cancel a pending restart.

When Check my steps is enabled, show a reminder to turn on the microphone, explain one step at a time, and pause for feedback. Feedback is AI-generated and may miss errors. Microphone permission stays separate and explicit. The tutor does not promise to speak over uninterrupted student speech.

### Microphone controls

- Voice input stays off until the student clicks the microphone button in a connected session. The browser requests microphone permission separately from screen sharing. Clicking the button again stops microphone tracks; text and screen tutoring continue.
- Show a permission-request state with a cancel action, a listening indicator, the selected microphone label, and an input level meter. If the device becomes muted or disconnected, show its current state. Explain denied permission, missing devices, and device-access failures with a retry instruction.
- Request mono input with echo cancellation, noise suppression, and automatic gain control when supported. The audio worklet resamples input to 16 kHz and sends little-endian 16-bit PCM chunks over the existing `audio` event. The level meter stays local and measures input volume; it does not perform speech recognition.
- Keep continuous microphone input available while enabled. The Live service detects speech pauses; send `audio_end` when input stops. Stop queued tutor speech when the service reports an interruption.
- Stop microphone tracks and playback on session failure, End, navigation away from the tutor, and page exit. Ignore late permission results from a cancelled request so they cannot restart capture. Never record or save live audio.

## Annotation rendering

`add_annotations` is the only provider-requested visual action. The backend attaches the exact frame reference pinned to the question or automatic review, rather than rebinding to a frame arriving during generation. Each item contains a shape, a short label, and a normalized rectangle. The student authorized the annotation integration fixes on 2026-10-11. The frontend:

- Accepts only known shapes (`pointer`, `rectangle`, `underline`, `note`) and finite coordinates in the `0..1` range.
- Rejects unknown frames, old frame IDs, out-of-bounds regions, long labels, and excessive mark counts.
- Maps normalized coordinates to the displayed video content area, accounting for `object-fit`, letterboxing, and resize. Prefer an SVG `viewBox` tied to the source frame dimensions so marks stay aligned.
- Uses the original source dimensions for the SVG `viewBox` and centered aspect-preserving scaling to match the video preview. Downscaled JPEG frames retain their source dimensions for validation, so normalized coordinates still align. Rejects frames older than ten seconds, limits the total active marks to eight across requests, discards marks if the shared source changes dimensions, and recalculates label sizing when the preview is resized.
- Places each explanatory label inside its region, wraps long text, and reduces its font size to fit. Labels cannot spill outside the box. For regions too small for readable text, show an abbreviated label or omit it in the preview; the annotation list retains the complete explanation.
- Renders marks as pointer-events-disabled UI elements; they must not intercept input to the video or conversation controls.
- Gives each mark a dismiss button in an accessible list and expires marks automatically. If an annotation references a stale frame, return an error tool response and display the spoken explanation without a mark.
- Captures at most 1920 pixels along the long dimension, without upscaling, at JPEG quality 0.85. Reduce quality or dimensions if the decoded JPEG would exceed 2,200,000 bytes; retain one sampled frame per second and the existing WebSocket formats.
- Keeps an in-memory grayscale comparison thumbnail of at most 960 pixels along the long dimension with each of the eight frame references. Do not retain old JPEGs, write comparison data to storage, or send comparison data to the backend. Release it when capture ends or memory resets.
- Compares the marked region, including a small surrounding margin, with the current preview at admission, on sampled frames, and every 250 ms while marks are present. Remove changed targets, including scroll/window movement and edits in that region. Unrelated changes outside the region and narrow cursor-like differences are ignored. Missing comparison data or changed source dimensions fail closed in the application. This heuristic cannot guarantee pixel-perfect placement or track moving targets.
- Draws an underline at the bottom of its target bounds, a pointer arrow toward their center, a rectangle outline, and a distinct note box. Small text regions show their complete explanation in the annotation list rather than covering the text with a label. Preserve the centered SVG/video scaling.
- Clears marks on a provider interruption as well as pause, End, source replacement, and memory reset.

The example schema and coordinate convention live in [the product design](design.md#annotation-contract). The browser performs final frame and geometry validation because it owns the preview and frame map.

## Client state model

Represent the session with explicit states instead of inferring status from whether a stream or socket happens to exist:

| State | Meaning | Main actions |
| --- | --- | --- |
| `idle` | No capture or Live session | Share screen |
| `selecting_screen` | Browser picker is open | Cancel |
| `connecting` | Capture selected; token/session setup in progress | Cancel / end |
| `active` | Live session and selected inputs are available | Ask, mute, pause screen, end |
| `screen_paused` | Screen track ended; text/audio may continue | Share screen again, end |
| `reconnecting` | Live connection is recovering | End, retry if recovery fails |
| `ending` | Capture and audio are stopped; waiting for recap acknowledgement | Navigation remains available; new sessions wait for completion |
| `ended` | Tracks and Live session are closed | Start another session |
| `error` | Permission, network, or setup failed | Retry, change browser/source |

Uploads and generated materials have their own small status model, driven by the backend: `uploading → processing → ready | failed` for uploads and `generating → ready | failed` for study materials. Keep these separate from the Live session state so a long lecture transcription doesn't block tutoring.

The browser may stop a screen track when the user ends sharing from its own control. Listen for the track's `ended` event and immediately stop frame sending and update the UI.

## Privacy, accessibility, and failure behavior

- Explain that selected screen frames and enabled microphone audio pass through the Screen Tutor backend for live tutoring before the student starts. Screen Tutor does not save them.
- Keep a visible sharing indicator and immediate End/Stop controls. A Screen pause action should stop the capture track; resuming invokes the browser picker again.
- Keep captured frames, transcript, and marks in memory only by default. Clear them when the session ends.
- Uploaded materials and generated study items are stored by the backend in the student's account. Make delete easy to find for every upload, material, and class.
- Show distinct errors for unsupported file type, file too large, upload interrupted, processing failed, and generation failed, each with a retry where it makes sense.
- Never log the one-use session ticket or include it in app analytics.
- Use semantic buttons, visible focus, keyboard operation, captions, sufficient contrast, and reduced-motion support.
- Show distinct errors for denied permissions, unsupported capture, failed ticket issuance, backend session disconnect, and expired session. Do not continue sending a stream after an error or user stop.

## Frontend acceptance criteria

Run `npm test --prefix frontend` for the isolated preview/annotation regressions and `npm run build --prefix frontend` for the production bundle. The regression tests use synthetic elements and never capture screen content, call Gemini, or modify saved course data. Actual SVG letterboxing and label layout require a browser integration check.

- A student can share a target through the browser picker and see a synchronized preview in the page.
- Typed questions work without microphone permission; voice can be enabled separately.
- Tutor speech plays and its transcript is visible.
- A valid annotation lands over the correct region of the preview; invalid or stale annotations are dropped safely.
- Pause and End stop the corresponding media immediately and visibly.
- The browser bundle contains no provider SDK, model configuration, or provider credential.
- A student can create classes (for example, Math and Science) and see each class's uploads and study materials kept separate.
- A student can upload notes, a document, a syllabus, slides, and a lecture video into a class and see each one's progress until it is ready.
- A ready lecture shows a timestamped transcript that seeks the player when clicked.
- A student can generate a quiz, practice test, study guide, and flashcard set from a class's uploads, take the quiz or test, and see a score with explanations and source links.
- The Math 151 demo class starts with basic-algebra quiz and practice-exam examples; students can submit answers, review the answer key and explanations, retake them, and see saved attempt scores.
- A student can open the Tutor tab from a missed question and ask about it.
