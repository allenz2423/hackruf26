-- SQLite schema for backend-owned class library data.
-- Owner IDs are stored as TEXT so local/demo deployments can use a stable UUID.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

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

CREATE TABLE IF NOT EXISTS class_memberships (
    class_id    TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'left', 'removed')),
    joined_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    added_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    ended_at    TEXT,
    PRIMARY KEY (class_id, user_id)
);

CREATE TABLE IF NOT EXISTS class_membership_capabilities (
    class_id    TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    capability  TEXT NOT NULL CHECK (capability IN (
        'read', 'create', 'edit', 'delete', 'delete_others', 'invite', 'manage_members',
        'manage_permissions'
    )),
    granted_by  TEXT REFERENCES users(id) ON DELETE SET NULL,
    granted_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (class_id, user_id, capability),
    FOREIGN KEY (class_id, user_id)
        REFERENCES class_memberships(class_id, user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS class_invitations (
    id               TEXT PRIMARY KEY,
    class_id         TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    code_digest      TEXT NOT NULL UNIQUE,
    created_by       TEXT NOT NULL REFERENCES users(id),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    expires_at       TEXT NOT NULL,
    max_uses         INTEGER NOT NULL CHECK (max_uses BETWEEN 1 AND 100),
    successful_uses  INTEGER NOT NULL DEFAULT 0 CHECK (successful_uses >= 0),
    revoked_at       TEXT,
    CHECK (successful_uses <= max_uses)
);

CREATE TABLE IF NOT EXISTS class_invitation_capabilities (
    invitation_id TEXT NOT NULL REFERENCES class_invitations(id) ON DELETE CASCADE,
    capability    TEXT NOT NULL CHECK (capability IN (
        'read', 'create', 'edit', 'delete', 'delete_others', 'invite', 'manage_members',
        'manage_permissions'
    )),
    PRIMARY KEY (invitation_id, capability)
);

CREATE INDEX IF NOT EXISTS class_memberships_user_status_idx
    ON class_memberships (user_id, status, joined_at DESC);
CREATE INDEX IF NOT EXISTS class_invitations_class_created_idx
    ON class_invitations (class_id, created_at DESC);

CREATE TABLE IF NOT EXISTS course_notes (
    id                      TEXT PRIMARY KEY,
    class_id                TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    resource_scope          TEXT NOT NULL DEFAULT 'class_owned'
        CHECK (resource_scope IN ('class_owned', 'student_contribution')),
    created_by              TEXT REFERENCES users(id) ON DELETE SET NULL,
    publication_state       TEXT NOT NULL DEFAULT 'published'
        CHECK (publication_state IN ('draft', 'scheduled', 'published')),
    release_at              TEXT,
    published_at            TEXT,
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
    source_index_status     TEXT NOT NULL DEFAULT 'unindexed'
        CHECK (source_index_status IN ('unindexed', 'processing', 'ready', 'failed')),
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
    CHECK (source_type <> 'pasted_text' OR (extracted_text IS NOT NULL AND trim(extracted_text) <> '')),
    CHECK ((publication_state = 'scheduled' AND release_at IS NOT NULL) OR
        (publication_state <> 'scheduled' AND release_at IS NULL))
);

-- Immutable source revisions and stable logical section/chunk identities.
-- Text lives in source_chunk_revisions so historical citations remain exact.
CREATE TABLE IF NOT EXISTS source_revisions (
    id                  TEXT PRIMARY KEY,
    note_id             TEXT NOT NULL REFERENCES course_notes(id) ON DELETE CASCADE,
    revision_number     INTEGER NOT NULL CHECK (revision_number > 0),
    processing_revision INTEGER NOT NULL CHECK (processing_revision >= 0),
    source_sha256       TEXT,
    extracted_sha256    TEXT NOT NULL,
    extraction_method   TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (note_id, revision_number),
    UNIQUE (note_id, processing_revision),
    UNIQUE (id, note_id)
);

CREATE TABLE IF NOT EXISTS source_index_heads (
    note_id             TEXT PRIMARY KEY REFERENCES course_notes(id) ON DELETE CASCADE,
    revision_id         TEXT NOT NULL UNIQUE REFERENCES source_revisions(id) ON DELETE CASCADE,
    processing_revision INTEGER NOT NULL DEFAULT 0 CHECK (processing_revision >= 0),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS source_sections (
    id          TEXT PRIMARY KEY,
    note_id     TEXT NOT NULL REFERENCES course_notes(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, note_id)
);

CREATE TABLE IF NOT EXISTS source_section_revisions (
    revision_id       TEXT NOT NULL REFERENCES source_revisions(id) ON DELETE CASCADE,
    section_id        TEXT NOT NULL REFERENCES source_sections(id) ON DELETE CASCADE,
    parent_section_id TEXT REFERENCES source_sections(id) ON DELETE SET NULL,
    section_order     INTEGER NOT NULL CHECK (section_order >= 0),
    title             TEXT NOT NULL,
    heading_path      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(heading_path)),
    locator           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(locator)),
    inferred          INTEGER NOT NULL DEFAULT 0 CHECK (inferred IN (0, 1)),
    PRIMARY KEY (revision_id, section_id),
    UNIQUE (revision_id, section_order)
);

CREATE TABLE IF NOT EXISTS source_chunks (
    id          TEXT PRIMARY KEY,
    section_id  TEXT NOT NULL REFERENCES source_sections(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (id, section_id)
);

CREATE TABLE IF NOT EXISTS source_chunk_revisions (
    id                TEXT PRIMARY KEY,
    revision_id       TEXT NOT NULL REFERENCES source_revisions(id) ON DELETE CASCADE,
    section_id        TEXT NOT NULL REFERENCES source_sections(id) ON DELETE CASCADE,
    chunk_id          TEXT NOT NULL REFERENCES source_chunks(id) ON DELETE CASCADE,
    chunk_order       INTEGER NOT NULL CHECK (chunk_order >= 0),
    locator           TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(locator)),
    text              TEXT NOT NULL CHECK (length(text) > 0),
    content_sha256    TEXT NOT NULL,
    token_count       INTEGER NOT NULL CHECK (token_count >= 0),
    extraction_method TEXT NOT NULL,
    provenance        TEXT NOT NULL DEFAULT 'source'
        CHECK (provenance IN ('source', 'generated_description')),
    UNIQUE (revision_id, chunk_order),
    UNIQUE (chunk_id, revision_id)
);

CREATE TABLE IF NOT EXISTS source_index_jobs (
    id                  TEXT PRIMARY KEY,
    note_id             TEXT NOT NULL REFERENCES course_notes(id) ON DELETE CASCADE,
    processing_revision INTEGER NOT NULL CHECK (processing_revision >= 0),
    status              TEXT NOT NULL CHECK (status IN ('queued', 'running', 'ready', 'failed', 'superseded')),
    attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts        INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    last_error_code     TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    completed_at        TEXT,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (note_id, processing_revision)
);

CREATE INDEX IF NOT EXISTS source_revisions_note_revision_idx
    ON source_revisions(note_id, revision_number DESC);
CREATE INDEX IF NOT EXISTS source_chunk_revisions_revision_idx
    ON source_chunk_revisions(revision_id, chunk_order);
CREATE INDEX IF NOT EXISTS source_index_jobs_status_idx
    ON source_index_jobs(status, created_at, id);

CREATE VIRTUAL TABLE IF NOT EXISTS source_chunks_fts USING fts5(
    chunk_revision_id UNINDEXED,
    note_id UNINDEXED,
    revision_id UNINDEXED,
    title,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS source_chunk_revision_fts_insert
AFTER INSERT ON source_chunk_revisions
BEGIN
    INSERT INTO source_chunks_fts(chunk_revision_id, note_id, revision_id, title, text)
    SELECT NEW.id, r.note_id, NEW.revision_id, s.title, NEW.text
    FROM source_revisions r
    JOIN source_section_revisions s
      ON s.revision_id=NEW.revision_id AND s.section_id=NEW.section_id
    WHERE r.id=NEW.revision_id;
END;

CREATE TRIGGER IF NOT EXISTS source_chunk_revision_fts_delete
AFTER DELETE ON source_chunk_revisions
BEGIN
    DELETE FROM source_chunks_fts WHERE chunk_revision_id=OLD.id;
END;

-- Class-scoped concept graph; all extraction output begins as a proposal.
CREATE TABLE IF NOT EXISTS class_concepts (
    id                TEXT PRIMARY KEY,
    class_id          TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    canonical_name    TEXT NOT NULL CHECK (length(trim(canonical_name)) BETWEEN 1 AND 160),
    normalized_name   TEXT NOT NULL,
    description       TEXT NOT NULL CHECK (length(trim(description)) BETWEEN 1 AND 1500),
    parent_concept_id TEXT,
    status            TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','validated','rejected','deprecated','merged')),
    provenance        TEXT NOT NULL CHECK (provenance IN ('explicit','inferred','manual')),
    version           INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    merged_into_id    TEXT,
    created_by        TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (id,class_id),
    FOREIGN KEY (parent_concept_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE SET NULL,
    FOREIGN KEY (merged_into_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE SET NULL,
    CHECK (parent_concept_id IS NULL OR parent_concept_id <> id),
    CHECK (merged_into_id IS NULL OR merged_into_id <> id)
);

CREATE UNIQUE INDEX IF NOT EXISTS class_concepts_active_name_idx
    ON class_concepts(class_id,normalized_name)
    WHERE status IN ('proposed','validated','deprecated');
CREATE INDEX IF NOT EXISTS class_concepts_class_status_idx
    ON class_concepts(class_id,status,canonical_name);

CREATE TABLE IF NOT EXISTS class_concept_aliases (
    id             TEXT PRIMARY KEY,
    class_id       TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    concept_id     TEXT NOT NULL,
    name           TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 160),
    normalized_name TEXT NOT NULL,
    language       TEXT NOT NULL DEFAULT 'und',
    status         TEXT NOT NULL DEFAULT 'validated'
        CHECK (status IN ('proposed','validated','rejected')),
    created_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (id,class_id),
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS class_concept_aliases_concept_idx
    ON class_concept_aliases(concept_id);

CREATE TABLE IF NOT EXISTS class_concept_edges (
    id               TEXT PRIMARY KEY,
    class_id         TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    from_concept_id  TEXT NOT NULL,
    to_concept_id    TEXT NOT NULL,
    type             TEXT NOT NULL CHECK (type IN ('part_of','prerequisite_for','related_to','applied_in')),
    necessity        TEXT NOT NULL DEFAULT 'none' CHECK (necessity IN ('none','essential','helpful')),
    provenance       TEXT NOT NULL CHECK (provenance IN ('explicit','inferred','manual')),
    explanation      TEXT NOT NULL CHECK (length(trim(explanation)) BETWEEN 1 AND 1000),
    status           TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','validated','rejected')),
    version          INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (id,class_id),
    FOREIGN KEY (from_concept_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE CASCADE,
    FOREIGN KEY (to_concept_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE CASCADE,
    CHECK (from_concept_id <> to_concept_id),
    CHECK (
        (type='prerequisite_for' AND necessity IN ('essential','helpful'))
        OR (type<>'prerequisite_for' AND necessity='none')
    )
);
UPDATE class_concept_edges AS e SET status='rejected',version=version+1
WHERE e.status IN ('proposed','validated') AND EXISTS (
    SELECT 1 FROM class_concept_edges prior
    WHERE prior.class_id=e.class_id AND prior.from_concept_id=e.from_concept_id
      AND prior.to_concept_id=e.to_concept_id AND prior.type=e.type
      AND prior.status IN ('proposed','validated') AND prior.id<e.id
);
DROP INDEX IF EXISTS class_concept_edges_unique_idx;
CREATE UNIQUE INDEX class_concept_edges_unique_idx
    ON class_concept_edges(class_id,from_concept_id,to_concept_id,type)
    WHERE status IN ('proposed','validated');
CREATE INDEX IF NOT EXISTS class_concept_edges_class_status_idx
    ON class_concept_edges(class_id,status,type);

CREATE TABLE IF NOT EXISTS class_concept_source_refs (
    id                 TEXT PRIMARY KEY,
    class_id           TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    concept_id         TEXT,
    edge_id            TEXT,
    chunk_revision_id  TEXT NOT NULL REFERENCES source_chunk_revisions(id) ON DELETE CASCADE,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK ((concept_id IS NOT NULL AND edge_id IS NULL) OR
           (concept_id IS NULL AND edge_id IS NOT NULL)),
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id) ON DELETE CASCADE,
    FOREIGN KEY (edge_id,class_id) REFERENCES class_concept_edges(id,class_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS class_concept_source_refs_concept_idx
    ON class_concept_source_refs(concept_id,chunk_revision_id) WHERE concept_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS class_concept_source_refs_edge_idx
    ON class_concept_source_refs(edge_id,chunk_revision_id) WHERE edge_id IS NOT NULL;

-- Validated class question bank. Prompt-facing data and private grading data are
-- kept server-side; every item has exact, immutable source revision links.
CREATE TABLE IF NOT EXISTS class_question_bank_items (
    id                TEXT PRIMARY KEY,
    class_id          TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    family_id         TEXT NOT NULL,
    family_signature  TEXT NOT NULL,
    prompt            TEXT NOT NULL CHECK (length(trim(prompt)) BETWEEN 1 AND 5000),
    kind              TEXT NOT NULL CHECK (kind IN ('multiple_choice','true_false','short_answer')),
    level             INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    language          TEXT NOT NULL CHECK (length(language) BETWEEN 2 AND 35),
    choices_json      TEXT NOT NULL CHECK (json_valid(choices_json) AND json_type(choices_json)='array'),
    grading_json      TEXT NOT NULL CHECK (json_valid(grading_json) AND json_type(grading_json)='object'),
    state             TEXT NOT NULL DEFAULT 'validated' CHECK (state IN ('validated','deprecated')),
    exposure_count   INTEGER NOT NULL DEFAULT 0 CHECK (exposure_count >= 0),
    validator_version TEXT NOT NULL,
    generation_model  TEXT NOT NULL,
    generation_version TEXT NOT NULL,
    created_by        TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (id,class_id)
);
CREATE INDEX IF NOT EXISTS class_question_bank_select_idx
    ON class_question_bank_items(class_id,state,kind,level,language,created_at);
CREATE INDEX IF NOT EXISTS class_question_bank_family_idx
    ON class_question_bank_items(class_id,family_id);

CREATE TABLE IF NOT EXISTS class_question_bank_concepts (
    item_id      TEXT NOT NULL,
    class_id     TEXT NOT NULL,
    concept_id   TEXT NOT NULL,
    relation     TEXT NOT NULL CHECK (relation IN ('primary','secondary')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (item_id,concept_id),
    FOREIGN KEY (item_id,class_id) REFERENCES class_question_bank_items(id,class_id)
        ON DELETE CASCADE,
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS class_question_bank_concepts_concept_idx
    ON class_question_bank_concepts(class_id,concept_id,relation);

CREATE TABLE IF NOT EXISTS class_question_bank_sources (
    item_id                 TEXT NOT NULL,
    class_id                TEXT NOT NULL,
    chunk_revision_id       TEXT NOT NULL REFERENCES source_chunk_revisions(id) ON DELETE CASCADE,
    source_order            INTEGER NOT NULL CHECK (source_order >= 0),
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (item_id,chunk_revision_id),
    FOREIGN KEY (item_id,class_id) REFERENCES class_question_bank_items(id,class_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS class_question_bank_sources_chunk_idx
    ON class_question_bank_sources(chunk_revision_id,item_id);

CREATE TRIGGER IF NOT EXISTS class_question_bank_orphaned_source
AFTER DELETE ON class_question_bank_sources
WHEN NOT EXISTS (SELECT 1 FROM class_question_bank_sources WHERE item_id=OLD.item_id)
BEGIN
    UPDATE class_question_bank_items SET state='deprecated' WHERE id=OLD.item_id;
END;

CREATE TABLE IF NOT EXISTS class_question_generation_operations (
    id            TEXT PRIMARY KEY,
    class_id      TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    requester_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    operation_id  TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('running','complete','failed')),
    material_id   TEXT REFERENCES study_materials(id) ON DELETE SET NULL,
    error_code    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (class_id,requester_id,operation_id)
);

CREATE TABLE IF NOT EXISTS study_material_feedback_releases (
    material_id TEXT PRIMARY KEY REFERENCES study_materials(id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL UNIQUE,
    released_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    released_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS class_concept_merge_history (
    id                 TEXT PRIMARY KEY,
    class_id           TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    source_concept_id  TEXT NOT NULL,
    target_concept_id  TEXT NOT NULL,
    source_name        TEXT NOT NULL,
    source_description TEXT NOT NULL,
    source_version     INTEGER NOT NULL,
    merged_by          TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (source_concept_id),
    FOREIGN KEY (source_concept_id,class_id) REFERENCES class_concepts(id,class_id),
    FOREIGN KEY (target_concept_id,class_id) REFERENCES class_concepts(id,class_id),
    CHECK (source_concept_id <> target_concept_id)
);

CREATE TABLE IF NOT EXISTS class_concept_extraction_jobs (
    id                 TEXT PRIMARY KEY,
    class_id           TEXT NOT NULL,
    note_id            TEXT NOT NULL,
    source_revision_id TEXT NOT NULL REFERENCES source_revisions(id) ON DELETE CASCADE,
    requested_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
    status             TEXT NOT NULL CHECK (status IN ('queued','running','complete','failed','superseded')),
    attempts           INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts       INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    error_code         TEXT,
    prompt_version     TEXT NOT NULL DEFAULT 'concept-graph-extraction-v1',
    model_name         TEXT,
    available_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at         TEXT,
    completed_at       TEXT,
    UNIQUE (note_id,source_revision_id),
    FOREIGN KEY (note_id,class_id) REFERENCES course_notes(id,class_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS study_materials (
    id              TEXT PRIMARY KEY,
    class_id        TEXT REFERENCES course_classes(id) ON DELETE CASCADE,
    origin_class_id TEXT,
    resource_scope  TEXT NOT NULL DEFAULT 'class_owned'
        CHECK (resource_scope IN ('class_owned', 'personal', 'student_contribution')),
    owner_id        TEXT REFERENCES users(id) ON DELETE CASCADE,
    created_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    publication_state TEXT,
    release_at      TEXT,
    published_at    TEXT,
    feedback_release_policy TEXT NOT NULL DEFAULT 'after_completion'
        CHECK (feedback_release_policy IN ('after_completion','manual','never')),
    assessment_validation_state TEXT NOT NULL DEFAULT 'legacy_unvalidated'
        CHECK (assessment_validation_state IN ('legacy_unvalidated','validated')),
    assessment_validation_version TEXT,
    assessment_engine_version INTEGER NOT NULL DEFAULT 0 CHECK (assessment_engine_version IN (0,1)),
    kind            TEXT NOT NULL CHECK (kind IN ('quiz', 'practice_exam', 'study_guide', 'flashcards')),
    title           TEXT NOT NULL CHECK (trim(title) <> ''),
    status          TEXT NOT NULL DEFAULT 'generating'
        CHECK (status IN ('generating', 'ready', 'failed')),
    scope           TEXT NOT NULL DEFAULT '{"kind":"all"}'
        CHECK (json_valid(scope) AND json_type(scope) = 'object'),
    options         TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(options) AND json_type(options) = 'object'),
    generation_instructions TEXT
        CHECK (generation_instructions IS NULL OR length(generation_instructions) <= 2000),
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
    CHECK ((resource_scope = 'personal' AND class_id IS NULL AND owner_id IS NOT NULL
            AND publication_state IS NULL AND release_at IS NULL)
        OR (resource_scope IN ('class_owned', 'student_contribution') AND class_id IS NOT NULL
            AND owner_id IS NULL AND publication_state IN ('draft', 'scheduled', 'published'))),
    CHECK ((publication_state = 'scheduled' AND release_at IS NOT NULL) OR
        (publication_state <> 'scheduled' AND release_at IS NULL)),
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
    source_class_id TEXT,
    source_note_id  TEXT REFERENCES course_notes(id) ON DELETE SET NULL,
    source_title    TEXT NOT NULL DEFAULT '',
    source_kind     TEXT NOT NULL DEFAULT 'notes',
    position    INTEGER NOT NULL DEFAULT 0 CHECK (position >= 0),
    -- Compatibility aliases for older integrations and stored-source triggers.
    class_id    TEXT,
    note_id     TEXT,
    PRIMARY KEY (material_id, position),
    FOREIGN KEY (material_id) REFERENCES study_materials(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS study_material_attempts (
    id              TEXT PRIMARY KEY,
    material_id     TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    owner_id        TEXT REFERENCES users(id) ON DELETE CASCADE,
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
    snapshot_version INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_version >= 0),
    assessment_type TEXT NOT NULL DEFAULT 'quiz'
        CHECK (assessment_type IN ('quiz','mastery_ladder')),
    grading_policy TEXT NOT NULL DEFAULT 'objective_only'
        CHECK (grading_policy IN ('objective_only','reasoning_optional','reasoning_required')),
    feedback_release_policy TEXT NOT NULL DEFAULT 'after_completion'
        CHECK (feedback_release_policy IN ('after_completion','manual','never')),
    feedback_released_at TEXT,
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

-- A run pins both its safe student projection and the private grading source.
-- Option IDs are fresh per run; option_mapping is never returned to the client.
CREATE TABLE IF NOT EXISTS quiz_run_questions (
    id               TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES quiz_runs(id) ON DELETE CASCADE,
    material_id      TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    bank_item_id     TEXT REFERENCES class_question_bank_items(id) ON DELETE SET NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    family_id        TEXT NOT NULL,
    public_snapshot  TEXT NOT NULL CHECK (json_valid(public_snapshot) AND json_type(public_snapshot)='object'),
    private_snapshot TEXT NOT NULL CHECK (json_valid(private_snapshot) AND json_type(private_snapshot)='object'),
    option_mapping   TEXT NOT NULL CHECK (json_valid(option_mapping) AND json_type(option_mapping)='object'),
    snapshot_sha256  TEXT NOT NULL,
    evidence_eligible INTEGER NOT NULL DEFAULT 0 CHECK (evidence_eligible IN (0,1)),
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (run_id,ordinal),
    UNIQUE (run_id,id)
);
CREATE INDEX IF NOT EXISTS quiz_run_questions_bank_item_idx
    ON quiz_run_questions(bank_item_id,run_id);

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
    answer_status                   TEXT NOT NULL DEFAULT 'unanswered'
        CHECK (answer_status IN ('answered','unanswered')),
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

-- Ladder records contain progression and retry metadata only. Question snapshots,
-- student responses, grading, and evidence remain in the shared quiz tables.
CREATE TABLE IF NOT EXISTS mastery_ladder_sessions (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id        TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    material_id     TEXT NOT NULL UNIQUE REFERENCES study_materials(id) ON DELETE CASCADE,
    run_id          TEXT NOT NULL UNIQUE REFERENCES quiz_runs(id) ON DELETE CASCADE,
    operation_id    TEXT NOT NULL,
    request_digest  TEXT NOT NULL,
    language        TEXT NOT NULL,
    grading_policy  TEXT NOT NULL
        CHECK (grading_policy IN ('objective_only','reasoning_required')),
    status          TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','completed','failed','unavailable')),
    version         INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    completed_at    TEXT,
    UNIQUE (owner_id,operation_id)
);
CREATE INDEX IF NOT EXISTS mastery_ladder_owner_updated_idx
    ON mastery_ladder_sessions(owner_id,updated_at DESC);

CREATE TABLE IF NOT EXISTS mastery_ladder_concepts (
    ladder_id           TEXT NOT NULL REFERENCES mastery_ladder_sessions(id) ON DELETE CASCADE,
    class_id            TEXT NOT NULL,
    concept_id          TEXT NOT NULL,
    concept_label       TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','passed','failed','skipped','unavailable')),
    current_level       INTEGER NOT NULL DEFAULT 1 CHECK (current_level BETWEEN 1 AND 4),
    recovery_state      TEXT NOT NULL DEFAULT 'not_offered'
        CHECK (recovery_state IN ('not_offered','available','used','unavailable')),
    current_question_id TEXT REFERENCES quiz_run_questions(id) ON DELETE SET NULL,
    recovery_question_id TEXT REFERENCES quiz_run_questions(id) ON DELETE SET NULL,
    version             INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (ladder_id,concept_id),
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS mastery_ladder_concepts_state_idx
    ON mastery_ladder_concepts(ladder_id,state,current_level);

CREATE TABLE IF NOT EXISTS mastery_ladder_items (
    ladder_id       TEXT NOT NULL,
    class_id        TEXT NOT NULL,
    concept_id      TEXT NOT NULL,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    role            TEXT NOT NULL CHECK (role IN ('primary','recovery')),
    question_id     TEXT NOT NULL UNIQUE REFERENCES quiz_run_questions(id) ON DELETE CASCADE,
    family_id       TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (ladder_id,concept_id,level,role),
    FOREIGN KEY (ladder_id,concept_id)
        REFERENCES mastery_ladder_concepts(ladder_id,concept_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mastery_ladder_operations (
    id              TEXT PRIMARY KEY,
    ladder_id       TEXT NOT NULL REFERENCES mastery_ladder_sessions(id) ON DELETE CASCADE,
    concept_id      TEXT,
    operation_id    TEXT NOT NULL,
    operation_kind  TEXT NOT NULL CHECK (operation_kind IN ('create','response','reasoning','retry','skip')),
    request_digest  TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','complete','failed')),
    error_code      TEXT,
    result_json     TEXT CHECK (result_json IS NULL OR
                     (json_valid(result_json) AND json_type(result_json)='object')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (ladder_id,operation_id),
    FOREIGN KEY (ladder_id,concept_id)
        REFERENCES mastery_ladder_concepts(ladder_id,concept_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mastery_ladder_rubric_jobs (
    id              TEXT PRIMARY KEY,
    ladder_id       TEXT NOT NULL REFERENCES mastery_ladder_sessions(id) ON DELETE CASCADE,
    attempt_id      TEXT NOT NULL UNIQUE REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    operation_id    TEXT NOT NULL,
    answer_digest   TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','available','failed')),
    result          TEXT CHECK (result IS NULL OR result IN ('passed','failed')),
    feedback        TEXT,
    error_code      TEXT,
    prompt_version  TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    completed_at    TEXT
);
CREATE INDEX IF NOT EXISTS mastery_ladder_rubric_status_idx
    ON mastery_ladder_rubric_jobs(status,updated_at);

-- Owner-private, append-only assessment outcomes and learning projections.
-- Correctness is kept server-side even while feedback release is pending.
CREATE TABLE IF NOT EXISTS assessment_outcomes (
    id              TEXT PRIMARY KEY,
    attempt_id      TEXT NOT NULL UNIQUE REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    run_id          TEXT NOT NULL REFERENCES quiz_runs(id) ON DELETE CASCADE,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    material_id     TEXT NOT NULL REFERENCES study_materials(id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL,
    family_id       TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('correct','incorrect','unanswered','skipped','pending')),
    correctness     REAL CHECK (correctness IS NULL OR correctness BETWEEN 0 AND 1),
    reasoning_status TEXT NOT NULL CHECK (reasoning_status IN
        ('none','sufficient','insufficient','pending','failed','rubric_passed','rubric_failed')),
    grading_policy  TEXT NOT NULL CHECK (grading_policy IN ('objective_only','reasoning_optional','reasoning_required')),
    level           INTEGER CHECK (level IS NULL OR level BETWEEN 1 AND 4),
    quality_weight  REAL NOT NULL DEFAULT 1.0 CHECK (quality_weight >= 0 AND quality_weight <= 1),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK ((status IN ('correct','incorrect') AND correctness IS NOT NULL) OR
           (status IN ('unanswered','skipped','pending') AND correctness IS NULL))
);
CREATE INDEX IF NOT EXISTS assessment_outcomes_owner_created_idx
    ON assessment_outcomes(owner_id,created_at DESC);
CREATE INDEX IF NOT EXISTS assessment_outcomes_run_status_idx
    ON assessment_outcomes(run_id,status);

CREATE TABLE IF NOT EXISTS learning_xp_events (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_key       TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN
        ('valid_response','correct_response','accepted_reasoning','assessment_complete',
         'ladder_complete','verified_improvement')),
    amount          INTEGER NOT NULL CHECK (amount > 0),
    run_id          TEXT REFERENCES quiz_runs(id) ON DELETE CASCADE,
    attempt_id      TEXT REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    release_material_id TEXT REFERENCES study_materials(id) ON DELETE CASCADE,
    algorithm_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (owner_id,event_key)
);
CREATE INDEX IF NOT EXISTS learning_xp_owner_created_idx
    ON learning_xp_events(owner_id,created_at DESC);

CREATE TABLE IF NOT EXISTS learning_preferences (
    owner_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    chosen_goal    TEXT NOT NULL DEFAULT 'balanced'
        CHECK (chosen_goal IN ('balanced','build_foundation','exam_prep','review')),
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS learning_recommendations (
    id                       TEXT PRIMARY KEY,
    owner_id                 TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id                 TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    concept_id               TEXT NOT NULL,
    recommendation_type      TEXT NOT NULL CHECK (recommendation_type IN ('practice','review','diagnostic')),
    chosen_goal              TEXT NOT NULL CHECK (chosen_goal IN ('balanced','build_foundation','exam_prep','review')),
    label                    TEXT NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 240),
    reason                   TEXT NOT NULL CHECK (length(trim(reason)) BETWEEN 1 AND 1000),
    resource_type            TEXT,
    resource_id              TEXT,
    resource_title           TEXT,
    supporting_evidence_ids  TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(supporting_evidence_ids) AND json_type(supporting_evidence_ids)='array'),
    evidence_cursor          TEXT NOT NULL,
    state                    TEXT NOT NULL DEFAULT 'suggested'
        CHECK (state IN ('suggested','pending','active','completed','dismissed')),
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id) ON DELETE CASCADE,
    UNIQUE (owner_id,class_id,concept_id,recommendation_type,chosen_goal,evidence_cursor)
);
CREATE INDEX IF NOT EXISTS learning_recommendations_owner_state_idx
    ON learning_recommendations(owner_id,state,created_at DESC);

CREATE TABLE IF NOT EXISTS learning_recommendation_events (
    id               TEXT PRIMARY KEY,
    recommendation_id TEXT NOT NULL REFERENCES learning_recommendations(id) ON DELETE CASCADE,
    owner_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL CHECK (event_type IN
        ('shown','engaged','completed','dismissed','followed_by_performance')),
    related_attempt_id TEXT REFERENCES quiz_question_attempts(id) ON DELETE SET NULL,
    event_date       TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (recommendation_id,event_type,event_date,related_attempt_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS learning_recommendation_event_no_attempt_idx
    ON learning_recommendation_events(recommendation_id,event_type,event_date)
    WHERE related_attempt_id IS NULL;

-- Aggregate-safe exposure and outcome telemetry for internal calibration. No
-- raw reasoning, transcript, or direct user identifier is stored here.
CREATE TABLE IF NOT EXISTS question_calibration_events (
    id                    TEXT PRIMARY KEY,
    class_id              TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    bank_item_id          TEXT NOT NULL,
    run_id                TEXT NOT NULL REFERENCES quiz_runs(id) ON DELETE CASCADE,
    question_id           TEXT NOT NULL,
    attempt_id            TEXT REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    concept_id            TEXT NOT NULL,
    family_id             TEXT NOT NULL,
    level                 INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    event_type            TEXT NOT NULL CHECK (event_type IN ('exposure','response')),
    ability_estimate      REAL CHECK (ability_estimate IS NULL OR ability_estimate BETWEEN 0 AND 1),
    correctness           REAL CHECK (correctness IS NULL OR correctness BETWEEN 0 AND 1),
    reasoning_result      TEXT CHECK (reasoning_result IS NULL OR reasoning_result IN
        ('none','sufficient','insufficient','rubric_passed','rubric_failed')),
    repeated_exposure     INTEGER NOT NULL DEFAULT 0 CHECK (repeated_exposure IN (0,1)),
    source_revision_digest TEXT NOT NULL,
    generation_version    TEXT NOT NULL,
    review_flags          TEXT NOT NULL DEFAULT '[]'
        CHECK (json_valid(review_flags) AND json_type(review_flags)='array'),
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (bank_item_id,class_id) REFERENCES class_question_bank_items(id,class_id)
        ON DELETE CASCADE,
    FOREIGN KEY (run_id,question_id) REFERENCES quiz_run_questions(run_id,id) ON DELETE CASCADE,
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id) ON DELETE CASCADE,
    UNIQUE (run_id,question_id,event_type),
    UNIQUE (attempt_id,event_type)
);
CREATE INDEX IF NOT EXISTS question_calibration_item_type_idx
    ON question_calibration_events(bank_item_id,event_type,created_at);
CREATE INDEX IF NOT EXISTS question_calibration_class_concept_idx
    ON question_calibration_events(class_id,concept_id,level,event_type);

CREATE TABLE IF NOT EXISTS question_calibration_flags (
    id                 TEXT PRIMARY KEY,
    class_id           TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    bank_item_id       TEXT NOT NULL,
    flag_code          TEXT NOT NULL CHECK (flag_code IN ('ability_adjusted_mismatch')),
    response_count     INTEGER NOT NULL CHECK (response_count >= 30),
    adjusted_residual  REAL NOT NULL CHECK (adjusted_residual BETWEEN -1 AND 1),
    state              TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','reviewed')),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (bank_item_id,class_id) REFERENCES class_question_bank_items(id,class_id)
        ON DELETE CASCADE,
    UNIQUE (bank_item_id,flag_code)
);
CREATE INDEX IF NOT EXISTS question_calibration_flags_class_state_idx
    ON question_calibration_flags(class_id,state,created_at DESC);

CREATE TABLE IF NOT EXISTS calibration_graph_flags (
    id                 TEXT PRIMARY KEY,
    class_id           TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    edge_id            TEXT NOT NULL,
    flag_code          TEXT NOT NULL CHECK (flag_code IN ('asymmetric_performance')),
    observed_learners  INTEGER NOT NULL CHECK (observed_learners >= 10),
    prerequisite_rate REAL NOT NULL CHECK (prerequisite_rate BETWEEN 0 AND 1),
    downstream_rate   REAL NOT NULL CHECK (downstream_rate BETWEEN 0 AND 1),
    state              TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','reviewed')),
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (edge_id,class_id) REFERENCES class_concept_edges(id,class_id) ON DELETE CASCADE,
    UNIQUE (edge_id,flag_code)
);

CREATE TABLE IF NOT EXISTS question_bank_replenishment_jobs (
    id                TEXT PRIMARY KEY,
    class_id          TEXT NOT NULL REFERENCES course_classes(id) ON DELETE CASCADE,
    concept_id        TEXT NOT NULL,
    level             INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    language          TEXT NOT NULL CHECK (length(language) BETWEEN 2 AND 35),
    source_digest     TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('queued','running','complete','failed','superseded')),
    attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts     INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 5),
    available_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    error_code        TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at        TEXT,
    completed_at      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    FOREIGN KEY (concept_id,class_id) REFERENCES class_concepts(id,class_id) ON DELETE CASCADE,
    UNIQUE (class_id,concept_id,level,language,source_digest)
);
CREATE INDEX IF NOT EXISTS question_bank_replenishment_status_idx
    ON question_bank_replenishment_jobs(status,available_at,created_at);

CREATE TABLE IF NOT EXISTS learning_calibration_jobs (
    id                TEXT PRIMARY KEY,
    schedule_key      TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL CHECK (status IN ('queued','running','complete','failed')),
    attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts      INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 5),
    available_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    error_code        TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    started_at        TEXT,
    completed_at      TEXT,
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS learning_calibration_jobs_status_idx
    ON learning_calibration_jobs(status,available_at,created_at);

CREATE TABLE IF NOT EXISTS mastery_evidence (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id        TEXT,
    concept_id      TEXT NOT NULL,
    concept_label   TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES quiz_runs(id) ON DELETE CASCADE,
    attempt_id      TEXT NOT NULL REFERENCES quiz_question_attempts(id) ON DELETE CASCADE,
    question_id     TEXT NOT NULL,
    family_id       TEXT NOT NULL,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    correctness     REAL NOT NULL CHECK (correctness BETWEEN 0 AND 1),
    reasoning_result TEXT NOT NULL CHECK (reasoning_result IN
        ('none','sufficient','insufficient','rubric_passed','rubric_failed')),
    grading_policy  TEXT NOT NULL CHECK (grading_policy IN ('objective_only','reasoning_optional','reasoning_required')),
    quality_weight  REAL NOT NULL CHECK (quality_weight > 0 AND quality_weight <= 1),
    algorithm_version TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (owner_id,attempt_id,concept_id)
);
CREATE INDEX IF NOT EXISTS mastery_evidence_owner_concept_created_idx
    ON mastery_evidence(owner_id,concept_id,created_at,id);

CREATE TABLE IF NOT EXISTS mastery_projections (
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL,
    concept_label   TEXT NOT NULL,
    class_id        TEXT,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    alpha           REAL NOT NULL CHECK (alpha > 0),
    beta            REAL NOT NULL CHECK (beta > 0),
    mean            REAL NOT NULL CHECK (mean BETWEEN 0 AND 1),
    lower_90        REAL NOT NULL CHECK (lower_90 BETWEEN 0 AND 1),
    upper_90        REAL NOT NULL CHECK (upper_90 BETWEEN 0 AND 1),
    probability_threshold REAL NOT NULL CHECK (probability_threshold BETWEEN 0 AND 1),
    evidence_count  INTEGER NOT NULL CHECK (evidence_count >= 0),
    historical_peak INTEGER CHECK (historical_peak IS NULL OR historical_peak BETWEEN 1 AND 4),
    current_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (current_confirmed IN (0,1)),
    last_assessed_at TEXT,
    review_due      INTEGER NOT NULL DEFAULT 0 CHECK (review_due IN (0,1)),
    algorithm_version TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (owner_id,concept_id,level)
);

CREATE TABLE IF NOT EXISTS mastery_snapshots (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    concept_id      TEXT NOT NULL,
    concept_label   TEXT NOT NULL,
    class_id        TEXT,
    level           INTEGER NOT NULL CHECK (level BETWEEN 1 AND 4),
    mean            REAL NOT NULL CHECK (mean BETWEEN 0 AND 1),
    lower_90        REAL NOT NULL CHECK (lower_90 BETWEEN 0 AND 1),
    upper_90        REAL NOT NULL CHECK (upper_90 BETWEEN 0 AND 1),
    evidence_count  INTEGER NOT NULL CHECK (evidence_count >= 0),
    current_level   INTEGER CHECK (current_level IS NULL OR current_level BETWEEN 1 AND 4),
    trigger_evidence_id TEXT NOT NULL REFERENCES mastery_evidence(id) ON DELETE CASCADE,
    algorithm_version TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    UNIQUE (owner_id,concept_id,level,trigger_evidence_id)
);
CREATE INDEX IF NOT EXISTS mastery_snapshots_owner_concept_asof_idx
    ON mastery_snapshots(owner_id,concept_id,as_of);

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
    owner_id                TEXT REFERENCES users(id) ON DELETE CASCADE,
    answer_key_release_state TEXT NOT NULL DEFAULT 'draft'
        CHECK (answer_key_release_state IN ('draft', 'scheduled', 'published')),
    answer_key_release_at   TEXT,
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
    ),
    CHECK ((answer_key_release_state = 'scheduled' AND answer_key_release_at IS NOT NULL) OR
        (answer_key_release_state <> 'scheduled' AND answer_key_release_at IS NULL))
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
    is_admin      INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
    avatar        TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS signup_requests (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL COLLATE NOCASE,
    email         TEXT COLLATE NOCASE,
    display_name  TEXT,
    password_hash TEXT,
    status        TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    reviewed_by   TEXT REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at   TEXT,
    CHECK (
        (status = 'pending' AND password_hash IS NOT NULL) OR
        (status <> 'pending' AND password_hash IS NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS signup_requests_pending_username_idx
    ON signup_requests (username COLLATE NOCASE) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS signup_requests_pending_email_idx
    ON signup_requests (email COLLATE NOCASE)
    WHERE status = 'pending' AND email IS NOT NULL;
CREATE INDEX IF NOT EXISTS signup_requests_pending_created_idx
    ON signup_requests (status, created_at);

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
