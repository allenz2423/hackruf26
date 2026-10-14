# Implementation Progress — Course Knowledge and Student Mastery

**Specification:** [`improved-impl.md`](improved-impl.md)
**Updated:** 2026-10-14
**Current phase:** Implementation, verification, and final production deployment complete.

## Current status

All seven implementation gates have working backend and frontend paths. The implementation and final source-revision trigger fix are deployed to the existing `root@alma` Docker service at `jotai.tech`. The app container is healthy on image `sha256:b17f91709f634c61d3a89b9d1ad46d5cab26dd8cadcc883179005f0e12f10c98`; the production database passed integrity checks and the public health endpoint responds successfully.

## Gate 0 audit checklist

- **Upload/extraction:** `class_library.py` validates uploads and uses existing local parsers for readable PDF, PPTX, DOCX, and pasted text; provider extraction handles scanned/low-text PDF, images, and lecture media. Original bytes stay in `LocalPrivateStorage` outside the static root. `source_index.py` records PDF page, slide, DOCX order/headings, or lecture timestamps; content and chunk revisions are immutable, logical IDs are stable across matches, and index writes are guarded by `processing_revision`.
- **Persistence/migration:** SQLite and `schema_migrations` in `database.py`/`sqlite_schema.sql` are authoritative. Existing `course_classes`, `course_notes`, `study_materials`, uploaded assessments, runs, attempts, reasoning, and hidden answer keys were retained and extended. Startup applies idempotent schema upgrades. Archived PostgreSQL files are not used.
- **Authorization:** `class_access.py` checks current identity, membership/capabilities, class ownership, personal-resource ownership, publication/schedule, and assessment answer-key release. Exact source fetch and file downloads recheck access. Personal generation from readable materials does not require class-level create permission.
- **Answer-key exposure audit:** material detail, run creation/resume, standard submit, Reasoning Mode, Ladder snapshots, review tutor, source search, graph citations, and recommendation/resource responses use safe allowlists or run-pinned snapshots. In-progress payloads omit keys, mappings, correctness, rubrics, and unreleased explanations. Legacy attempts with unknown owners remain private and never become mastery evidence.
- **Reuse/contracts:** source index reuses upload extraction/storage and FTS5; the graph uses existing Gemini structured output and class capabilities; bank generation and grading extend existing materials/runs; Ladder reuses `quiz_runs`, attempts, reasoning, and grading. New records are additive, owner-bound, or class-scoped; provider calls occur outside database transactions.

## Public contracts and decisions

- Source retrieval: `GET /api/classes/{class_id}/sources/search` and `/sources/chunks/{chunk_revision_id}` return bounded current search results or the authorized immutable cited chunk.
- Graph: class graph read is member-safe; manager review/extraction/merge/concept/edge mutations use the existing class manager capability. Proposed relationships remain separate from the validated graph; prerequisite-cycle checks and insertion share a transaction.
- Assessment: class bank generation requires shared `create`; personal generation uses the existing personal-resource path and only needs `read` on selected sources. New runs pin private grading snapshots and random option mappings while returning only opaque public question/choice IDs. Feedback release is enforced server-side.
- Learning: one `beta-evidence-v2` scoring service handles eligible quiz, exam, and Ladder outcomes. XP, objective accuracy, reasoning quality, and mastery remain separate. Progress, history, concept detail, preferences, and recommendations derive the owner from the session. No classmate or teacher progress view exists.
- Mastery Ladder: each selected concept has independent level/recovery/terminal state. `objective_only` uses deterministic correctness; `reasoning_required` combines the existing sufficiency-to-submit gate with a separate validated rubric. One distinct same-level recovery is prefetched where available; skip is not incorrectness.
- Background work: SQLite-backed source, graph, bank-replenishment, and calibration-refresh jobs use bounded claims/retries and restart recovery. Production workers run inside the single API process; SQLite is not configured for multiple app instances.
- Calibration: one refresh is keyed per 15-minute UTC window, retries up to three times with bounded backoff, and is inspectable through an administrator-only endpoint. Flags are advisory and never auto-change difficulty or graph edges.

## Milestones

| Gate | Status | Evidence |
| --- | --- | --- |
| 0. Audit and contract | Complete | This audit and updated product/backend/frontend design docs. |
| 1. Source index | Implemented; tests cover revisions, FTS permissions, exact fetch, stale-write rejection, retry, PDF/PPTX/DOCX/text structure, and access revocation. |
| 2. Concept graph | Implemented; tests cover source-backed proposals, normalization, versioned manager decisions, merge history, and transactional cycle prevention. |
| 3. Secure questions | Implemented; tests cover source validation, family de-duplication, personal/shared separation, safe DTOs, run snapshots, feedback release, grading, and privacy. |
| 4. Evidence and scoring | Implemented; tests cover idempotent XP/outcomes, replay, privacy, feedback release, weights, recency, and eight synthetic trajectories. |
| 5. Mastery Ladder | Implemented; integrated tests cover all four levels, successful and failed recovery, skip, insufficient then cumulative sufficient reasoning, failed rubric despite a correct answer, provider failure/retry, and answer/outcome idempotency. |
| 6. Learning views | Implemented; frontend and API tests cover private progress/detail/history, preferences, explainable recommendations, engagement, and owner isolation. |
| 7. Calibration | Implemented; tests cover exposure thresholds, aggregate-only flags, bounded revision-aware replenishment, and calibration job idempotency/recovery/retry. |

## Verification log

Final verification on the deployed source:

- `PYTHONWARNINGS=ignore PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -q`: **222 tests passed**.
- `cd frontend && npm test`: **93 tests passed**.
- `cd frontend && npm run build`: passed. Vite reports the existing mixed static/dynamic `audio.js` import, so that module is not split into a separate chunk.
- `backend/.venv/bin/ruff check --select I,F,B,UP backend/app backend/tests`: passed.
- `backend/.venv/bin/ruff check --select E501 backend/app backend/tests`: the repo-wide check reports **253** existing long-line findings; none intersect added backend lines. The `I,F,B,UP` check passes.
- `backend/.venv/bin/python -m compileall -q backend/app backend/tests` and `git diff --check`: passed.
- Focused source-to-learning E2E with controlled provider fakes: ten generated biology PPTX lecture decks uploaded through the public API, native slide locator verification, duplicate concept normalization, Simplified Chinese generation, private per-student progress, a full four-level Ladder, recovery success/failure, skip, flawed reasoning, rubric-provider failure/retry, and pinned-source revision checks all passed. These are deterministic integration checks; they do not verify live Gemini output quality.
- Production deployment: `docker compose build app` and `docker compose up -d --no-build app` completed on `root@alma`; container `jotai-app` is healthy. `https://jotai.tech/healthz` returned `{"status":"ok"}`, the homepage returned HTTP 200 with valid TLS, and `/api/auth/me` returned guest access disabled with signup mode `approval`. Nginx Proxy Manager remained running and its ports/configuration were not changed.
- Before deployment and before the source-trigger update, online SQLite backups were created at `/srv/jotai/backups/screen_tutor.sqlite3.pre-improved-impl-20261014T145942Z` and `/srv/jotai/backups/screen_tutor.sqlite3.pre-source-trigger-fix-20261014T151212Z`. Both backups passed `PRAGMA integrity_check`; the live database passed the same check and all 15 checked learning schema tables were present.
- The first startup found one legacy private upload whose file was root-owned and unreadable to the app UID. Only that stored file's ownership was corrected, its source-index job was retried, and the job and note reached `ready`; after the final deploy there are no failed or pending source-index jobs and no unreadable source files.
- The slide-upload E2E exposed that the old source-change trigger advanced `processing_revision` while a file's extracted text was first persisted. The trigger now ignores the processor's own transition to `ready` while continuing to invalidate edits. Startup replaces the trigger, and tests verify current slide provenance and stale-edit protection.

The source-to-learning E2E uses controlled provider fakes, not live Gemini. Its ten synthetic lecture uploads are PPTX decks with real slide text and locators; live model extraction, scanned-PDF and video/audio ingestion quality, question quality, rubric quality, and translated-content quality have not been exercised. Format-specific extraction/provenance is also covered in focused source-index tests. The three-month trajectory is simulated deterministic time, not empirical learner calibration.

## Selected mastery defaults

`beta-evidence-v2` uses Beta(1,1) per concept and cognitive-demand level, confirms at `P(proficiency > 0.80) >= 0.90`, retains 0.96 of prior evidence per later valid observation, and marks a level for review after 90 days without a qualifying event. Stored short-interval weights are 0.25 below 30 seconds, 0.5 below 5 minutes, 0.75 below 30 minutes, then 1.0; family and per-run/concept/level caps apply during replay. XP defaults are +5 valid response, +5 correct response, +5 accepted reasoning, +10 assessment completion, +20 completed Ladder, and +15 verified improvement, each with idempotent event keys and repeat caps. Eight deterministic trajectories cover growth, lucky guessing, easy farming, stable proficiency, isolated failure, sustained regression, repeated variants, and grading outage. These are heuristic estimates, not empirically calibrated scores.

Question review flags require at least 30 responses and absolute ability-adjusted residual of 0.25. Prerequisite-edge review flags require at least 10 paired learners and a rate gap of 0.35. Bank replenishment is capped at 16 jobs per day, 20 enqueues per scan, one concurrent worker claim, three tries, and two validated items per concept/level/language slot.

## Live issue follow-up — quiz generation and Mastery Ladder

The production logs showed personal assessment jobs ending with `TimeoutError`, while a Ladder request correctly returned an unavailable state for a class with no validated question-bank items. The assessment generation path made separate provider generation and validation calls but imposed a 70-second overall deadline. Also, the bank worker marked a job complete when its slot was already full but returned a zero processed count; the worker then slept for 15 minutes even when more jobs were queued. A global 10-job daily replenishment budget had already been consumed by another class, leaving this class without queued bank work.

Changes made for this issue: raise bounded quiz/exam and question-bank provider deadlines to 300 seconds; raise the daily replenishment cap to 16; count full-slot no-op jobs as processed so the worker drains queued work; and sleep until a scheduled retry is due instead of applying the 15-minute idle backoff. Regression tests cover no-op draining and retry scheduling. The saved quiz with validation state `legacy_unvalidated` remains blocked from run creation by design, preserving grading integrity; it must be regenerated under the current validator.

Verification and rollout: the focused question-bank suite passed **23 tests**, the full backend suite passed **229 tests**, the changed backend files passed `ruff check --select I,F,B,UP`, and `git diff --check` passed. The updated Docker image was built and deployed to `root@alma`; `jotai-app` is healthy and `https://jotai.tech/healthz` returns `{"status":"ok"}`. Production Gemini question-bank generation for the class initially failed validation twice, then succeeded on bounded retries. The production database now has **two validated items at each of levels 1–4 for both validated concepts**, and all six queued replenishment jobs are complete. The old Ladder remains `unavailable` by design; the student must choose **Start another ladder** to create a fresh run from the now-populated bank. A new personal quiz has not yet been generated with the increased 300-second deadline, so that path is implemented and deployed but live-provider verified only by the related question-bank calls, not by a post-change personal-quiz run.

## Known verification limits

- The end-to-end run uses deterministic provider fakes; live Gemini extraction, question quality, rubric quality, and translated content quality have not been exercised. The fake verifies the Simplified Chinese field and flow, not a translation-quality evaluation.
- The E2E uses ten synthetic PPTX lecture decks; scanned-PDF and video/audio lecture ingestion are covered by focused format tests but were not included in the integrated scenario.
- The three-month trajectory is simulated deterministic time; it is not empirical learner calibration.
- The service is a single-process SQLite deployment. Multi-instance operation requires a different shared database/job deployment design.
- Repo-wide E501 reports 253 existing line-length findings, with none on added backend lines; the other selected Ruff rules pass.
