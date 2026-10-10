-- Application profile data belongs in public; authentication credentials,
-- email, and login sessions remain managed by Supabase Auth.

CREATE TABLE IF NOT EXISTS public.profiles (
    id                  uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name        text,
    username            text,
    education_level     text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT profiles_display_name_length CHECK (
        display_name IS NULL OR (btrim(display_name) <> '' AND char_length(display_name) <= 120)
    ),
    CONSTRAINT profiles_username_format CHECK (
        username IS NULL OR username ~ '^[A-Za-z0-9_]{3,32}$'
    ),
    CONSTRAINT profiles_education_level_length CHECK (
        education_level IS NULL OR (
            btrim(education_level) <> '' AND char_length(education_level) <= 40
        )
    )
);

COMMENT ON TABLE public.profiles IS
    'Private student profile fields linked one-to-one to a Supabase Auth user.';
COMMENT ON COLUMN public.profiles.created_at IS
    'The Supabase Auth account creation timestamp; email remains in auth.users.';
COMMENT ON COLUMN public.profiles.username IS
    'Optional unique profile handle; authentication continues to use Supabase Auth.';

CREATE UNIQUE INDEX IF NOT EXISTS profiles_username_lower_unique_idx
    ON public.profiles (lower(username))
    WHERE username IS NOT NULL;

DROP TRIGGER IF EXISTS profiles_set_updated_at ON public.profiles;
CREATE TRIGGER profiles_set_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE public.profiles FROM anon, authenticated;
GRANT SELECT ON TABLE public.profiles TO authenticated;
GRANT UPDATE (display_name, username, education_level)
    ON TABLE public.profiles TO authenticated;
GRANT ALL ON TABLE public.profiles TO service_role;

DROP POLICY IF EXISTS profiles_select_self ON public.profiles;
CREATE POLICY profiles_select_self
    ON public.profiles
    FOR SELECT
    TO authenticated
    USING ((SELECT auth.uid()) = id);

DROP POLICY IF EXISTS profiles_update_self ON public.profiles;
CREATE POLICY profiles_update_self
    ON public.profiles
    FOR UPDATE
    TO authenticated
    USING ((SELECT auth.uid()) = id)
    WITH CHECK ((SELECT auth.uid()) = id);

-- Supabase Auth creates the profile in the same transaction as signup. Keep
-- this function locked down and schema-qualify all objects in its empty path.
CREATE OR REPLACE FUNCTION public.create_profile_for_auth_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.profiles (id, display_name, created_at, updated_at)
    VALUES (
        NEW.id,
        left(
            COALESCE(
                NULLIF(btrim(NEW.raw_user_meta_data ->> 'display_name'), ''),
                NULLIF(btrim(NEW.raw_user_meta_data ->> 'full_name'), ''),
                NULLIF(btrim(NEW.raw_user_meta_data ->> 'name'), '')
            ),
            120
        ),
        NEW.created_at,
        NEW.created_at
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.create_profile_for_auth_user() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS on_auth_user_created_make_profile ON auth.users;
CREATE TRIGGER on_auth_user_created_make_profile
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.create_profile_for_auth_user();

-- Backfill accounts that existed before this migration was applied.
INSERT INTO public.profiles (id, display_name, created_at, updated_at)
SELECT
    users.id,
    left(
        COALESCE(
            NULLIF(btrim(users.raw_user_meta_data ->> 'display_name'), ''),
            NULLIF(btrim(users.raw_user_meta_data ->> 'full_name'), ''),
            NULLIF(btrim(users.raw_user_meta_data ->> 'name'), '')
        ),
        120
    ),
    users.created_at,
    users.created_at
FROM auth.users AS users
ON CONFLICT (id) DO NOTHING;
