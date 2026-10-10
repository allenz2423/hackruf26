# Screen Tutor backend

The FastAPI service protects the long-lived Gemini API key and issues a
single-use, short-lived Gemini Live token to the browser. Screen and microphone
media, model audio, transcripts, and annotations go directly between the
browser and Gemini; this service has no media or conversation endpoints and
stores no user content.

## Local setup

1. Create a virtual environment and install the backend package:

   ```bash
   cd backend
   python -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```

2. Copy the root `.env.example` to `.env`, then set `GEMINI_API_KEY`.
3. Start the API from the repository root:

   ```bash
   cd ..
   uvicorn app.main:app --app-dir backend --reload
   ```

The API listens on port 8000. A Vite development server can proxy `/api` to
`http://localhost:8000`. If `frontend/dist` exists, FastAPI serves it from the
same origin. The API is available at `POST /api/live-token`; health is at
`GET /healthz`.

## Production configuration

Set `APP_ENV=production`, `GEMINI_API_KEY`, and an exact comma-separated list
of HTTPS `APP_ORIGINS`. Until the product has a real sign-in integration, the
included production gate requires a high-entropy `DEMO_GATE_TOKEN`; the
frontend must send it as an `Authorization: Bearer ...` header after the
student passes the private demo gate. Do not put this value in a browser bundle.
For a public product, replace this demo gate with a trusted authenticated app
session before exposing the endpoint.

The token rate limiter is in-memory and applies per process and client IP. It
is appropriate for local and single-instance demos. A multi-worker or
multi-instance deployment needs a shared rate-limit store or an upstream API
gateway limit. If running behind a proxy, configure Uvicorn to trust forwarded
headers only from that proxy; the app does not trust arbitrary
`X-Forwarded-For` values.

## API contract

`POST /api/live-token` accepts `{}` (or an empty body) and returns exactly:

```json
{
  "access_token": "short-lived-token",
  "model": "gemini-3.8-live",
  "expires_at": "2026-10-10T18:30:00Z"
}
```

The request cannot select a model, prompt, or tool. Failures return a stable
`code`, user-safe `message`, and `request_id`; raw upstream errors and
credentials are never returned. The token response is marked `no-store`.

## Supabase database

The class-library schema is captured in the root `supabase/migrations/`
directory. The current FastAPI service does not connect to the database or
implement class, note, quiz, or test endpoints yet. When those services are
added, keep the Supabase Postgres connection string server-side and apply
schema changes through versioned Supabase CLI migrations. The initial
migration enables row-level security without browser-facing policies, so
ordinary `anon` and `authenticated` Supabase Data API clients cannot read or
write these tables until user authentication and per-user policies are
deliberately added. A privileged server database connection can bypass RLS,
so future API queries must still enforce class ownership.
