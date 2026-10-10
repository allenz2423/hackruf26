# Screen Tutor

**An AI tutor that runs in the browser, sees the screen you choose to share, talks through your work, and places temporary visual notes over the preview in the page.**

Screen Tutor is a HackRU Fall 2026 project concept. In the web UI, the student can share a display, application window, or browser tab, ask a question by voice or text, and get an explanation grounded in the visible work. When a visual reference helps, the tutor can point to a region of the shared-screen preview with a highlight, underline, or short note. Marks are rendered by the page as a removable overlay; the model does not click, type, or change the student's work.

## Design

See [the design document](docs/design.md) for the product flow, architecture, data handling, annotation format, MVP scope, and open questions.

## Status

Design draft. No application code or setup instructions exist yet.

## Proposed stack

- A browser client for screen sharing, microphone/audio, a live preview, and an annotation overlay in the page.
- Gemini Live API using `gemini-3.8-live` for real-time multimodal tutoring.
- A small token service that issues short-lived Gemini Live credentials; the long-lived API key stays server-side.

The MVP annotates the shared-screen preview inside the web page. It does not draw over other applications outside the browser. The current Gemini model and API behavior are linked in the [design doc](docs/design.md#gemini-live-integration).
