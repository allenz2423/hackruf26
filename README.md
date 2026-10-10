# Screen Tutor

**An AI tutor that runs in the browser, sees the screen you choose to share, talks through your work, and places temporary visual notes over the preview in the page.**

Screen Tutor is a HackRU Fall 2026 project concept. In the web UI, the student can share a display, application window, or browser tab, ask a question by voice or text, and get an explanation grounded in the visible work. When a visual reference helps, the tutor can point to a region of the shared-screen preview with a highlight, underline, or short note. Marks are rendered by the page as a removable overlay; the model does not click, type, or change the student's work.

Students can also upload their own course materials (notes, documents, syllabi, lecture slides, and video/audio lectures) and organize them by class (Math, Science, and so on). Each class gets its own tutor that uses that class's materials, and the app can generate quizzes, study guides, practice tests, and flashcards from them.

## Design

See the [product overview](docs/design.md), [frontend design](docs/frontend-design.md), and [backend design](docs/backend-design.md).

## Status

The FastAPI backend uses SQLite for class-library data and private local storage
for uploaded files. Supabase Auth remains the identity provider for browser
sign-in; the backend verifies its access tokens and stores the verified user ID
with each class. The default SQLite database and uploaded files live under
`backend/.local/` and need persistent storage for deployed environments.

## Proposed stack

- HTML, CSS, and JavaScript for the browser UI, with Google's JavaScript SDK for Gemini Live.
- Python, FastAPI, and Pydantic for the token service.
- Gemini Live API using `gemini-3.8-live` for real-time multimodal tutoring.
- Supabase Auth for sign-in and SQLite for backend-owned class-library data.
- A small backend that issues constrained, short-lived Gemini Live credentials; the long-lived API key stays server-side. SQLite-backed class-library APIs store course notes and generated study materials.

See [backend setup and configuration](backend/README.md) to run the token
service locally.

The MVP annotates the shared-screen preview inside the web page. It does not draw over other applications outside the browser. The current Gemini model and API behavior are linked in the [design doc](docs/design.md#gemini-live-integration).
