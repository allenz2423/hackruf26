-- Store parsed uploads of existing tests separately from generated study
-- materials. The original upload and extracted text remain in course_notes.
-- Generated quizzes and practice exams continue to live in study_materials;
-- their source links can point to the course_notes row for an uploaded test.

CREATE TABLE IF NOT EXISTS public.uploaded_assessments (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id            uuid NOT NULL,
    note_id             uuid NOT NULL UNIQUE,
    processing_status   text NOT NULL DEFAULT 'processing',
    has_student_responses boolean NOT NULL DEFAULT false,
    questions           jsonb NOT NULL DEFAULT '[]'::jsonb,
    grading_mode        text NOT NULL DEFAULT 'not_graded',
    score_percent       numeric(5, 2),
    grading_result      jsonb,
    grade_confidence    numeric(4, 3),
    failure_code        text,
    processed_at        timestamptz,
    graded_at           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uploaded_assessments_id_class_unique UNIQUE (id, class_id),
    CONSTRAINT uploaded_assessments_note_class_fk
        FOREIGN KEY (note_id, class_id)
        REFERENCES public.course_notes(id, class_id) ON DELETE CASCADE,
    CONSTRAINT uploaded_assessments_status_check
        CHECK (processing_status IN ('processing', 'ready', 'needs_review', 'failed')),
    CONSTRAINT uploaded_assessments_grading_mode_check
        CHECK (grading_mode IN ('not_graded', 'answer_key', 'class_notes_estimated', 'needs_review')),
    CONSTRAINT uploaded_assessments_questions_is_array
        CHECK (jsonb_typeof(questions) = 'array'),
    CONSTRAINT uploaded_assessments_result_is_object
        CHECK (grading_result IS NULL OR jsonb_typeof(grading_result) = 'object'),
    CONSTRAINT uploaded_assessments_score_range
        CHECK (score_percent IS NULL OR score_percent BETWEEN 0 AND 100),
    CONSTRAINT uploaded_assessments_confidence_range
        CHECK (grade_confidence IS NULL OR grade_confidence BETWEEN 0 AND 1),
    CONSTRAINT uploaded_assessments_failure_only_on_failure
        CHECK (processing_status = 'failed' OR failure_code IS NULL),
    CONSTRAINT uploaded_assessments_grade_state_consistent CHECK (
        (
            grading_mode IN ('answer_key', 'class_notes_estimated')
            AND has_student_responses
            AND score_percent IS NOT NULL
            AND graded_at IS NOT NULL
        )
        OR
        (
            grading_mode IN ('not_graded', 'needs_review')
            AND score_percent IS NULL
            AND graded_at IS NULL
        )
    ),
    CONSTRAINT uploaded_assessments_processing_state_consistent CHECK (
        (
            processing_status IN ('processing', 'failed')
            AND score_percent IS NULL
            AND graded_at IS NULL
        )
        OR
        (
            processing_status = 'ready'
            AND (
                (
                    grading_mode = 'not_graded'
                    AND NOT has_student_responses
                    AND score_percent IS NULL
                    AND graded_at IS NULL
                )
                OR
                (
                    grading_mode IN ('answer_key', 'class_notes_estimated')
                    AND has_student_responses
                    AND score_percent IS NOT NULL
                    AND graded_at IS NOT NULL
                )
            )
        )
        OR
        (
            processing_status = 'needs_review'
            AND grading_mode = 'needs_review'
            AND score_percent IS NULL
            AND graded_at IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS uploaded_assessments_class_created_idx
    ON public.uploaded_assessments (class_id, created_at DESC);
CREATE INDEX IF NOT EXISTS uploaded_assessments_class_status_idx
    ON public.uploaded_assessments (class_id, processing_status);

DROP TRIGGER IF EXISTS uploaded_assessments_set_updated_at ON public.uploaded_assessments;
CREATE TRIGGER uploaded_assessments_set_updated_at
    BEFORE UPDATE ON public.uploaded_assessments
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Keep assessment contents and answer material behind the server API. Browser
-- Data API access remains closed until owner-scoped policies are deliberately
-- designed and enabled.
ALTER TABLE public.uploaded_assessments ENABLE ROW LEVEL SECURITY;
