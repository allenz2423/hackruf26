-- Student reports for potentially incorrect generated study content.
-- Reports are private application data and are only read by the backend.

SET search_path = public;

CREATE TABLE IF NOT EXISTS public.study_material_flags (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    material_id     uuid NOT NULL,
    class_id        uuid NOT NULL,
    reporter_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    question_id     text NOT NULL,
    reason          text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT study_material_flags_question_not_blank CHECK (btrim(question_id) <> ''),
    CONSTRAINT study_material_flags_reason_length CHECK (reason IS NULL OR char_length(reason) <= 1000),
    CONSTRAINT study_material_flags_unique UNIQUE (material_id, reporter_id, question_id),
    CONSTRAINT study_material_flags_material_class_fk
        FOREIGN KEY (material_id, class_id)
        REFERENCES public.study_materials(id, class_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS study_material_flags_reporter_created_idx
    ON public.study_material_flags (reporter_id, created_at DESC);

ALTER TABLE public.study_material_flags ENABLE ROW LEVEL SECURITY;
