# Screen Tutor — Product and Technical Design

**Project:** HackRU Fall 2026  
**Status:** Design draft  
**Working name:** Screen Tutor

Implementation detail lives in the [frontend design](frontend-design.md) and [backend design](backend-design.md).

## Summary

Screen Tutor is an AI tutor that runs in a browser and observes a screen the student explicitly shares. The student asks a question by voice or text. The tutor responds conversationally, refers to visible content, and can place a small number of temporary marks—such as a pointer, underline, or note—over the relevant region in the web app's live preview.

The browser owns screen capture and drawing. Gemini interprets the shared frames and returns speech, text transcription, and structured annotation requests. The web app validates those requests and draws the marks locally over its preview. It never gives the model general mouse, keyboard, shell, or application-control access.

Students can also upload their own course materials (notes, documents, syllabi, lecture slides, and recorded video or audio lectures) and organize them by class (for example, Math, Biology, or History). Each class has its own tutor context: when the student studies in a class, the tutor grounds its answers in that class's materials, and it can generate quizzes, study guides, practice exams, and flashcards from them.

**Scope note:** a regular web page can show annotations over the shared-screen preview inside that page. It cannot draw a persistent overlay over another desktop application. The MVP keeps the preview visible beside the conversation so the student can see the marks in context.

## Goals

- Let a student get help about the work currently visible on their display.
- Keep explanations conversational and anchored to what the tutor can actually see.
- Make it easy to point at a particular equation, line of code, diagram, or UI element.
- Keep the student in control of screen and microphone sharing, annotations, and session lifetime.
- Make the first useful interaction short: share, ask, understand, continue working.
- Let students upload their own notes, documents, syllabi, lecture slides, and video/audio lectures, and organize them by class.
- Generate study materials (quizzes, study guides, practice exams, flashcards) from a class's uploaded materials.
- Give each class a tutor that uses that class's notes as context, so answers match what the student is actually learning.

## Non-goals for the MVP

- Editing the student's document, code, or application for them.
- Clicking, typing, scrolling, or running commands on the student's behalf.
- Recording a lesson, retaining screenshots or audio, or building behavioral student profiles. (Notes and study materials that the student explicitly uploads or saves are stored; see [Notes and study materials](#notes-and-study-materials).)
- Supporting multiple students, classrooms, teacher dashboards, or assignment grading.
- Guaranteeing perfect OCR or pixel-precise visual grounding. The student can correct or dismiss a mark.

## Primary user flow

1. **Start a session.** The student opens Screen Tutor and clicks Share. The browser's screen-sharing picker lets them choose a display, application window, or browser tab. The page shows the captured preview and requests microphone permission separately.
2. **Check sharing.** A persistent indicator shows that screen and/or microphone input is live. The student can pause either stream or end the session at any time.
3. **Ask for help.** The student asks aloud or types a question, such as “Why does this step follow?” or “Can you give me a hint for this error?”
4. **Get a grounded response.** Gemini sees recent sampled frames and the student's question. The tutor answers in voice, with a visible transcript or concise response card.
5. **See a mark when useful.** The tutor can request a local pointer, underline, box, or note linked to the current shared frame. The page positions it over the preview and shows a matching short explanation.
6. **Continue or clear.** Marks expire after a short time or can be dismissed individually. The student can ask a follow-up, pause sharing, or end the session.

### Notes and study flow

1. **Create a class.** The student creates a class such as “Math 151” or “Biology” from the Classes page.
2. **Upload course materials.** The student uploads notes, documents, a syllabus, lecture slides, or a recorded video/audio lecture into that class, or pastes text. The app extracts text (and transcribes lectures) and shows each upload's processing status.
3. **Study with the class tutor.** When the student starts a tutoring session inside a class, the tutor gets that class's notes as reference material, alongside any shared screen.
4. **Generate study materials.** The student picks a class (and optionally specific uploads, lectures, or syllabus topics) and asks for a quiz, study guide, practice exam, or flashcard set. The app generates it from those notes and opens it.
5. **Practice and review.** The student takes a quiz or practice exam in the app, sees a score, and can ask the tutor to explain any question they missed.

## Product behavior

### Tutor style

- Start with a hint or a clarifying question instead of immediately solving the whole problem.
- Explain what is visible and name uncertainty when text or layout is unclear.
- Keep spoken turns brief and let the student interrupt.
- Use marks sparingly; each one should clarify a spoken explanation.
- If a location cannot be identified confidently, describe it (“the second line under the graph”) or ask the student instead of inventing a precise mark.

### Session controls

- Let the browser's source picker offer available displays, windows, and tabs; do not start sharing silently.
- Show a visible, persistent capture state and the selected target.
- Provide separate pause/resume controls for screen and microphone input, plus an obvious end-session action.
- Show when annotations are active. Let the student dismiss one mark or clear all marks.
- On disconnect or permission loss, stop sending media and explain how to resume.

### Notes and study materials

**Classes.** Every note and generated material belongs to exactly one class. The student can create, rename, and delete classes, and move notes between them. Deleting a class deletes its notes and materials after a confirmation.

**Course materials.** In this doc, “notes” means any material the student uploads to a class. Each upload has a kind:

| Kind | Example inputs | How it's processed |
| --- | --- | --- |
| Notes | Typed notes, photos/scans of handwritten notes, pasted text | Text parsing; Gemini for images and handwriting |
| Documents | PDF, DOCX, TXT/Markdown readings, handouts, homework sheets | Text parsing; Gemini for scanned PDFs |
| Syllabus | Course syllabus (PDF/DOCX/text) | Text extraction plus a structured outline of topics, weeks, and exam dates |
| Lecture slides | PDF or PPTX slide decks | Per-slide text extraction; Gemini for image-heavy slides |
| Lecture video/audio | MP4/WebM/MOV video or MP3/M4A/WAV audio of a recorded lecture | Gemini transcription with timestamps, plus a summary and key-topic list; video frames used for board/slide content |

The app keeps the original file and the extracted text or transcript. The student can view, rename, change the kind of, edit the extracted text of, or delete any upload. Lecture recordings show a timestamped transcript, and study materials built from a lecture link back to the moment they came from.

**Syllabus as a study map.** When a class has a syllabus, the app shows its topic and exam outline. The student can generate materials scoped to a syllabus topic or an upcoming exam (for example, “practice exam for Midterm 1”), using only the uploads that cover those topics.

**Per-class tutor.** A tutoring session started inside a class includes that class's notes as reference context. The tutor should:

- Prefer the student's notes for definitions, notation, and methods, so explanations match the course.
- Say which note an explanation comes from when it relies on one.
- Say when the notes don't cover something rather than presenting outside knowledge as if it came from the notes.
- Keep classes separate: a Math session doesn't use Biology notes.

**Generated study materials.** All materials are generated only from the selected class's notes (or a chosen subset):

| Type | Contents | Options |
| --- | --- | --- |
| Quiz | Short set of multiple-choice, true/false, and short-answer questions with an answer key and explanations | Number of questions, topics, difficulty |
| Study guide | Organized summary of key concepts, definitions, formulas, and examples | Topics, length |
| Practice exam | Longer, timed mix of question types (including worked/free-response problems) with a scoring rubric | Length, time limit, topics, difficulty |
| Flashcards | Term/definition or question/answer cards | Number of cards, topics |

Each question or section links back to the upload it was drawn from (page or slide number, or lecture timestamp). Materials are saved to the class so the student can retake or regenerate them. Generated content can be wrong, so the UI labels it as AI-generated and lets the student flag an incorrect question.

## System design

```mermaid
flowchart LR
    U[Student]
    C[Browser UI\nCapture + audio + preview overlay]
    T[Backend\nToken service + notes API\nLong-lived API key]
    D[(Database + file storage\nClasses, notes, materials)]
    G[Gemini Live API\ngemini-3.8-live]
    M[Gemini API\nText extraction + generation]

    U -->|selects target, asks question| C
    U -->|uploads notes, requests quiz/guide/exam| C
    C -->|authenticated token request| T
    T -->|short-lived Live token| C
    C -->|notes, generation requests| T
    T <-->|store / load| D
    T <-->|extract text, generate materials| M
    C -->|sampled screen frames + optional audio/text + class notes context| G
    G -->|audio, transcript, annotation tool call| C
    C -->|local speech playback + validated overlay| U
```

### Components

**Browser client**

- Calls `navigator.mediaDevices.getDisplayMedia()` directly from the student's Share button handler. The browser presents its own source picker and permission UI; the app must not try to choose or silently retain a source on the student's behalf.
- Displays the returned `MediaStream` in a video preview. Draws annotations in a positioned canvas/SVG layer aligned to that preview, so the geometry maps to the captured frame even when the preview is resized.
- Samples and scales frames before sending them. The Live API documents video frames as individual images at up to 1 frame per second; the client should send only as often as needed for the current tutoring exchange.
- Captures microphone audio only while enabled and plays the tutor's audio response.
- Renders transcripts, response cards, and annotations in the page. Annotation rendering is local and never modifies the captured app or desktop.
- Converts annotation coordinates from the referenced capture frame into preview coordinates, accounting for scaling, letterboxing, cropping, and source changes.

**Token service**

- Holds the long-lived Gemini API key.
- Authenticates the client and provisions a short-lived, restricted Live API token for the selected model/configuration.
- Does not receive or proxy screen frames or microphone audio in the proposed client-to-Gemini design.
- For a public deployment, apply rate limits and abuse controls to token issuance.

**Notes and study backend**

- Requires student sign-in so notes are tied to one account and never visible to other students.
- Stores uploaded files in private object storage and stores classes, extracted note text, and generated materials in a database.
- Extracts text from uploads: direct parsing for text, DOCX, PPTX, and text-based PDFs; Gemini multimodal input for images and scanned PDFs.
- Transcribes video/audio lectures with Gemini in a background job (upload to the Gemini Files API, request a timestamped transcript and summary), since long recordings can take minutes to process.
- Parses a syllabus into a structured outline (topics, weeks, exam dates) used to scope generation.
- Generates study materials by calling a standard (non-Live) Gemini model with the selected notes and a structured-output schema, then validates the result before saving it.
- When a Live session starts inside a class, supplies that class's note text (or a summary if the notes are too large) for the session's system context.
- Enforces upload limits (file size, files per class) and rate limits on generation.

**Data model (sketch)**

```text
Class          { id, owner_id, name, created_at }
Note           { id, class_id, title, kind: notes|document|syllabus|slides|lecture_video|lecture_audio,
                 file_url?, mime_type, duration_s?, extracted_text, transcript_segments[]? ({start_s, end_s, text}),
                 status: uploading|processing|ready|failed, created_at }
SyllabusOutline{ class_id, note_id, topics[] ({title, week?}), exams[] ({name, date?, topics[]}) }
StudyMaterial  { id, class_id, type: quiz|study_guide|practice_exam|flashcards, source_note_ids[], scope (topics/exam), options, content (JSON), created_at }
Attempt        { id, material_id, answers, score, completed_at }
```

**Generated material format (quiz example)**

```json
{
  "type": "quiz",
  "title": "Derivatives: Chain Rule",
  "questions": [
    {
      "id": "q1",
      "kind": "multiple_choice",
      "prompt": "What is d/dx of sin(3x)?",
      "choices": ["cos(3x)", "3cos(3x)", "-3cos(3x)", "3sin(3x)"],
      "answer": 1,
      "explanation": "Chain rule: the derivative of the outer function, cos(3x), times the derivative of the inner function, 3.",
      "sources": [{ "note_id": "note-42", "page": 3 }, { "note_id": "lecture-7", "timestamp_s": 1265 }]
    }
  ]
}
```

The backend rejects output that doesn't match the schema, is missing answers, or cites notes outside the selected class.

**Gemini Live session**

- Uses `gemini-3.8-live` for low-latency multimodal conversation.
- Receives screen frames as image/video input, along with the student's audio or typed question.
- Returns audio for spoken tutoring. Enable output transcription when the app needs text captions or response cards; the Live model's primary response modality is audio.
- May call the app's narrow `add_annotations` function to request visual marks. The app must execute and validate function calls itself and return a tool response to the Live session.

### Gemini Live integration

The current model identifier is **`gemini-3.8-live`**. The Live API uses a persistent WebSocket session. Its documentation describes individual video frames sent as images at a maximum of 1 FPS, supports function calling, and provides output transcription for displaying generated speech as text.

For a browser client that connects directly to Gemini, the token service should issue an ephemeral token. Do not bundle a long-lived API key in client JavaScript. Configure the token to constrain the model and allowed session settings. Keep the system instructions and tool declaration fixed on the trusted side where supported.

Live sessions have finite connection and media-session lifetimes. The implementation should handle disconnects, use session resumption/context compression where appropriate, and present a clear reconnect state rather than silently starting a fresh conversation. Keep a small local conversation summary for recovery only if needed; do not persist captured frames by default.

Official references:

- [Gemini 3.8 Live model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live)
- [Live API getting started](https://ai.google.dev/gemini-api/docs/live-api/get-started-sdk)
- [Live API capabilities and limits](https://ai.google.dev/gemini-api/docs/live-api/capabilities)
- [Live API tool use](https://ai.google.dev/gemini-api/docs/live-api/tools)
- [Ephemeral tokens](https://ai.google.dev/gemini-api/docs/live-api/ephemeral-tokens)
- [Live API session management](https://ai.google.dev/gemini-api/docs/live-api/session-management)
- [MDN: Screen Capture API and `getDisplayMedia()`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)

### Annotation contract

Expose one visual tool to the model, for example:

```json
{
  "name": "add_annotations",
  "arguments": {
    "frame_id": "frame-123",
    "items": [
      {
        "kind": "underline",
        "region": { "x": 0.12, "y": 0.34, "width": 0.48, "height": 0.06 },
        "label": "Check the sign in this step"
      }
    ]
  }
}
```

Coordinates are normalized to the exact frame that was sent (`0..1`, origin at top-left). The client should reject malformed values, unknown or stale frame IDs, excessive annotation counts, and regions outside the capture bounds. Annotations should be short-lived, non-interactive, and removable. If geometry is missing or invalid, show the verbal explanation without drawing.

The tool should have no capability beyond adding or clearing this overlay. In particular, do not expose general desktop-control tools.

## Privacy and data handling

- Screen sharing starts only after a click and the browser's source-selection/permission flow. The browser requires a fresh user action for each capture request; use HTTPS outside localhost. Microphone permission is requested separately.
- The selected screen content and optional microphone stream are sent to Google's Gemini API for the live session. Tell the student this before they start sharing.
- Do not save screenshots, audio, or transcripts by default. Keep frame buffers in memory only and clear them when sharing stops or the session ends.
- Keep the Gemini API key on the token service. Use short-lived ephemeral tokens in the client.
- Pause immediately when the student pauses capture, the browser reports the track ended, permission is revoked, or the session ends. A change of source requires a new browser selection flow.
- Avoid sending unchanged frames repeatedly. Provide a clear screen pause control for sensitive content.
- Uploaded notes and generated study materials **are** stored, because the student explicitly saves them. They are private to the student's account, and the student can delete any note, material, or whole class. Deleting a note removes the file and its extracted text.
- Uploaded content, including lecture recordings, is sent to the Gemini API for text extraction, transcription, generation, and per-class tutoring. Tell the student this at upload time, and remind them to upload only recordings they are allowed to keep (some courses restrict lecture recordings).

## MVP acceptance criteria

- A student can explicitly choose a screen/window/tab through the browser, see its live preview, and start and stop sharing.
- A student can ask one spoken or typed question about visible content and receive a relevant spoken answer.
- The app can show the answer as readable text when transcription is enabled.
- The tutor can return at least one frame-grounded annotation, and the overlay places it in the correct region of the selected target's preview.
- The student can dismiss marks and pause screen and microphone input independently.
- The app handles permission denial, network loss, invalid annotation data, and Live session expiry without crashing or continuing to send media.
- The shipped client contains no long-lived Gemini API key, and the default flow stores no captured media.
- A student can create at least two classes (for example, Math and Science), upload notes into each, and see them kept separate.
- A student can generate a quiz and a study guide from a class's notes, take the quiz in the app, and see a score with explanations.
- A tutoring session started in a class uses that class's notes and not another class's.
- A student can delete a note or class, and it no longer appears or affects generation.

## Risks and mitigations

| Risk                                                        | Mitigation                                                                                                                                                                        |
| ----------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Model points to the wrong line or object                    | Anchor marks to a specific sent frame, keep marks temporary, and let the student dismiss them. When uncertain, answer in words instead.                                           |
| Screen sharing exposes unrelated private content            | Let the student choose a window/display, show a persistent indicator, and provide immediate pause/end controls.                                                                   |
| Live API latency or disconnect interrupts tutoring          | Use concise turns, expose connection state, and implement session resumption/recovery.                                                                                            |
| Exposed client API key                                      | Use a backend-issued ephemeral token constrained to the Live model and session configuration.                                                                                     |
| Frequent frames increase cost and context usage             | Respect the documented frame-rate limit, sample only when needed, and avoid resending unchanged content.                                                                          |
| Screen-capture support and behavior vary by browser         | Prototype on the target desktop browser early, detect unavailable capture APIs, and offer a clear error with transcript-only fallback.                                            |
| Generated questions or answers are wrong                    | Generate only from the student's notes, cite the source note, validate the schema, label content as AI-generated, and let the student flag errors.                                |
| A class has too many notes to fit in context                | Summarize or chunk notes per class, and let the student pick specific notes or topics for each generation.                                                                        |
| Poor text extraction from handwritten or low-quality photos | Show the extracted text for review and let the student edit it or re-upload.                                                                                                      |
| Long lecture videos are slow or costly to process           | Process in a background job with visible progress, cap video length and size, transcribe once and reuse the transcript, and let the student upload audio-only instead.            |
| Stored notes leak between students                          | Require sign-in, scope every query by owner, and keep files in private storage behind signed URLs.                                                                                |
| A student expects marks to appear over the external app     | Keep the preview and annotations visible together, and state that marks are in the web UI. A system-wide overlay would require a separate native client and is outside MVP scope. |

## Open implementation decisions

- First supported browser(s) and screen-capture compatibility target.
- Whether the MVP uses voice by default or starts with typed questions plus optional voice.
- Annotation appearance and expiration time.
- Whether students can opt into storing session summaries later.
- Supported upload formats and size limits for the MVP, including maximum lecture video length and whether to keep the original video or only its transcript after processing.
- Whether notes reach the tutor as full text, summaries, or chunks retrieved per question (RAG).
- Which standard Gemini model to use for text extraction and study-material generation.
- Database and file-storage choice (for example, Firebase, Supabase, or Postgres plus S3-compatible storage).
- Authentication approach for a public demo versus a local hackathon build.
