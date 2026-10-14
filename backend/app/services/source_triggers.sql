-- Install after column migrations, including on databases created by older builds.
DROP TRIGGER IF EXISTS course_notes_delete_derived_materials;
DROP TRIGGER IF EXISTS course_notes_mark_materials_changed;

CREATE TRIGGER IF NOT EXISTS course_notes_mark_materials_changed
AFTER UPDATE OF extracted_text, kind ON course_notes
WHEN (OLD.status = 'ready' OR NEW.status <> 'ready')
    AND (OLD.extracted_text IS NOT NEW.extracted_text OR OLD.kind IS NOT NEW.kind)
BEGIN
    UPDATE study_materials SET sources_outdated = 1
    WHERE id IN (SELECT material_id FROM study_material_sources WHERE COALESCE(source_note_id,note_id) = NEW.id);
    -- Direct source edits also invalidate a job, without coupling it to title changes.
    UPDATE course_notes SET processing_revision = processing_revision + 1
    WHERE id = NEW.id AND processing_revision = OLD.processing_revision;
END;

CREATE TRIGGER IF NOT EXISTS course_notes_mark_materials_missing
BEFORE DELETE ON course_notes
BEGIN
    UPDATE study_materials SET sources_missing = 1
    WHERE id IN (SELECT material_id FROM study_material_sources WHERE COALESCE(source_note_id,note_id) = OLD.id);
END;
