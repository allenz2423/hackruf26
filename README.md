# Screen Tutor

**An AI tutor that runs in the browser, sees the screen you choose to share, talks through your work, and places temporary visual notes over the preview in the page.**

Screen Tutor is a HackRU Fall 2026 project concept. In the web UI, the student can share a display, application window, or browser tab, ask a question by voice or text, and get an explanation grounded in the visible work. When a visual reference helps, the tutor can point to a region of the shared-screen preview with a highlight, underline, or short note. Marks are rendered by the page as a removable overlay; the model does not click, type, or change the student's work.

Students can also upload their own course materials (notes, documents, syllabi, lecture slides, and video/audio lectures) and organize them by class (Math, Science, and so on). Each class gets its own tutor that uses that class's materials, and the app can generate quizzes, study guides, practice tests, and flashcards from them.

For automatic spoken corrections, enable **Check my steps** before **Share screen**. The tutor checks changed screen content without needing a question or microphone input, and speaks a short hint when it identifies a clear mistake. Leave a completed step visible briefly so it can review it. It skips unchanged views and leaves time between corrections. Optionally turn on the microphone to explain your steps; speaking interrupts the tutor, and checks wait for a pause. End the session to change modes. Feedback can miss errors.

If the tutor keeps referring to erased or outdated work, use **Reset memory** in the Conversation panel. It clears the conversation and marks and opens a fresh tutor connection while keeping your screen shared. Turn the microphone on again afterward.

## Design

See the [product overview](docs/design.md), [frontend design](docs/frontend-design.md), and [backend design](docs/backend-design.md).

See the [proactive coaching and barge-in design](docs/proactive-coaching-design.md) for the implemented backend, silent-typing behavior, spoken interruptions, test results, and frontend handoff.

## Status

The FastAPI backend uses SQLite for class-library data and private local storage
for uploaded files. Local/demo ownership is scoped by a configured UUID, so no
external auth provider is required. The default SQLite database and uploaded
files live under `backend/.local/` and need persistent storage for deployed
environments.

## Proposed stack

- HTML, CSS, and JavaScript for the browser UI, with a provider-neutral WebSocket connection to the backend.
- Python, FastAPI, and Pydantic for server-owned Live sessions and content processing.
- Gemini Live API using `gemini-3.8-live` for real-time multimodal tutoring.
- SQLite for backend-owned class-library data.
- The backend owns all provider SDK use, model configuration, instructions, and tools. The browser receives only a short-lived, one-use WebSocket ticket; the long-lived API key never leaves the server.

See [backend setup and configuration](backend/README.md) to run the session
gateway locally.

The MVP annotates the shared-screen preview inside the web page. It does not draw over other applications outside the browser. The current Gemini model and API behavior are linked in the [design doc](docs/design.md#gemini-live-integration).
