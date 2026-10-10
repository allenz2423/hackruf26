-- Screen Tutor class library: initial Supabase Postgres migration.
-- Supabase Auth owns auth.users and auth.sessions. Application ownership
-- references Auth user IDs; this schema does not copy or manage sessions.
-- Uploaded binaries stay in private object storage; only their storage keys
-- and extracted text are stored here. Live-session media, transcripts,
-- annotations, and conversations are intentionally outside this schema.

SET search_path = public;

DO $$ BEGIN
    CREATE TYPE course_note_kind AS ENUM (
        'notes', 'document', 'syllabus', 'slides', 'lecture_video', 'lecture_audio'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE note_source_type AS ENUM ('file', 'pasted_text');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE processing_status AS ENUM ('uploading', 'processing', 'ready', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE study_material_kind AS ENUM ('quiz', 'practice_exam', 'study_guide', 'flashcards');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE generation_status AS ENUM ('generating', 'ready', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS course_classes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name            text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT course_classes_name_not_blank CHECK (btrim(name) <> '')
);

CREATE TABLE IF NOT EXISTS course_notes (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id                uuid NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    title                   text NOT NULL,
    kind                    course_note_kind NOT NULL DEFAULT 'notes',
    source_type             note_source_type NOT NULL,
    original_filename       text,
    mime_type               text,
    storage_key             text UNIQUE,
    file_size_bytes         bigint,
    duration_seconds       integer,
    extracted_text          text,
    summary                 text,
    key_topics              jsonb NOT NULL DEFAULT '[]'::jsonb,
    transcript_segments     jsonb NOT NULL DEFAULT '[]'::jsonb,
    syllabus_outline        jsonb,
    status                  processing_status NOT NULL DEFAULT 'processing',
    processing_progress     smallint NOT NULL DEFAULT 0,
    failure_code            text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    processed_at            timestamptz,
    CONSTRAINT course_notes_id_class_unique UNIQUE (id, class_id),
    CONSTRAINT course_notes_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT course_notes_file_size_nonnegative CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    CONSTRAINT course_notes_duration_nonnegative CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    CONSTRAINT course_notes_duration_only_for_lectures CHECK (
        duration_seconds IS NULL OR kind IN ('lecture_video', 'lecture_audio')
    ),
    CONSTRAINT course_notes_transcript_is_array CHECK (jsonb_typeof(transcript_segments) = 'array'),
    CONSTRAINT course_notes_key_topics_is_array CHECK (jsonb_typeof(key_topics) = 'array'),
    CONSTRAINT course_notes_syllabus_outline_is_object CHECK (
        syllabus_outline IS NULL OR jsonb_typeof(syllabus_outline) = 'object'
    ),
    CONSTRAINT course_notes_progress_range CHECK (processing_progress BETWEEN 0 AND 100),
    CONSTRAINT course_notes_ready_is_complete CHECK (status <> 'ready' OR processing_progress = 100),
    CONSTRAINT course_notes_file_source_has_storage CHECK (
        (source_type = 'file' AND storage_key IS NOT NULL AND original_filename IS NOT NULL
            AND mime_type IS NOT NULL AND file_size_bytes IS NOT NULL)
        OR
        (source_type = 'pasted_text' AND storage_key IS NULL AND original_filename IS NULL
            AND mime_type IS NULL AND file_size_bytes IS NULL)
    ),
    CONSTRAINT course_notes_failure_code_only_on_failure CHECK (
        status = 'failed' OR failure_code IS NULL
    ),
    CONSTRAINT course_notes_pasted_text_has_content CHECK (
        source_type <> 'pasted_text' OR (extracted_text IS NOT NULL AND btrim(extracted_text) <> '')
    )
);

CREATE TABLE IF NOT EXISTS study_materials (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id        uuid NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    kind            study_material_kind NOT NULL,
    title           text NOT NULL,
    status          generation_status NOT NULL DEFAULT 'generating',
    scope           jsonb NOT NULL DEFAULT '{"kind":"all"}'::jsonb,
    options         jsonb NOT NULL DEFAULT '{}'::jsonb,
    content         jsonb,
    failure_code    text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    generated_at    timestamptz,
    CONSTRAINT study_materials_id_class_unique UNIQUE (id, class_id),
    CONSTRAINT study_materials_title_not_blank CHECK (btrim(title) <> ''),
    CONSTRAINT study_materials_scope_is_object CHECK (jsonb_typeof(scope) = 'object'),
    CONSTRAINT study_materials_options_is_object CHECK (jsonb_typeof(options) = 'object'),
    CONSTRAINT study_materials_content_is_object CHECK (
        content IS NULL OR jsonb_typeof(content) = 'object'
    ),
    CONSTRAINT study_materials_ready_has_content CHECK (status <> 'ready' OR content IS NOT NULL),
    CONSTRAINT study_materials_failure_code_only_on_failure CHECK (
        status = 'failed' OR failure_code IS NULL
    )
);

-- Material-level source links make every quiz/test traceable to notes in its
-- own class. Per-question page, slide, or lecture timestamp citations live in
-- the validated content JSON.
CREATE TABLE IF NOT EXISTS study_material_sources (
    material_id     uuid NOT NULL,
    class_id        uuid NOT NULL,
    note_id         uuid NOT NULL,
    position        smallint NOT NULL DEFAULT 0,
    PRIMARY KEY (material_id, note_id),
    CONSTRAINT study_material_sources_position_unique UNIQUE (material_id, position),
    CONSTRAINT study_material_sources_position_nonnegative CHECK (position >= 0),
    CONSTRAINT study_material_sources_material_class_fk
        FOREIGN KEY (material_id, class_id)
        REFERENCES study_materials(id, class_id) ON DELETE CASCADE,
    CONSTRAINT study_material_sources_note_class_fk
        FOREIGN KEY (note_id, class_id)
        REFERENCES course_notes(id, class_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS study_material_attempts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    material_id     uuid NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    answers         jsonb NOT NULL,
    score_percent   numeric(5, 2) NOT NULL,
    completed_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT study_material_attempts_answers_is_object CHECK (jsonb_typeof(answers) = 'object'),
    CONSTRAINT study_material_attempts_score_range CHECK (score_percent BETWEEN 0 AND 100)
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

CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS course_classes_set_updated_at ON course_classes;
CREATE TRIGGER course_classes_set_updated_at
    BEFORE UPDATE ON course_classes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS course_notes_set_updated_at ON course_notes;
CREATE TRIGGER course_notes_set_updated_at
    BEFORE UPDATE ON course_notes
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS study_materials_set_updated_at ON study_materials;
CREATE TRIGGER study_materials_set_updated_at
    BEFORE UPDATE ON study_materials
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- A generated item is derived data. Removing any note it cites removes that
-- item and its attempts, matching the class-library deletion contract.
CREATE OR REPLACE FUNCTION delete_materials_for_note()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
    DELETE FROM study_materials
    WHERE id IN (
        SELECT material_id
        FROM public.study_material_sources
        WHERE note_id = OLD.id
    );
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS course_notes_delete_derived_materials ON course_notes;
CREATE TRIGGER course_notes_delete_derived_materials
    BEFORE DELETE ON course_notes
    FOR EACH ROW EXECUTE FUNCTION delete_materials_for_note();

-- The backend will use a server-only database connection and enforce class
-- ownership in its API. Supabase's browser-facing Data API stays closed until
-- matching owner-scoped policies are deliberately added.
ALTER TABLE public.course_classes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.course_notes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.study_materials ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.study_material_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.study_material_attempts ENABLE ROW LEVEL SECURITY;
