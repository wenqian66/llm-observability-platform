-- ============================================
-- PostgreSQL Database Schema
-- User Management, Apps & API Keys
-- LLM Gateway v2.3.1
-- ============================================
-- Run once on a fresh database:
--   psql -h <host> -U <user> -d <database> -f postgres_init.sql
-- Safe to re-run — all statements use IF NOT EXISTS / OR REPLACE.
--
-- API key storage model:
--   Plaintext dsrs-... keys are returned to the caller ONCE and never stored.
--   Only bcrypt(plaintext) is persisted in api_key_hash.
--   Validation uses bcrypt.verify(submitted, stored_hash) in the application layer.
--   No UNIQUE constraint on api_key_hash — bcrypt hashes cannot be used for
--   indexed lookup and uniqueness is guaranteed by the key entropy itself.
-- ============================================


-- ============================================
-- 1. USERS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS public.users (
    id            SERIAL PRIMARY KEY,
    first_name    VARCHAR(100)  NOT NULL,
    last_name     VARCHAR(100)  NOT NULL,
    net_id        VARCHAR(255)  NOT NULL UNIQUE,
    api_key_hash  VARCHAR(255),            -- bcrypt hash only — plaintext never stored
    is_active     BOOLEAN       DEFAULT TRUE,
    is_admin      BOOLEAN       DEFAULT FALSE,
    created_at    TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_net_id    ON public.users(net_id);
CREATE INDEX IF NOT EXISTS idx_users_is_active ON public.users(is_active);
CREATE INDEX IF NOT EXISTS idx_users_is_admin  ON public.users(is_admin);


-- ============================================
-- 2. APPS TABLE
-- ============================================

CREATE TABLE IF NOT EXISTS public.apps (
    id                      SERIAL PRIMARY KEY,
    app_name                VARCHAR(255)  NOT NULL UNIQUE,
    api_key_hash            VARCHAR(255),            -- bcrypt hash only — plaintext never stored
    description             TEXT,
    created_by              VARCHAR(255),            -- net_id of the admin who created this app
    is_active               BOOLEAN       DEFAULT TRUE,
    semantic_cache_enabled  BOOLEAN       DEFAULT TRUE,
    created_at              TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_apps_app_name       ON public.apps(app_name);
CREATE INDEX IF NOT EXISTS idx_apps_is_active      ON public.apps(is_active);
CREATE INDEX IF NOT EXISTS idx_apps_semantic_cache ON public.apps(semantic_cache_enabled);


-- ============================================
-- 3. TRIGGER FUNCTION (Auto-update timestamps)
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_users_updated_at ON public.users;
CREATE TRIGGER update_users_updated_at
    BEFORE UPDATE ON public.users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_apps_updated_at ON public.apps;
CREATE TRIGGER update_apps_updated_at
    BEFORE UPDATE ON public.apps
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


-- ============================================
-- 4. HELPER VIEWS
--
-- api_key_hash is intentionally excluded from all views.
-- has_key boolean is the only signal exposed — hashes never leave the DB.
-- ============================================

-- Users with no key yet (pending self-generate or admin bootstrap)
CREATE OR REPLACE VIEW users_without_keys AS
SELECT id, net_id, first_name, last_name, is_admin, created_at
FROM public.users
WHERE is_active = TRUE AND api_key_hash IS NULL;

-- Apps with no key yet (pending admin generate)
CREATE OR REPLACE VIEW apps_without_keys AS
SELECT id, app_name, description, semantic_cache_enabled, created_at
FROM public.apps
WHERE is_active = TRUE AND api_key_hash IS NULL;

-- All active users with a key set
CREATE OR REPLACE VIEW active_user_keys AS
SELECT
    id,
    net_id,
    first_name,
    last_name,
    is_admin,
    TRUE  AS has_key,
    created_at,
    updated_at
FROM public.users
WHERE is_active = TRUE AND api_key_hash IS NOT NULL
ORDER BY created_at DESC;

-- All active apps with a key set
CREATE OR REPLACE VIEW active_app_keys AS
SELECT
    id,
    app_name,
    description,
    semantic_cache_enabled,
    TRUE  AS has_key,
    created_at,
    updated_at
FROM public.apps
WHERE is_active = TRUE AND api_key_hash IS NOT NULL
ORDER BY created_at DESC;

-- Combined view across users and apps
CREATE OR REPLACE VIEW all_active_keys AS
SELECT
    'user'        AS entity_type,
    net_id        AS entity_name,
    CONCAT(first_name, ' ', last_name) AS display_name,
    is_admin,
    NULL::BOOLEAN AS semantic_cache_enabled,
    TRUE          AS has_key,
    created_at,
    updated_at
FROM public.users
WHERE is_active = TRUE AND api_key_hash IS NOT NULL

UNION ALL

SELECT
    'app'         AS entity_type,
    app_name      AS entity_name,
    app_name      AS display_name,
    FALSE         AS is_admin,
    semantic_cache_enabled,
    TRUE          AS has_key,
    created_at,
    updated_at
FROM public.apps
WHERE is_active = TRUE AND api_key_hash IS NOT NULL

ORDER BY created_at DESC;


-- ============================================
-- 5. SAMPLE DATA (for testing only)
-- Skip in production — create users via /admin/users/create
-- and have each person generate their own key.
-- ============================================

INSERT INTO public.users (first_name, last_name, net_id, is_active, is_admin)
VALUES
    ('Matias',    'Kind',       'mcarras2', TRUE, TRUE),
    ('Abhinay',   'Yarlagadda', 'abhinay4', TRUE, TRUE),
    ('Farid',     'Saud',       'gfs3',     TRUE, TRUE),
    ('Abhilasha', 'Mathne',     'amathne2', TRUE, FALSE),
    ('Divya',     'Gupta',      'divya8',   TRUE, FALSE)
ON CONFLICT (net_id) DO NOTHING;

INSERT INTO public.apps (app_name, description, created_by, is_active, semantic_cache_enabled)
VALUES
    ('Atlas',          'Natural language query engine for DSRS data',      'abhinay4', TRUE, TRUE),
    ('Blockchain Hub', 'DSRS project management system on Polygon network', 'abhinay4', TRUE, TRUE),
    ('Echo Log',       'LLM Gateway observability dashboard',               'abhinay4', TRUE, TRUE)
ON CONFLICT (app_name) DO NOTHING;


-- ============================================
-- 6. COMMENTS
-- ============================================

COMMENT ON TABLE  public.users                        IS 'User management — one row per person';
COMMENT ON TABLE  public.apps                         IS 'Registered applications that consume the LLM gateway';
COMMENT ON COLUMN public.users.api_key_hash           IS 'bcrypt hash of the dsrs- prefixed key. NULL until user self-generates (or admin bootstraps for admins). Plaintext is never stored.';
COMMENT ON COLUMN public.apps.api_key_hash            IS 'bcrypt hash of the dsrs- prefixed key. NULL until admin generates via /admin/keys/app/generate. Plaintext is never stored.';
COMMENT ON COLUMN public.apps.semantic_cache_enabled  IS 'When TRUE, requests from this app are eligible for semantic cache hits';
COMMENT ON COLUMN public.apps.created_by              IS 'net_id of the admin who registered this app';