-- SQLite schema for backend-owned class library data.
-- Owner IDs are stored as TEXT so local/demo deployments can use a stable UUID.

CREATE TABLE IF NOT EXISTS course_classes (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    name        TEXT NOT NULL CHECK (trim(name) <> ''),
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

CREATE TRIGGER IF NOT EXISTS course_notes_delete_derived_materials
BEFORE DELETE ON course_notes
FOR EACH ROW
BEGIN
    DELETE FROM study_materials
    WHERE id IN (SELECT material_id FROM study_material_sources WHERE note_id = OLD.id);
END;
