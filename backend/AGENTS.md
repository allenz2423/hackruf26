# Backend Agent Instructions

Read the repository [AGENTS.md](../AGENTS.md) and [backend design](../docs/backend-design.md) before changing the service.

## Scope

- Own Python service code and backend-specific configuration under `backend/`.
- Use Python, FastAPI, Pydantic v2, `pydantic-settings`, Google's `google-genai` Python SDK, and Uvicorn as described in the backend design.
- Coordinate shared WebSocket/API changes with the frontend role; do not change frontend implementation in a backend-only task.

## Required behavior

- Keep `GEMINI_API_KEY` on the server. Load it from environment configuration; never return, log, or persist it.
- The backend owns provider SDK usage, model selection, instructions, session configuration, and tools. The browser receives only a short-lived, one-use WebSocket ticket from `POST /api/live-sessions`, then connects to `WS /api/live`.
- The approved Live tools are `google_search`, `calculate_expression`, `verify_lean_proof`, and `add_annotations`. Execute calculations and proof checks on the backend. Send annotation requests to the browser for local frame/geometry validation and rendering.
- Relay screen frames and enabled microphone audio only while the session is active. Apply strict message size and frame-rate limits. Never log, record, or persist live media, transcripts, or conversations.
- Add origin checks, rate limits, request-size limits, one-use ticket validation, and sanitized error responses. Quick app APIs require an authenticated session unless the operator explicitly enables `ALLOW_GUEST_ACCESS`; account-owned APIs always require a session.
- The calculator accepts only bounded numeric or symbolic expressions through its allow-listed parser. Never use SymPy's string parser on user input; never log or persist calculator expressions or results.
- Lean proof checking accepts only a bounded formal proposition and tactic proof, rejects metaprogramming and admission syntax, runs with a timeout and OS resource limits, and never logs or stores submitted proofs. Keep it behind the configured app access policy; isolate it before public multi-tenant deployment.
- Keep backend-specific settings and Python dependencies under backend ownership. `.env` is local and ignored; `.env.example` contains names and safe defaults only.

## Shared API contract

`POST /api/live-sessions` returns a short-lived ticket and expiry, never a provider credential or model metadata. The ticket is consumed once by `WS /api/live`. The server owns the provider session and translates between its protocol and the browser's small event protocol.
