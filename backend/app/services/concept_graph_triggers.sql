-- Queue exactly the current ready source revision. The job unique key and
-- INSERT OR IGNORE make readiness/head order and duplicate callbacks safe.
CREATE TRIGGER IF NOT EXISTS concept_extraction_enqueue_head_insert
AFTER INSERT ON source_index_heads
BEGIN
    INSERT OR IGNORE INTO class_concept_extraction_jobs
        (id,class_id,note_id,source_revision_id,requested_by,status)
    SELECT lower(hex(randomblob(16))),n.class_id,n.id,NEW.revision_id,c.owner_id,'queued'
    FROM course_notes n JOIN course_classes c ON c.id=n.class_id
    JOIN source_revisions r ON r.id=NEW.revision_id
    WHERE n.id=NEW.note_id AND n.status='ready' AND n.source_index_status='ready'
      AND r.processing_revision=n.processing_revision
      AND (n.resource_scope<>'student_contribution' OR n.publication_state='published'
           OR (n.publication_state='scheduled' AND julianday(n.release_at)<=julianday('now')))
      AND NOT EXISTS (SELECT 1 FROM uploaded_assessments a WHERE a.note_id=n.id);
END;

CREATE TRIGGER IF NOT EXISTS concept_extraction_enqueue_head_update
AFTER UPDATE OF revision_id ON source_index_heads
BEGIN
    INSERT OR IGNORE INTO class_concept_extraction_jobs
        (id,class_id,note_id,source_revision_id,requested_by,status)
    SELECT lower(hex(randomblob(16))),n.class_id,n.id,NEW.revision_id,c.owner_id,'queued'
    FROM course_notes n JOIN course_classes c ON c.id=n.class_id
    JOIN source_revisions r ON r.id=NEW.revision_id
    WHERE n.id=NEW.note_id AND n.status='ready' AND n.source_index_status='ready'
      AND r.processing_revision=n.processing_revision
      AND (n.resource_scope<>'student_contribution' OR n.publication_state='published'
           OR (n.publication_state='scheduled' AND julianday(n.release_at)<=julianday('now')))
      AND NOT EXISTS (SELECT 1 FROM uploaded_assessments a WHERE a.note_id=n.id);
END;

CREATE TRIGGER IF NOT EXISTS concept_extraction_enqueue_note_ready
AFTER UPDATE OF status,source_index_status ON course_notes
WHEN NEW.status='ready' AND NEW.source_index_status='ready'
BEGIN
    INSERT OR IGNORE INTO class_concept_extraction_jobs
        (id,class_id,note_id,source_revision_id,requested_by,status)
    SELECT lower(hex(randomblob(16))),NEW.class_id,NEW.id,h.revision_id,c.owner_id,'queued'
    FROM source_index_heads h
    JOIN source_revisions r ON r.id=h.revision_id
    JOIN course_classes c ON c.id=NEW.class_id
    WHERE h.note_id=NEW.id AND r.processing_revision=NEW.processing_revision
      AND (NEW.resource_scope<>'student_contribution' OR NEW.publication_state='published'
           OR (NEW.publication_state='scheduled' AND julianday(NEW.release_at)<=julianday('now')))
      AND NOT EXISTS (SELECT 1 FROM uploaded_assessments a WHERE a.note_id=NEW.id);
END;
