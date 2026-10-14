# Reasoning Mode handoff

## Checkpoint

The implementation and follow-up enhancements are committed on `main` through `0bec271` (`Decouple feedback generation, add optimistic locking, and track exact latency`). The original implementation specification at `docs/reasoning-first-quiz-implementation.md` was kept out of those commits. This handoff is a separate, uncommitted file.

## What is implemented

Reasoning Mode is an opt-in mode for saved, server-owned quizzes and practice tests. Standard remains the default and retains its current submit-and-grade flow. A mode selection is stored on each `quiz_runs` row, and Reasoning Mode uses run-scoped question attempts, durable reasoning turns, immutable accepted/submitted snapshots, backend sufficiency checks, skip state, authoritative grading, and separately retryable reasoning feedback.

The evaluator gets the question context and submitted reasoning, but not the answer key. Answer controls remain editable before and after acceptance. Final submission is unavailable until the backend accepts a meaningful attempt; acceptance does not submit the answer. Live voice reuses the existing same-origin Gemini Live session path and only finalized student transcripts enter reasoning history. Partial transcripts and tutor speech are excluded.

The primary implementation is in `backend/app/api/quiz_attempts.py`, `backend/app/services/quiz_reasoning.py`, `backend/app/services/sqlite_schema.sql`, and `frontend/src/quiz-player.js`. API and persistence contracts are described in `docs/backend-design.md` and `docs/frontend-design.md`.

## Run and test

From the repository root:

```bash
cd frontend && npm test && npm run build
```

Run the backend suite with the repository's configured Python environment:

```bash
PYTHONPATH=backend python -m unittest discover -s backend/tests
```

For a manual browser run, start the backend and Vite as described in [README.md](../README.md), sign in, open a class, and create or open a saved quiz. Choose **Reasoning Mode** in the quiz start picker. Useful cases are:

1. Select an answer, submit a concise explanation, then submit the answer after acceptance.
2. Submit a substantive but incorrect explanation and verify acceptance is independent of answer correctness.
3. Submit a vague explanation, answer the targeted follow-up, and verify acceptance.
4. Choose **Skip question**, cancel once to confirm drafts remain, then confirm a skip.
5. Refresh after acceptance and verify the backend-restored state still allows answer submission.
6. Add another reasoning turn after acceptance, submit, and verify feedback includes the full submitted history.

Automated provider tests use mocked responses. The server-side `GEMINI_API_KEY` is configured in the ignored repository-root `.env`; do not print it or move it into frontend configuration. A one-request evaluator smoke test against the configured Gemini text model accepted the specification's meaningful-but-mistaken reasoning fixture. This verifies provider connectivity and structured output, not broad evaluator quality or real-provider voice behavior.

## Resolved enhancements

The following enhancements have been implemented on top of the initial checkpoint:
- **Decoupled feedback execution:** Final answer submission persists the answer and grade immediately, returning `feedback_status: "pending"` right away while generating reasoning feedback via FastAPI `BackgroundTasks`. The client polls or inspects the run, and an optional `wait_for_feedback: true` flag allows synchronous awaiting when needed.
- **Version-based optimistic concurrency checks:** Reasoning Mode mutations (`submit_reasoning`, `retry_reasoning`, `skip_question`, `submit_final_answer`, `retry_feedback`) require `expected_version`. Stale new mutations are rejected with 409 `version_conflict`. Replaying a committed operation with the same operation ID and content returns its existing result before version validation, so a lost response can be recovered safely. The frontend API client rejects missing or invalid versions before sending.
- **Exact median and P95 latency tracking:** Latency samples, including zero-millisecond samples, are recorded in `quiz_reasoning_latency_samples`. Daily median averages the two middle values for even sample counts; P95 uses nearest rank. Startup migration adds the aggregate columns to existing databases. Metrics are exposed via `GET /api/quiz-reasoning/metrics`.

## Remaining limits to keep in mind while testing

- The mode picker is currently available only for remote/saved quizzes. Browser-local and sample quizzes retain Standard mode because their question and answer data are not owned by the backend.
- Reasoning Mode runs for timed practice tests are currently untimed. Standard practice tests retain their existing timer.
- The production build succeeds, but Vite reports that `audio.js` is both dynamically and statically imported; the current dynamic import does not create a separate bundle chunk.
- The local demo/sample quiz flow will not exercise this feature. Use a generated saved quiz with an authenticated account.

## Verification recorded

- Backend: `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests` — 130 tests, 129 passed, 1 skipped.
- Frontend: `cd frontend && npm test` — 44 tests passed.
- Build: `cd frontend && npm run build` — passed; Vite still reports that `audio.js` is both dynamically and statically imported, so it remains in the main bundle.
- Lint and whitespace: targeted Ruff checks for the changed Python files and `git diff --check` passed.
- Real provider: one schema-valid Gemini reasoning evaluation returned `sufficient` for a generic substantive-but-incorrect OS/kernel fixture. No personal or student reasoning was used; the key stayed in the server-side environment and was not printed.
- Regression coverage now includes existing-database migration, mandatory version validation, stale-version idempotent replay for answer/reasoning/retries, and exact median/P95 with deterministic samples.
- No raw reasoning or media is logged by the new workflow. Daily aggregates and exact latency measurements are stored in `quiz_reasoning_metrics`; inspect via `GET /api/quiz-reasoning/metrics` or the database query in `docs/backend-design.md`.
