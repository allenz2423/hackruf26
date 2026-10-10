# Screen Tutor backend

FastAPI owns the Gemini API key, provisions one-use Gemini Live tokens, verifies Supabase Auth access tokens, and serves the class library API. The browser connects directly to Gemini Live; screen frames, microphone audio, tutor audio, live transcripts, and annotation events do not pass through this service.

## Local setup

1. Start or select a Supabase project. Apply the tracked database migrations with the Supabase CLI (`supabase start` for the local stack or `supabase db push` for a linked project).
2. Copy the root `.env.example` to `.env` and configure `GEMINI_API_KEY`, `SUPABASE_URL`, `DATABASE_URL`, and a random `FILE_URL_SIGNING_SECRET` with at least 32 characters. Set `SUPABASE_JWT_SECRET` only for projects still signing JWTs with HS256. Set `GEMINI_TEXT_MODEL=gemini-3.8-flash` to override its default.
3. Install and run the backend:

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   cd ..
   uvicorn app.main:app --app-dir backend --reload
   ```

The API listens on port 8000. A Vite development server can proxy `/api` to it. If `frontend/dist` exists, FastAPI serves it from the same origin. Original course files are stored privately under `backend/.local/private_uploads` by default; use a persistent private volume when deploying this single-instance implementation.

## Authentication and ownership

When `SUPABASE_URL` is configured, protected APIs require `Authorization: Bearer <Supabase access token>`. The server validates the JWT signature and claims using Supabase JWKS, or `SUPABASE_JWT_SECRET` for legacy HS256 projects, and scopes every database query to the verified `sub` UUID. Sign-in, refresh, and sign-out remain Supabase Auth operations; the backend does not copy refresh tokens or create its own user-session table.

Without `SUPABASE_URL`, configure `DEMO_GATE_TOKEN` to use the token and calculator prototypes. If neither authentication option is configured, protected routes return `503`. Class-library routes return `503` without Supabase Auth because per-user ownership cannot be established. Production must configure Supabase Auth or a strong private demo gate for the token service.

## API

- `GET /healthz`
- `POST /api/live-token` with `{}` or optional `{"class_id":"<uuid>"}`
- `POST /api/tools/calculate`
- `GET|POST /api/classes`
- `PATCH|DELETE /api/classes/{class_id}`
- `POST|GET /api/classes/{class_id}/uploads`
- `GET|PATCH|DELETE /api/uploads/{upload_id}`
- `POST /api/uploads/{upload_id}/retry`
- `GET|PUT /api/classes/{class_id}/syllabus`
- `POST|GET /api/classes/{class_id}/study-materials`
- `GET|DELETE /api/study-materials/{material_id}`
- `POST /api/study-materials/{material_id}/attempts`
- `POST /api/study-materials/{material_id}/flags`

The full request/response shapes, status codes, processing lifecycle, and data boundaries are in the [backend design](../docs/backend-design.md#http-api).

## Gemini models

`GEMINI_MODEL` defaults to `gemini-3.8-live` for low-latency tutor sessions. `GEMINI_TEXT_MODEL` defaults to `gemini-3.8-flash` for document extraction, lecture transcription, syllabus parsing, and structured study-material generation. These are separate APIs and credentials: the Flash model does not provide the Live session.

## Limits and deployment notes

- `MAX_UPLOAD_MB` defaults to 250 MiB, matching the current browser-side validation. The server remains authoritative.
- `MAX_UPLOADS_PER_CLASS` defaults to 100 uploads; `MAX_LECTURE_MINUTES` defaults to 120.
- Long media is uploaded to Gemini's temporary Files API for processing and deleted after the job when possible.
- Upload and generation jobs use FastAPI background tasks for the hackathon MVP. They run in-process; a restart can interrupt active work. Use a durable queue before multi-instance production.
- The local private storage directory is not cloud storage and is ignored by Git. Use a persistent disk or move `LocalPrivateStorage` to a private object store before deploying to ephemeral hosts.
- Rate limits are in-memory, per user and process. Use a shared store or gateway when deploying multiple workers/instances.
- Uploaded course materials and generated study items are stored because the student explicitly adds them. Live screen/audio media and tutor conversations are not stored.
