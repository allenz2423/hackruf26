# Frontend Agent Instructions

Read the repository [AGENTS.md](../AGENTS.md) and [frontend design](../docs/frontend-design.md) before changing the client.

## Scope

- Own the browser UI and client behavior under `frontend/`.
- Use HTML, CSS, and JavaScript modules with the agreed Vite setup and Google's `@google/genai` SDK.
- Do not change Python backend implementation, backend configuration, or `.env` files. For backend changes, update the shared contract request or coordinate with the backend role.

## Required behavior

- Start `getDisplayMedia()` only from the student's explicit Share action and let the browser show its source picker.
- Draw annotations over the shared-screen preview inside this page. Never claim to draw over another desktop app.
- Connect directly to Gemini Live with the ephemeral `access_token` returned from `POST /api/live-token`.
- Keep `GEMINI_API_KEY` out of all frontend files, bundles, source maps, logs, and browser storage.
- Send only sampled screen frames and enabled microphone input to Gemini. Never send media to the Python backend.
- Validate `add_annotations` calls against the referenced frame, coordinate bounds, allowed shapes, and reasonable label/count limits before rendering.
- Stop capture and audio immediately when the student ends or pauses the corresponding input. Clear in-memory media and annotations at session end.
- Keep sharing, microphone, connection, and error states visible and accessible.

## Shared API contract

`POST /api/live-token` accepts `{}` and returns:

```json
{
  "access_token": "short-lived-token",
  "model": "gemini-3.8-live",
  "expires_at": "ISO-8601 timestamp"
}
```

The server owns model selection and tool configuration. Do not add caller-controlled model or tool parameters in the client.
