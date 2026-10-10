# Screen Tutor backend

FastAPI owns the Gemini API key, provisions one-use Gemini Live tokens, verifies Supabase Auth access tokens, and serves the class library API. The browser connects directly to Gemini Live; screen frames, microphone audio, tutor audio, live transcripts, and annotation events do not pass through this service.

## Local setup

1. Start or select a Supabase project. Apply the tracked database migrations with the Supabase CLI (`supabase start` for the local stack or `supabase db push` for a linked project).
2. Copy the root `.env.example` to `.env` and configure `GEMINI_API_KEY`, `SUPABASE_URL`, `DATABASE_URL`, and a random `FILE_URL_SIGNING_SECRET` with at least 32 characters. Set `SUPABASE_JWT_SECRET` only for projects still signing JWTs with HS256. Set `GEMINI_TEXT_MODEL=gemini-3.8-flash` to override its default.
3. Install Lean through `elan` if it is not already installed, then prepare the pinned Lean/Mathlib project. The proof checker uses the local `lake` executable; no container runtime is required.

   ```bash
   curl -fsSL https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --default-toolchain none
   export PATH="$HOME/.elan/bin:$PATH"
   cd backend/lean
   lake exe cache get
   cd ../..
   ```

4. Install and run the backend:

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   cd ..
   uvicorn app.main:app --app-dir backend --reload
   ```

The API listens on port 8000. A Vite development server can proxy `/api` to it. If `frontend/dist` exists, FastAPI serves it from the same origin. Original course files are stored privately under `backend/.local/private_uploads` by default; use a persistent private volume when deploying this single-instance implementation.

## Authentication and ownership

When `SUPABASE_URL` is configured, protected APIs require `Authorization: Bearer <Supabase access token>`. The server validates the JWT signature and claims using Supabase JWKS, or `SUPABASE_JWT_SECRET` for legacy HS256 projects, and scopes every database query to the verified `sub` UUID. Sign-in, refresh, and sign-out remain Supabase Auth operations; the backend does not copy refresh tokens or create its own user-session table.

Without `SUPABASE_URL`, configure `DEMO_GATE_TOKEN` to use the token, calculator, and Lean proof-check tools. If neither authentication option is configured, protected routes return `503`. Class-library routes return `503` without Supabase Auth because per-user ownership cannot be established. Production must configure Supabase Auth or a strong private demo gate for the token service.

## API

- `GET /healthz`
- `POST /api/live-token` with `{}` or optional `{"class_id":"<uuid>"}`
- `POST /api/tools/calculate`
- `POST /api/tools/verify-lean`
- `GET|POST /api/classes`
- `PATCH|DELETE /api/classes/{class_id}`
- `POST|GET /api/classes/{class_id}/uploads`
- `GET|PATCH|DELETE /api/uploads/{upload_id}`
- `POST /api/uploads/{upload_id}/retry`
- `GET|PUT /api/classes/{class_id}/syllabus`
- `POST|GET /api/classes/{class_id}/study-materials`
- `POST /api/classes/{class_id}/assessments`
- `GET /api/classes/{class_id}/assessments`
- `GET /api/assessments/{assessment_id}`
- `GET|DELETE /api/study-materials/{material_id}`
- `POST /api/study-materials/{material_id}/attempts`
- `POST /api/study-materials/{material_id}/flags`

The full request/response shapes, status codes, processing lifecycle, and data boundaries are in the [backend design](../docs/backend-design.md#http-api).

Gemini Live advertises `verify_lean_proof` by default. The browser dispatches that blocking function call to `POST /api/tools/verify-lean` and returns the response to the same Live session using the original function-call ID. The endpoint accepts a formal proposition and a Lean tactic proof body, then returns `verified`, `rejected`, or `timed_out` with bounded diagnostics. Lean checks only the formal statement the model supplied.

## Gemini models

`GEMINI_MODEL` defaults to `gemini-3.8-live` for low-latency tutor sessions. `GEMINI_TEXT_MODEL` defaults to `gemini-3.8-flash` for document extraction, lecture transcription, syllabus parsing, and structured study-material generation. These are separate APIs and credentials: the Flash model does not provide the Live session.

## Limits and deployment notes

- `MAX_UPLOAD_MB` defaults to 250 MiB, matching the current browser-side validation. The server remains authoritative.
- `MAX_UPLOADS_PER_CLASS` defaults to 100 uploads; `MAX_LECTURE_MINUTES` defaults to 120.
- Long media is uploaded to Gemini's temporary Files API for processing and deleted after the job when possible.
- Upload and generation jobs use FastAPI background tasks for the hackathon MVP. They run in-process; a restart can interrupt active work. Use a durable queue before multi-instance production.
- The local private storage directory is not cloud storage and is ignored by Git. Use a persistent disk or move `LocalPrivateStorage` to a private object store before deploying to ephemeral hosts.
- Rate limits are in-memory, per user and process. Use a shared store or gateway when deploying multiple workers/instances.
- The Lean runner uses the pinned Lean 4/Mathlib project, caps input/output, limits concurrency, and applies a timeout plus CPU/file-size limits where `prlimit` is available. It is not a full OS sandbox; keep proof checks behind authentication and add process isolation before public multi-tenant deployment. Set `LEAN_TOOL_ENABLED=false` to omit the tool from newly issued Live tokens.
- Uploaded course materials and generated study items are stored because the student explicitly adds them. Live screen/audio media and tutor conversations are not stored.
