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

- **Header:** connection status, screen-sharing status, microphone status, and End session.
- **Screen stage:** video preview, annotation layer, source/re-share controls, and an explicit pause action.
- **Conversation:** user questions, tutor text transcript, connection/retry notices, and a Clear conversation control.
- **Composer:** text question input, Send, and microphone toggle.
- **Annotation controls:** dismiss one mark and Clear marks. Marks expire automatically after a short interval.
- **Class selector:** shows which class's materials the tutor is using, or “No class” for a general session.
- **Check my steps:** an optional checkbox before Share. When enabled, the tutor gives short corrective hints at pauses while the student explains a solution. The control is locked during a session; End unlocks it for the next session.

## Class library and study pages

### Page map

```text
Classes ─┬─ Math 151 ─┬─ Library   (uploads: notes, docs, syllabus, slides, lectures)
         │            ├─ Study     (generate + open quizzes, practice tests, guides, flashcards)
         │            └─ Tutor     (screen-share workspace using this class's materials)
         └─ Biology ── …
```

**Classes page.** A grid of class cards (name, color/icon, upload count, next exam from the syllabus if known) plus **New class**. Each card has rename and delete actions; delete asks for confirmation because it removes the class's uploads and study materials.

**Class page.** A header with the class name and three tabs: Library, Study, and Tutor.

### Library tab (uploads)

The local SQLite workspace calls class/upload APIs without an access-token prerequisite. Existing browser route and IndexedDB IDs stay stable; a persisted `backendId` identifies the corresponding backend class or upload. Library refresh merges remote records into those mappings without discarding locally retained source files. New classes and uploads save through the API, with local files retained for recovery after network failures. Pasted notes use a multiline field with the backend's 1,000,000-character limit. Rename and deletion use the mapped backend UUID and surface API failures.

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
- **Validation before upload:** reject unsupported types and files over the size/length limit with a clear message, and don't send them.
- **Upload detail view:** shows the original (PDF/slide viewer, image, or video/audio player) beside the extracted text. Lectures show a timestamped transcript; clicking a line seeks the player. The student can edit extracted text, rename, change kind, or delete.
- **Syllabus outline:** when a syllabus is ready, show its parsed topics and exam dates at the top of the library so the student can check and correct them.
- **Upload notice:** before the first upload, tell the student that uploaded content is stored in their account and sent to Google's Gemini API for processing.

### Study tab (generated materials)

- **Generate panel:** pick a type (Quiz, Practice test, Study guide, Flashcards); a scope (whole class, selected uploads, syllabus topics, or an upcoming exam such as “Midterm 1”); and options (number of questions, difficulty, time limit for practice tests). Show a generating state and allow cancel.
- **Saved materials list:** every generated item with type, scope, date, and last score. The student can open, retake, regenerate, or delete it.
- **Quiz / practice test player:** one question at a time or full-page mode, multiple-choice, true/false, and short/free-response inputs, an optional timer for practice tests, and Submit. The results page shows the score, correct answers, explanations, and source links (page/slide number or lecture timestamp that opens the upload detail view). Each missed question has **Ask the tutor**, which opens the Tutor tab with that question as context.
- **Study guide view:** rendered headings, key terms, formulas (use KaTeX or MathJax for math), and source links; printable.
- **Saved guide files:** remote guides offer Download PDF and Download LaTeX. Files remain saved with the guide; older guides export from stored content on first download. Practice tests display their generated instructions above the assessment form.
- **Flashcards view:** flip card with keyboard support (Space to flip, arrows to move), “Got it” / “Study again” sorting, and shuffle.
- **AI-generated label and Flag:** every generated item is labeled as AI-generated, and each question or card has a Flag as wrong action.

### Upload document preview

Each processed upload automatically receives a balanced study companion. The preview shows separate **Study document**, **Original file**, and **Original text** views. Original files and complete extracted text remain intact. Study document offers **Concise**, **Balanced**, and **Detailed** explanations, cached independently, plus PDF, LaTeX, and verbatim source-text downloads. PDFs separate the explanation from a full original extracted-text appendix.

Refresh upload detail for current state and a fresh signed original URL. Poll `GET /api/uploads/{id}/document?detail_level=...` sequentially; create missing/retry failed versions with `POST /api/uploads/{id}/document` and `{detail_level}`. Show progress/errors and Retry document independently from extraction status. Closing or switching levels aborts client polling without cancelling the server job. Existing uploads create companions on first open. Original previews use signed backend URLs if IndexedDB lacks a copy. See the backend design for the full contract.

### SQLite study generation integration

Class-library calls use same-origin HTTP without requiring a bearer token in local SQLite mode. The Study tab refreshes backend uploads without replacing their browser IDs or local files. A source checklist shows each filename, kind, and processing status, with Select all, Clear, Notes only, and Syllabus only shortcuts. All uploads are initially selected; students can select any individual upload or combination. A live count summarizes the selection, and generation is disabled when nothing is selected. Choices survive returning from a saved quiz or flashcard player and are locked while generation runs.

Before generation, `course-sync.js` accepts the explicit browser IDs as `selectedUploadIds`, validates that they still belong to the class, maps only those records to backend UUIDs, retrieves their original files from IndexedDB (or uses pasted source text), and waits only for those uploads to be `ready`. Empty or stale explicit selections fail without falling back to the whole class. Unselected uploads are neither submitted nor polled. Local IDs remain stable for routing and file previews; backend UUIDs are persisted as `backendId` fields. Saved generated materials show their selected source filenames.

Study generation submits an explicit upload scope, keeps the returned pending material ID, and polls status sequentially. Pending and failed items remain visible for recovery; generation errors do not create template quizzes. Navigation stops client polling without claiming to cancel the server job. Old browser-only templates remain labeled as local drafts.

Remote quiz detail omits answer keys. Submit choice IDs, true/false values, and short-answer text to the backend attempts endpoint for scoring and answer explanations. A submission failure keeps the student's answers available for retry instead of displaying a fabricated local score. Remote flags and mapped upload preview/retry/delete actions also call the backend without a token prerequisite. See [the backend integration contract](backend-design.md#quiz-generation-integration-for-the-frontend-agent) for request formats and recovery rules.

### Tutor tab

The screen-sharing workspace described above, with the class selected. The page tells the backend the class ID when starting the session so the tutor gets that class's materials. Tutor answers that rely on an upload show a source chip linking to it.

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
- `study.js` — generate panel, saved materials list, and generation status.
- `quiz-player.js` — quiz and practice test taking, timer, submission, and results.
- `flashcards.js` — flashcard deck view and sorting.

Keep the backend session in one controller so capture, audio, transcript, and annotation code share one WebSocket.

## Session flow

1. The student presses **Share screen**. In that click handler, call `navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })`. Let the browser choose the source; do not attempt to select one from JavaScript or reuse permission silently.
2. Show the returned stream in a `<video>` element using `srcObject`. The local preview does not pass through the backend.
3. Request a one-use ticket from `POST /api/live-sessions`, then open `WS /api/live` on the same origin using the ticket subprotocol. If ticket creation or connection fails, stop capture tracks and show a retry action.
4. While screen sharing is active, sample the preview to JPEG no more than once per second, scaling its longest dimension to at most 1280 pixels without enlarging small sources. Send each frame over the backend WebSocket with a client-generated `frame_id` and keep a short in-memory map of frame IDs to JPEG dimensions, original source dimensions, and timestamps. Stop clears the frame map and the sampling canvas.
5. Send typed questions as text. When the student enables the microphone, request microphone permission separately and stream audio over the same WebSocket. Screen-only plus typed input remains available when microphone permission is denied.
6. Play returned tutor audio and show transcript events as captions/conversation text. The backend translates provider events into a small, provider-neutral event protocol.
7. When the student ends screen sharing, stop the tracks, close the backend session, clear frame buffers, and remove annotations. When only the microphone is paused, stop sending audio while keeping text/screen tutoring available.

The browser-to-backend event contract is intentionally separate from provider payloads. The backend owns all provider connections, model configuration, instructions, and tool execution.

The session ticket request may include `mode: "conversation"` or `mode: "check_steps"`, alongside an optional backend class UUID. The default is conversation. The browser sends only the selected mode, never coaching instructions or speech detection settings. Coaching uses the same audio, frame, annotation, and interruption events. Resuming a paused screen retains the selected mode and existing connection.

When Check my steps is enabled, show a reminder to turn on the microphone, explain one step at a time, and pause for feedback. Feedback is AI-generated and may miss errors. Microphone permission stays separate and explicit. The tutor does not promise to speak over uninterrupted student speech.

### Microphone controls

- Voice input stays off until the student clicks the microphone button in a connected session. The browser requests microphone permission separately from screen sharing. Clicking the button again stops microphone tracks; text and screen tutoring continue.
- Show a permission-request state with a cancel action, a listening indicator, the selected microphone label, and an input level meter. If the device becomes muted or disconnected, show its current state. Explain denied permission, missing devices, and device-access failures with a retry instruction.
- Request mono input with echo cancellation, noise suppression, and automatic gain control when supported. The audio worklet resamples input to 16 kHz and sends little-endian 16-bit PCM chunks over the existing `audio` event. The level meter stays local and measures input volume; it does not perform speech recognition.
- Keep continuous microphone input available while enabled. The Live service detects speech pauses; send `audio_end` when input stops. Stop queued tutor speech when the service reports an interruption.
- Stop microphone tracks and playback on session failure, End, navigation away from the tutor, and page exit. Ignore late permission results from a cancelled request so they cannot restart capture. Never record or save live audio.

## Annotation rendering

`add_annotations` is the only provider-requested visual action. The backend attaches the `frame_id` of the latest frame it forwarded. Each item contains a shape, a short label, and a normalized rectangle. The frontend:

- Accepts only known shapes (`pointer`, `rectangle`, `underline`, `note`) and finite coordinates in the `0..1` range.
- Rejects unknown frames, old frame IDs, out-of-bounds regions, long labels, and excessive mark counts.
- Maps normalized coordinates to the displayed video content area, accounting for `object-fit`, letterboxing, and resize. Prefer an SVG `viewBox` tied to the source frame dimensions so marks stay aligned.
- Uses the original source dimensions for the SVG `viewBox` and centered aspect-preserving scaling to match the video preview. Downscaled JPEG frames retain their source dimensions for validation, so normalized coordinates still align. Rejects frames older than ten seconds, limits the total active marks to eight across requests, discards marks if the shared source changes dimensions, and recalculates label sizing when the preview is resized.
- Places each explanatory label inside its region, wraps long text, and reduces its font size to fit. Labels cannot spill outside the box. For regions too small for readable text, show an abbreviated label or omit it in the preview; the annotation list retains the complete explanation.
- Renders marks as pointer-events-disabled UI elements; they must not intercept input to the video or conversation controls.
- Gives each mark a dismiss button in an accessible list and expires marks automatically. If an annotation references a stale frame, return an error tool response and display the spoken explanation without a mark.

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
