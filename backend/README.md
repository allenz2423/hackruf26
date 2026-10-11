# Screen Tutor backend

FastAPI owns the Gemini API key, model configuration, Live sessions, and model tools, and serves the SQLite-backed class library API. The browser connects to a same-origin backend WebSocket. Screen frames and enabled microphone audio pass through the backend in memory and are forwarded to the provider; live media and conversation data are not logged or stored.

## Local setup

1. Copy the root `.env.example` to `.env` and configure `GEMINI_API_KEY` and a random `FILE_URL_SIGNING_SECRET` with at least 32 characters. Set `GEMINI_TEXT_MODEL=gemini-3.8-flash` to override its default.
2. Keep the default `LOCAL_OWNER_ID` for a single local workspace, or replace it with another UUID if you need a separate SQLite owner namespace.
3. Install Lean through `elan` if it is not already installed, then prepare the pinned Lean/Mathlib project. The proof checker uses the local `lake` executable; no container runtime is required.

   ```bash
   curl -fsSL https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --default-toolchain none
   export PATH="$HOME/.elan/bin:$PATH"
   cd backend/lean
   lake exe cache get
   cd ../..
   ```

4. Set `SQLITE_PATH` if you want the database somewhere other than `backend/.local/screen_tutor.sqlite3`. The backend creates its parent directory and initializes the schema at startup.
5. Install and run the backend:

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   cd ..
   uvicorn app.main:app --app-dir backend --reload
   ```

The API listens on port 8000. A Vite development server can proxy `/api` to it. If `frontend/dist` exists, FastAPI serves it from the same origin. Original course files are stored privately under `backend/.local/private_uploads` by default; use persistent private storage for both that directory and the SQLite database file when deploying this single-instance implementation. Back up both together. SQLite is configured for one backend process; move to a shared database before running multiple workers or instances.

## Regression checks

From the repository root, run the isolated calculator/Lean argument, quiz-scoring, and processing-lifecycle tests:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v
```

These tests use in-memory SQLite and a fake Lean runner; they do not call Gemini or change the local course library. Live and native Lean integration checks require the running service and configured runtime.

## Authentication and ownership

Accounts live in SQLite (`users`, `user_sessions`). Passwords are stored as salted scrypt hashes. A login sets an HttpOnly, SameSite=Lax `jot_session` cookie; only a hash of the session token is stored. Every class-library row belongs to the logged-in account, and the backend never accepts an owner from the client.

- **Guests** (no session) may use quick tutoring (`POST /api/live-sessions` without `class_id`), the calculator, and the Lean checker. They cannot read or write class-library data, and cannot start a class tutor session.
- **Logged-in accounts** get their own class library. Another account's classes return 404, and class-library endpoints return 401 without a session.
- `POST /api/auth/signup`, `POST /api/auth/login`, and `POST /api/auth/logout` need an allowed `Origin` and are rate limited (`AUTH_RATE_LIMIT` per `AUTH_RATE_WINDOW_SECONDS`). `GET /api/auth/me` returns `{"user": null}` for a guest. A logged-in account can change its details with `PATCH /api/auth/me` and its password with `POST /api/auth/password`.

Create a local test account (refused when `APP_ENV=production`):

```bash
cd backend
python -m app.scripts.seed_user USERNAME PASSWORD [--display-name NAME] [--demo] [--adopt-local-data]
```

`--demo` marks the account as the demo account (`is_demo`). Only that account starts with the built-in sample classes and study sets; every other account starts empty. Re-running the command on an existing account updates its password and demo flag.

`--adopt-local-data` gives that account any classes saved under the old `LOCAL_OWNER_ID` before accounts existed.

In production, also configure a strong `DEMO_GATE_TOKEN` to protect session-ticket and tool endpoints, and serve over HTTPS so the session cookie is sent with the `Secure` flag.

## API

- `GET /healthz`
- `POST /api/live-sessions` with `{}` or optional `class_id` (UUID) and `mode` (`conversation` or `check_steps`); returns a one-use ticket, not a provider credential
- `WS /api/live` using the ticket as a WebSocket subprotocol
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

Live screen changes supersede historical observations. The browser sends
`screen_paused` when sharing stops; the backend invalidates its latest snapshot
and rejects marks without a recent frame. Typed questions include the latest
bounded snapshot in memory. Reset tutor memory obtains a fresh ticket and opens
a new conversation without replaying old frames or history. See the
[Live grounding contract](../docs/backend-design.md#changing-screen-content-and-corrections).

The full request/response shapes, status codes, processing lifecycle, and data boundaries are in the [backend design](../docs/backend-design.md#http-api).

The backend executes the configured calculation and optional `verify_lean_proof` tools directly inside the Live session. Annotation requests are sent to the browser for frame/geometry validation and local rendering, then the result is returned to the session. The Lean checker accepts a formal proposition and tactic proof body and returns `verified`, `rejected`, or `timed_out` with bounded diagnostics. Lean checks only the formal statement supplied by the provider.

## Gemini models

`GEMINI_MODEL` defaults to `gemini-3.8-live` for low-latency tutor sessions. `GEMINI_TEXT_MODEL` defaults to `gemini-3.8-flash` for document extraction, lecture transcription, syllabus parsing, and structured study-material generation. They use separate API modes with the same server-only Gemini key; the Flash model does not provide the Live session.

## Limits and deployment notes

- `MAX_UPLOAD_MB` defaults to 250 MiB, matching the current browser-side validation. The server remains authoritative.
- `MAX_UPLOADS_PER_CLASS` defaults to 100 uploads; `MAX_LECTURE_MINUTES` defaults to 120.
- Long media is uploaded to Gemini's temporary Files API for processing and deleted after the job when possible.
- Upload and generation jobs use FastAPI background tasks for the hackathon MVP. Startup marks interrupted work as failed so uploads can be retried and study generation can be requested again. Ready records are preserved. Run one backend process; use a durable queue before multi-instance production.
- The local private storage directory is not cloud storage and is ignored by Git. Use a persistent disk or move `LocalPrivateStorage` to a private object store before deploying to ephemeral hosts.
- Rate limits are in-memory, per user and process. Session tickets expire after 45 seconds and can be consumed once. Use a shared store or gateway when deploying multiple workers/instances.
- The Lean runner uses the pinned Lean 4/Mathlib project with the fixed `Mathlib.Tactic` prelude, which supplies common tactics without loading every Mathlib module. It caps input/output, limits concurrency, and applies a 30-second default timeout plus CPU/file-size limits where `prlimit` is available. Proofs requiring definitions from other Mathlib modules may be rejected; the model tool declaration states this limit. It is not a full OS sandbox; keep proof checks behind authentication and add process isolation before public multi-tenant deployment. Set `LEAN_TOOL_ENABLED=false` to omit the tool from Live sessions.
- Uploaded course materials and generated study items are stored because the student explicitly adds them. Live screen/audio media and tutor conversations pass through active session memory only and are never logged or stored.
