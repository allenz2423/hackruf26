# Backend Agent Instructions

Read the repository [AGENTS.md](../AGENTS.md) and [backend design](../docs/backend-design.md) before changing the service.

## Scope

- Own Python service code and backend-specific configuration under `backend/`.
- Use Python, FastAPI, Pydantic v2, `pydantic-settings`, Google's `google-genai` Python SDK, and Uvicorn as described in the backend design.
- Do not change frontend implementation or UI behavior. If a client contract needs to change, update the shared design and coordinate with the frontend role first.

## Required behavior

- Keep `GEMINI_API_KEY` on the server. Load it from environment configuration; never return, log, or persist it.
- Implement `POST /api/live-token` as a narrow token-provisioning endpoint. Validate input and output with Pydantic. Do not accept a model name, system instruction, or arbitrary tools from the caller.
- Issue a short-lived ephemeral token constrained to the configured Live model and the approved session configuration/tool declarations where the Gemini API supports those constraints.
- Return only `access_token`, `model`, and `expires_at` to the browser. Do not log or persist the ephemeral token.
- Keep screen frames, microphone audio, tutor audio, transcripts, and annotation payloads out of backend requests, logs, and storage. The browser connects directly to Gemini Live.
- Add origin checks, rate limits, request-size limits, and sanitized error responses to the token endpoint. Public deployments require an authenticated app session or an explicit demo gate.
- Keep backend-specific settings and Python dependencies under backend ownership. `.env` is local and ignored; `.env.example` contains names and safe defaults only.

## Shared API contract

`POST /api/live-token` accepts `{}` and returns:

```json
{
  "access_token": "short-lived-token",
  "model": "gemini-3.8-live",
  "expires_at": "ISO-8601 timestamp"
}
```

The backend chooses the model and tool configuration. Keep this response and the Live session constraints aligned with the frontend design; communicate any contract change before implementation.
