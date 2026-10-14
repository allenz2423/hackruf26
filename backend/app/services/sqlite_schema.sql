-- SQLite schema for backend-owned class library data.
-- Owner IDs are stored as TEXT so local/demo deployments can use a stable UUID.

CREATE TABLE IF NOT EXISTS course_classes (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL CHECK (trim(name) <> ''),
    -- Card look picked in the browser: ids from fixed lists, NULL for the default.
    color       TEXT,
    icon        TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS course_notes (
    id                      TEXT PRIMARY KEY,
    class_id                TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    title                   TEXT NOT NULL CHECK (trim(title) <> ''),
    kind                    TEXT NOT NULL DEFAULT 'notes'
        CHECK (kind IN ('notes', 'document', 'syllabus', 'slides', 'lecture_video', 'lecture_audio')),
    source_type             TEXT NOT NULL CHECK (source_type IN ('file', 'pasted_text')),
    original_filename       TEXT,
    mime_type               TEXT,
    storage_key             TEXT UNIQUE,
    file_size_bytes         INTEGER CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    duration_seconds        INTEGER CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    extracted_text          TEXT,
    summary                 TEXT,
    key_topics              TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(key_topics) AND json_type(key_topics) = 'array'),
    transcript_segments     TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(transcript_segments) AND json_type(transcript_segments) = 'array'),
    syllabus_outline        TEXT
        CHECK (syllabus_outline IS NULL OR (json_valid(syllabus_outline) AND json_type(syllabus_outline) = 'object')),
    status                  TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'ready', 'failed')),
    processing_progress     INTEGER NOT NULL DEFAULT 0 CHECK (processing_progress BETWEEN 0 AND 100),
    processing_revision     INTEGER NOT NULL DEFAULT 0,
    failure_code            TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    processed_at            TEXT,
    UNIQUE (id, class_id),
    CHECK (duration_seconds IS NULL OR kind IN ('lecture_video', 'lecture_audio')),
    CHECK (status <> 'ready' OR processing_progress = 100),
    CHECK (status = 'failed' OR failure_code IS NULL),
    CHECK (
        (source_type = 'file' AND storage_key IS NOT NULL AND original_filename IS NOT NULL
            AND mime_type IS NOT NULL AND file_size_bytes IS NOT NULL)
        OR
        (source_type = 'pasted_text' AND storage_key IS NULL AND original_filename IS NULL
            AND mime_type IS NULL AND file_size_bytes IS NULL)
    ),
    CHECK (source_type <> 'pasted_text' OR (extracted_text IS NOT NULL AND trim(extracted_text) <> ''))
);

CREATE TABLE IF NOT EXISTS study_materials (
    id              TEXT PRIMARY KEY,
    class_id        TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('quiz', 'practice_exam', 'study_guide', 'flashcards')),
    title           TEXT NOT NULL CHECK (trim(title) <> ''),
    status          TEXT NOT NULL DEFAULT 'generating'
        CHECK (status IN ('generating', 'ready', 'failed')),
    scope           TEXT NOT NULL DEFAULT '{"kind":"all"}'
        CHECK (json_valid(scope) AND json_type(scope) = 'object'),
    options         TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(options) AND json_type(options) = 'object'),
    content         TEXT
        CHECK (content IS NULL OR (json_valid(content) AND json_type(content) = 'object')),
    failure_code    TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    generated_at    TEXT,
    sources_outdated INTEGER NOT NULL DEFAULT 0 CHECK (sources_outdated IN (0, 1)),
    sources_missing INTEGER NOT NULL DEFAULT 0 CHECK (sources_missing IN (0, 1)),
    context_truncated INTEGER NOT NULL DEFAULT 0 CHECK (context_truncated IN (0, 1)),
    UNIQUE (id, class_id),
    CHECK (status <> 'ready' OR content IS NOT NULL),
    CHECK (status = 'failed' OR failure_code IS NULL)
);

CREATE TABLE IF NOT EXISTS upload_documents (
    note_id           TEXT NOT NULL REFERENCES course_notes(id) ON DELETE CASCADE,
    detail_level      TEXT NOT NULL CHECK (detail_level IN ('concise', 'balanced', 'detailed')),
    job_id            TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('generating', 'ready', 'failed')),
    progress          INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    content           TEXT CHECK (content IS NULL OR json_valid(content)),
    original_text     TEXT NOT NULL,
    pdf               BLOB,
    latex             BLOB,
    failure_code      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (note_id, detail_level),
    CHECK (status <> 'ready' OR (content IS NOT NULL AND pdf IS NOT NULL AND latex IS NOT NULL))
);

CREATE TRIGGER IF NOT EXISTS course_notes_invalidate_documents
AFTER UPDATE OF extracted_text, kind, title ON course_notes
WHEN OLD.extracted_text IS NOT NEW.extracted_text OR OLD.kind IS NOT NEW.kind OR OLD.title IS NOT NEW.title
BEGIN
    DELETE FROM upload_documents WHERE note_id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS study_material_files (
    material_id TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    format      TEXT NOT NULL CHECK (format IN ('pdf', 'latex')),
    content     BLOB NOT NULL CHECK (length(content) > 0),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (material_id, format)
);

CREATE TABLE IF NOT EXISTS study_material_sources (
    material_id TEXT NOT NULL,
    class_id    TEXT NOT NULL,
    note_id     TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
    PRIMARY KEY (material_id, note_id),
    UNIQUE (material_id, position),
    FOREIGN KEY (material_id, class_id)
        REFERENCES study_materials(id, class_id) ON DELETE CASCADE,
    FOREIGN KEY (note_id, class_id)
        REFERENCES course_notes(id, class_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS study_material_attempts (
    id              TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    answers         TEXT NOT NULL CHECK (json_valid(answers) AND json_type(answers) = 'object'),
    score_percent   REAL NOT NULL CHECK (score_percent BETWEEN 0 AND 100),
    completed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Reasoning-first quiz runs are separate from legacy score-only attempts.
-- Every row contains intentionally submitted reasoning only; live media and
-- unsent transcript fragments never enter these tables.
CREATE TABLE IF NOT EXISTS quiz_runs (
    id              TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    operation_id    TEXT NOT NULL,
    mode            TEXT NOT NULL DEFAULT 'standard'
        CHECK (mode IN ('standard', 'reasoning')),
    status          TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'completed')),
    score_percent   REAL CHECK (score_percent IS NULL OR score_percent BETWEEN 0 AND 100),
    submission_operation_id TEXT,
    submission_digest TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at    TEXT,
    UNIQUE (material_id, owner_id, operation_id),
    CHECK ((status = 'completed' AND score_percent IS NOT NULL AND completed_at IS NOT NULL)
        OR (status = 'in_progress' AND score_percent IS NULL AND completed_at IS NULL))
);

CREATE TABLE IF NOT EXISTS quiz_question_attempts (
    id                              TEXT PRIMARY KEY,
    run_id                          TEXT NOT NULL REFERENCES quiz_runs(id) ON DELETE CASCADE,
    question_id                     TEXT NOT NULL,
    state                           TEXT NOT NULL DEFAULT 'awaiting_reasoning'
        CHECK (state IN ('awaiting_reasoning', 'evaluating_reasoning', 'reasoning_insufficient',
                         'reasoning_failed', 'reasoning_accepted', 'submitted', 'completed', 'skipped')),
    accepted_through_turn           INTEGER,
    accepted_snapshot_hash          TEXT,
    submitted_through_turn          INTEGER,
    submitted_snapshot_hash         TEXT,
    final_answer                    TEXT,
    answer_operation_id             TEXT,
    answer_operation_digest         TEXT,
    correct                         INTEGER CHECK (correct IS NULL OR correct IN (0, 1)),
    answer_feedback                 TEXT,
    reasoning_feedback              TEXT,
    feedback_status                 TEXT NOT NULL DEFAULT 'not_requested'
        CHECK (feedback_status IN ('not_requested', 'pending', 'available', 'failed')),
    feedback_operation_id           TEXT,
    active_evaluation_operation_id TEXT,
    skip_operation_id               TEXT,
    created_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    submitted_at                    TEXT,
    completed_at                    TEXT,
    skipped_at                      TEXT,
    version                         INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    UNIQUE (run_id, question_id),
    CHECK (state <> 'reasoning_accepted' OR
        (accepted_through_turn IS NOT NULL AND accepted_snapshot_hash IS NOT NULL)),
    CHECK (state NOT IN ('submitted', 'completed') OR
        (answer_operation_id IS NOT NULL AND submitted_through_turn IS NOT NULL
         AND submitted_snapshot_hash IS NOT NULL AND submitted_at IS NOT NULL)),
    CHECK (state <> 'skipped' OR (skip_operation_id IS NOT NULL AND skipped_at IS NOT NULL)),
    CHECK (state <> 'completed' OR (correct IS NOT NULL AND completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS quiz_reasoning_turns (
    id              TEXT PRIMARY KEY,
    attempt_id      TEXT NOT NULL REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    sequence_number INTEGER NOT NULL CHECK (sequence_number > 0),
    operation_id    TEXT NOT NULL,
    content_digest  TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('text', 'voice')),
    text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 12000),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (attempt_id, sequence_number),
    UNIQUE (attempt_id, operation_id)
);

CREATE TABLE IF NOT EXISTS quiz_reasoning_evaluations (
    id              TEXT PRIMARY KEY,
    attempt_id      TEXT NOT NULL REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    operation_id    TEXT NOT NULL,
    snapshot_hash   TEXT NOT NULL,
    through_turn    INTEGER NOT NULL CHECK (through_turn > 0),
    status          TEXT NOT NULL CHECK (status IN ('pending', 'available', 'failed', 'superseded')),
    result          TEXT CHECK (result IS NULL OR result IN ('sufficient', 'insufficient')),
    internal_reason TEXT,
    follow_up       TEXT,
    evaluator_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at    TEXT,
    UNIQUE (attempt_id, operation_id),
    CHECK (status <> 'available' OR (result IS NOT NULL AND internal_reason IS NOT NULL
        AND completed_at IS NOT NULL)),
    CHECK (result <> 'insufficient' OR follow_up IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS quiz_feedback_operations (
    id              TEXT PRIMARY KEY,
    attempt_id      TEXT NOT NULL REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    operation_id    TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'available', 'failed', 'superseded')),
    feedback_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    completed_at    TEXT,
    UNIQUE (attempt_id, operation_id)
);

-- Aggregate-only diagnostic counters. No owner, question, reasoning, or text
-- values are stored in metrics.
CREATE TABLE IF NOT EXISTS quiz_reasoning_metrics (
    metric_date         TEXT PRIMARY KEY,
    question_attempt_count INTEGER NOT NULL DEFAULT 0,
    evaluation_count    INTEGER NOT NULL DEFAULT 0,
    first_evaluation_count INTEGER NOT NULL DEFAULT 0,
    first_accepted_count INTEGER NOT NULL DEFAULT 0,
    followup_turn_count INTEGER NOT NULL DEFAULT 0,
    accepted_attempt_count INTEGER NOT NULL DEFAULT 0,
    skip_count          INTEGER NOT NULL DEFAULT 0,
    evaluation_failure_count INTEGER NOT NULL DEFAULT 0,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    latency_total_ms    INTEGER NOT NULL DEFAULT 0,
    latency_median_ms   REAL NOT NULL DEFAULT 0,
    latency_p95_ms      REAL NOT NULL DEFAULT 0,
    latency_le_1s       INTEGER NOT NULL DEFAULT 0,
    latency_le_3s       INTEGER NOT NULL DEFAULT 0,
    latency_le_10s      INTEGER NOT NULL DEFAULT 0,
    latency_le_30s      INTEGER NOT NULL DEFAULT 0,
    latency_gt_30s      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quiz_reasoning_latency_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_date TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL CHECK (latency_ms >= 0)
);

CREATE INDEX IF NOT EXISTS idx_reasoning_latency_date
    ON quiz_reasoning_latency_samples(metric_date);

CREATE TABLE IF NOT EXISTS uploaded_assessments (
    id                      TEXT PRIMARY KEY,
    class_id                TEXT NOT NULL,
    note_id                 TEXT NOT NULL UNIQUE,
    processing_status       TEXT NOT NULL DEFAULT 'processing'
        CHECK (processing_status IN ('processing', 'ready', 'needs_review', 'failed')),
    has_student_responses   INTEGER NOT NULL DEFAULT 0 CHECK (has_student_responses IN (0, 1)),
    questions               TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(questions) AND json_type(questions) = 'array'),
    grading_mode            TEXT NOT NULL DEFAULT 'not_graded'
        CHECK (grading_mode IN ('not_graded', 'answer_key', 'class_notes_estimated', 'needs_review')),
    score_percent           REAL CHECK (score_percent IS NULL OR score_percent BETWEEN 0 AND 100),
    grading_result          TEXT
        CHECK (grading_result IS NULL OR (json_valid(grading_result) AND json_type(grading_result) = 'object')),
    grade_confidence        REAL CHECK (grade_confidence IS NULL OR grade_confidence BETWEEN 0 AND 1),
    failure_code            TEXT,
    processed_at            TEXT,
    graded_at               TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, class_id),
    FOREIGN KEY (note_id, class_id)
        REFERENCES course_notes(id, class_id) ON DELETE CASCADE,
    CHECK (processing_status = 'failed' OR failure_code IS NULL),
    CHECK (
        (grading_mode IN ('answer_key', 'class_notes_estimated')
            AND has_student_responses = 1 AND score_percent IS NOT NULL AND graded_at IS NOT NULL)
        OR
        (grading_mode IN ('not_graded', 'needs_review') AND score_percent IS NULL AND graded_at IS NULL)
    ),
    CHECK (
        (processing_status IN ('processing', 'failed') AND score_percent IS NULL AND graded_at IS NULL)
        OR
        (processing_status = 'ready' AND (
            (grading_mode = 'not_graded' AND has_student_responses = 0
                AND score_percent IS NULL AND graded_at IS NULL)
            OR
            (grading_mode IN ('answer_key', 'class_notes_estimated')
                AND has_student_responses = 1 AND score_percent IS NOT NULL AND graded_at IS NOT NULL)
        ))
        OR
        (processing_status = 'needs_review' AND grading_mode = 'needs_review'
            AND score_percent IS NULL AND graded_at IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS study_material_flags (
    id              TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL,
    class_id        TEXT NOT NULL,
    reporter_id     TEXT NOT NULL,
    question_id     TEXT NOT NULL CHECK (trim(question_id) <> ''),
    reason          TEXT CHECK (reason IS NULL OR length(reason) <= 1000),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (material_id, reporter_id, question_id),
    FOREIGN KEY (material_id, class_id)
        REFERENCES study_materials(id, class_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS course_classes_owner_created_idx
    ON course_classes (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS course_notes_class_created_idx
    ON course_notes (class_id, created_at DESC);
CREATE INDEX IF NOT EXISTS course_notes_class_status_idx
    ON course_notes (class_id, status);
CREATE INDEX IF NOT EXISTS study_materials_class_created_idx
    ON study_materials (class_id, created_at DESC);
CREATE INDEX IF NOT EXISTS study_materials_class_status_idx
    ON study_materials (class_id, status);
CREATE INDEX IF NOT EXISTS study_material_attempts_material_completed_idx
    ON study_material_attempts (material_id, completed_at DESC);
CREATE INDEX IF NOT EXISTS uploaded_assessments_class_created_idx
    ON uploaded_assessments (class_id, created_at DESC);
CREATE INDEX IF NOT EXISTS uploaded_assessments_class_status_idx
    ON uploaded_assessments (class_id, processing_status);
CREATE INDEX IF NOT EXISTS study_material_flags_reporter_created_idx
    ON study_material_flags (reporter_id, created_at DESC);

-- Accounts and cookie-backed login sessions. Class rows keep a plain TEXT
-- owner_id so existing local data can be adopted by an account.
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL COLLATE NOCASE UNIQUE,
    email         TEXT COLLATE NOCASE UNIQUE,
    display_name  TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_demo       INTEGER NOT NULL DEFAULT 0,
    avatar        TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS user_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS user_sessions_user_idx ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS user_sessions_expires_idx ON user_sessions (expires_at);

-- Opted-in derived tutoring recaps. Raw conversations and media are never stored.
CREATE TABLE IF NOT EXISTS tutor_memory_scopes (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id    TEXT REFERENCES course_classes(id) ON DELETE CASCADE,
    scope_key   TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    revision    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (owner_id, scope_key),
    CHECK (scope_key = coalesce(class_id, 'general'))
);

CREATE TABLE IF NOT EXISTS tutor_recaps (
    id          TEXT PRIMARY KEY,
    scope_id    TEXT NOT NULL REFERENCES tutor_memory_scopes(id) ON DELETE CASCADE,
    session_id  TEXT NOT NULL UNIQUE,
    content     TEXT NOT NULL CHECK (json_valid(content) AND json_type(content) = 'object'),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS tutor_recaps_scope_idx ON tutor_recaps (scope_id, created_at DESC);
