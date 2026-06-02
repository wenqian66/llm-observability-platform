"""
database/postgres.py
PostgreSQL database client with API key management

API key storage model:
  - The plaintext dsrs-... key is returned to the caller ONCE and never stored
  - The database stores only bcrypt(plaintext) in the api_key_hash column
  - Validation uses bcrypt.verify(submitted, stored_hash) via the auth cache

Connection pool resilience:
  max_inactive_connection_lifetime=300 — asyncpg proactively recycles idle
    connections every 5 minutes, before the server closes them server-side.
    Prevents CancelledError / TimeoutError on SSL handshake after long idle
    periods (observed after ~hours of low traffic).
  command_timeout=60 — any query hanging beyond 60s raises a clean exception
    instead of blocking indefinitely.
"""

from typing import Any, Dict, Optional

import asyncpg

from utils.logger import logger


class PostgresDB:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(
        self,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        min_size: int = 2,
        max_size: int = 10,
    ):
        """Connect to PostgreSQL and initialize schema."""
        if self.pool is not None:
            return
        try:
            self.pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                min_size=min_size,
                max_size=max_size,
                max_inactive_connection_lifetime=300,
            )
            logger.info(f"✅ PostgreSQL connected: {host}:{port}/{database}")
            await self._initialize_schema()
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection failed: {e}")
            raise

    async def close(self):
        """Close PostgreSQL connection pool."""
        if self.pool:
            await self.pool.close()
            logger.info("✅ PostgreSQL connection closed")

    async def _initialize_schema(self):
        """
        Initialize database schema under an advisory lock so concurrent
        workers don't race on CREATE OR REPLACE FUNCTION / DROP TRIGGER.
        pg_advisory_lock(1) blocks until the lock is free; the second
        worker through runs the same IF NOT EXISTS / OR REPLACE DDL
        harmlessly — but now sequentially, avoiding "tuple concurrently
        updated" on pg_proc/pg_trigger catalog rows.
        """
        async with self.pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock(1)")
            try:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS public.users (
                        id            SERIAL PRIMARY KEY,
                        first_name    VARCHAR(100)  NOT NULL,
                        last_name     VARCHAR(100)  NOT NULL,
                        net_id        VARCHAR(255)  NOT NULL UNIQUE,
                        api_key_hash  VARCHAR(255),
                        is_active     BOOLEAN       DEFAULT TRUE,
                        is_admin      BOOLEAN       DEFAULT FALSE,
                        created_at    TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP,
                        updated_at    TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_net_id
                    ON public.users(net_id);
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_is_active
                    ON public.users(is_active);
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_users_is_admin
                    ON public.users(is_admin);
                """)

                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS public.apps (
                        id                      SERIAL PRIMARY KEY,
                        app_name                VARCHAR(255)  NOT NULL UNIQUE,
                        api_key_hash            VARCHAR(255),
                        description             TEXT,
                        created_by              VARCHAR(255),
                        is_active               BOOLEAN       DEFAULT TRUE,
                        semantic_cache_enabled  BOOLEAN       DEFAULT TRUE,
                        created_at              TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP,
                        updated_at              TIMESTAMPTZ   DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_apps_app_name
                    ON public.apps(app_name);
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_apps_is_active
                    ON public.apps(is_active);
                """)
                await conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_apps_semantic_cache
                    ON public.apps(semantic_cache_enabled);
                """)

                await conn.execute("""
                    CREATE OR REPLACE FUNCTION update_updated_at_column()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        NEW.updated_at = CURRENT_TIMESTAMP;
                        RETURN NEW;
                    END;
                    $$ LANGUAGE plpgsql;
                """)

                await conn.execute("""
                    DROP TRIGGER IF EXISTS update_users_updated_at ON public.users;
                """)
                await conn.execute("""
                    CREATE TRIGGER update_users_updated_at
                        BEFORE UPDATE ON public.users
                        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
                """)

                await conn.execute("""
                    DROP TRIGGER IF EXISTS update_apps_updated_at ON public.apps;
                """)
                await conn.execute("""
                    CREATE TRIGGER update_apps_updated_at
                        BEFORE UPDATE ON public.apps
                        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
                """)

                logger.info("✅ PostgreSQL schema initialized")
            finally:
                await conn.execute("SELECT pg_advisory_unlock(1)")

    # ============================================
    # API Key Validation
    # ============================================

    async def validate_api_key(self, api_key: str) -> Optional[Dict[str, Any]]:
        """
        Validate a plaintext API key against stored bcrypt hashes.

        Checks users first, then apps. The table scan is acceptable because
        both tables are small (tens to low hundreds of rows) — bcrypt doesn't
        support indexed lookup by design.

        NOTE: This is the slow path (~100-200ms bcrypt cost per row checked).
        The auth cache in utils/auth_cache.py wraps this so it is only called
        on cache misses (first request per 5-minute TTL window).
        """
        from utils.key_generator import verify_api_key

        async with self.pool.acquire() as conn:
            user = await self._find_user_by_key(conn, api_key, verify_api_key)
            if user is not None:
                return user

            app = await self._find_app_by_key(conn, api_key, verify_api_key)
            if app is not None:
                return app

        logger.warning(f"⚠️  Invalid API key attempted: {api_key[:8]}...")
        return None

    async def _find_user_by_key(self, conn, api_key: str, verify_fn) -> Optional[Dict]:
        """Scan active users with a hash set and bcrypt-verify the submitted key."""
        rows = await conn.fetch(
            """
            SELECT net_id, first_name, last_name, api_key_hash, is_admin
            FROM users
            WHERE api_key_hash IS NOT NULL AND is_active = TRUE
            """
        )
        for row in rows:
            if verify_fn(api_key, row["api_key_hash"]):
                return {
                    "api_key": api_key,
                    "entity_type": "user",
                    "user_net_id": row["net_id"],
                    "app_name": None,
                    "is_admin": row["is_admin"],
                    "semantic_cache_enabled": False,
                    "display_name": f"{row['first_name']} {row['last_name']}",
                }
        return None

    async def _find_app_by_key(self, conn, api_key: str, verify_fn) -> Optional[Dict]:
        """Scan active apps with a hash set and bcrypt-verify the submitted key."""
        rows = await conn.fetch(
            """
            SELECT app_name, api_key_hash, semantic_cache_enabled
            FROM apps
            WHERE api_key_hash IS NOT NULL AND is_active = TRUE
            """
        )
        for row in rows:
            if verify_fn(api_key, row["api_key_hash"]):
                return {
                    "api_key": api_key,
                    "entity_type": "app",
                    "user_net_id": None,
                    "app_name": row["app_name"],
                    "is_admin": False,
                    "semantic_cache_enabled": row["semantic_cache_enabled"],
                    "display_name": row["app_name"],
                }
        return None

    # ============================================
    # Key Generation — Regular users (self-service, no auth)
    # ============================================

    async def generate_key_for_user(self, net_id: str) -> Optional[str]:
        """
        Generate a bcrypt-hashed API key for a regular (non-admin) user.

        Called from the unauthenticated POST /keys/self-generate endpoint.
        Preconditions (all must hold):
          - User exists and is active
          - User is NOT an admin (admins use generate_key_for_admin)
          - api_key_hash is currently NULL

        Returns plaintext key shown ONCE, or None on any precondition failure.
        """
        from utils.key_generator import generate_api_key, hash_api_key

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, is_admin, api_key_hash FROM users WHERE net_id = $1",
                net_id,
            )

            if not row:
                logger.warning(f"⚠️  [SELF-GENERATE] User not found: {net_id}")
                return None
            if not row["is_active"]:
                logger.warning(f"⚠️  [SELF-GENERATE] User inactive: {net_id}")
                return None
            if row["is_admin"]:
                logger.warning(
                    f"⚠️  [SELF-GENERATE] Admin {net_id} must use /admin/keys/self-bootstrap"
                )
                return None
            if row["api_key_hash"] is not None:
                logger.warning(f"⚠️  [SELF-GENERATE] Key already exists for: {net_id}")
                return None

            plaintext = generate_api_key()
            await conn.execute(
                """
                UPDATE users
                SET api_key_hash = $1, updated_at = CURRENT_TIMESTAMP
                WHERE net_id = $2
                """,
                hash_api_key(plaintext),
                net_id,
            )

        logger.info(f"🔑 [SELF-GENERATE] Key generated for user: {net_id}")
        return plaintext

    # ============================================
    # Key Generation — Admins (bootstrap key required)
    # ============================================

    async def generate_key_for_admin(self, net_id: str) -> Optional[str]:
        """
        Generate a bcrypt-hashed API key for an admin user.

        Called from POST /admin/keys/self-bootstrap (bootstrap key auth).
        Preconditions (all must hold):
          - User exists, is active, and IS an admin
          - api_key_hash is currently NULL
            (another admin must null it before re-bootstrap is possible)

        Returns plaintext key shown ONCE, or None on any precondition failure.
        """
        from utils.key_generator import generate_api_key, hash_api_key

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, is_admin, api_key_hash FROM users WHERE net_id = $1",
                net_id,
            )

            if not row:
                logger.warning(f"⚠️  [BOOTSTRAP] User not found: {net_id}")
                return None
            if not row["is_active"]:
                logger.warning(f"⚠️  [BOOTSTRAP] User inactive: {net_id}")
                return None
            if not row["is_admin"]:
                logger.warning(
                    f"⚠️  [BOOTSTRAP] Non-admin {net_id} must use /keys/self-generate"
                )
                return None
            if row["api_key_hash"] is not None:
                logger.warning(
                    f"⚠️  [BOOTSTRAP] Key already set for admin {net_id} — "
                    "another admin must null it first"
                )
                return None

            plaintext = generate_api_key()
            await conn.execute(
                """
                UPDATE users
                SET api_key_hash = $1, updated_at = CURRENT_TIMESTAMP
                WHERE net_id = $2
                """,
                hash_api_key(plaintext),
                net_id,
            )

        logger.info(f"🔑 [BOOTSTRAP] Key generated for admin: {net_id}")
        return plaintext

    # ============================================
    # Key Generation — Admin issuing a user key
    # ============================================

    async def admin_generate_key_for_user(self, net_id: str) -> Optional[str]:
        """
        Admin-issued key generation for a specific regular (non-admin) user.

        Admins cannot generate keys for other admins — to reset an admin,
        null their key and have them re-bootstrap.

        Overwrites any existing hash (forced regeneration).
        Returns plaintext key shown ONCE, or None on any failure.
        """
        from utils.key_generator import generate_api_key, hash_api_key

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active, is_admin FROM users WHERE net_id = $1",
                net_id,
            )

            if not row:
                logger.warning(f"⚠️  [ADMIN-GENERATE] User not found: {net_id}")
                return None
            if not row["is_active"]:
                logger.warning(f"⚠️  [ADMIN-GENERATE] User inactive: {net_id}")
                return None
            if row["is_admin"]:
                logger.warning(
                    f"⚠️  [ADMIN-GENERATE] Cannot generate key for admin {net_id} — "
                    "null their key and have them re-bootstrap"
                )
                return None

            plaintext = generate_api_key()
            await conn.execute(
                """
                UPDATE users
                SET api_key_hash = $1, updated_at = CURRENT_TIMESTAMP
                WHERE net_id = $2
                """,
                hash_api_key(plaintext),
                net_id,
            )

        logger.info(f"🔑 [ADMIN-GENERATE] Key set for user: {net_id}")
        return plaintext

    # ============================================
    # Key Generation — Admin issuing an app key
    # ============================================

    async def admin_generate_key_for_app(self, app_name: str) -> Optional[str]:
        """
        Admin-issued key generation for an app.

        Always overwrites the existing hash — apps cannot self-recover so
        first-issue and regeneration share the same path.
        Returns plaintext key shown ONCE, or None on any failure.
        """
        from utils.key_generator import generate_api_key, hash_api_key

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_active FROM apps WHERE app_name = $1",
                app_name,
            )

            if not row:
                logger.warning(f"⚠️  [APP-KEY] App not found: {app_name}")
                return None
            if not row["is_active"]:
                logger.warning(f"⚠️  [APP-KEY] App inactive: {app_name}")
                return None

            plaintext = generate_api_key()
            await conn.execute(
                """
                UPDATE apps
                SET api_key_hash = $1, updated_at = CURRENT_TIMESTAMP
                WHERE app_name = $2
                """,
                hash_api_key(plaintext),
                app_name,
            )

        logger.info(f"🔑 [APP-KEY] Key generated for app: {app_name}")
        return plaintext

    # ============================================
    # Key Nulling — Admin only
    # ============================================

    async def null_key(self, entity_type: str, identifier: str) -> bool:
        """
        Set api_key_hash to NULL for a user (any) or app.

        identifier = net_id   for entity_type='user'
        identifier = app_name for entity_type='app'

        After nulling:
          - Regular user → self-generates via POST /keys/self-generate
          - Admin        → re-bootstraps via POST /admin/keys/self-bootstrap
          - App          → admin re-generates via POST /admin/keys/app/generate

        The endpoint handler must call auth_cache.evict_by_identity() immediately
        after this returns True so the old key stops working at once.

        Returns True if a row was found and updated, False otherwise.
        """
        async with self.pool.acquire() as conn:
            if entity_type == "user":
                result = await conn.execute(
                    """
                    UPDATE users
                    SET api_key_hash = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE net_id = $1
                    """,
                    identifier,
                )
            elif entity_type == "app":
                result = await conn.execute(
                    """
                    UPDATE apps
                    SET api_key_hash = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE app_name = $1
                    """,
                    identifier,
                )
            else:
                logger.warning(f"⚠️  [NULL-KEY] Unknown entity_type: {entity_type}")
                return False

            rows_affected = int(result.split()[-1])
            if rows_affected == 0:
                logger.warning(f"⚠️  [NULL-KEY] {entity_type} not found: {identifier}")
                return False

        logger.info(f"🗑️  [NULL-KEY] Nulled key for {entity_type}: {identifier}")
        return True

    # ============================================
    # CRUD Operations
    # ============================================

    async def create_user(
        self,
        first_name: str,
        last_name: str,
        net_id: str,
        is_admin: bool = False,
    ) -> str:
        """Create a new user record without an API key. Returns net_id."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (first_name, last_name, net_id, is_admin)
                VALUES ($1, $2, $3, $4)
                """,
                first_name,
                last_name,
                net_id,
                is_admin,
            )
        logger.info(f"✅ Created user: {net_id} (admin={is_admin})")
        return net_id

    async def create_app(
        self,
        app_name: str,
        description: Optional[str] = None,
        created_by: Optional[str] = None,
        semantic_cache_enabled: bool = True,
    ) -> str:
        """Create a new app record without an API key. Returns app_name."""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO apps (app_name, description, created_by, semantic_cache_enabled)
                VALUES ($1, $2, $3, $4)
                """,
                app_name,
                description,
                created_by,
                semantic_cache_enabled,
            )
        logger.info(f"✅ Created app: {app_name} (created_by={created_by})")
        return app_name

    # ============================================
    # Read Operations
    # ============================================

    async def list_users(self) -> list:
        """Return all users. api_key_hash never included in output."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    net_id,
                    first_name,
                    last_name,
                    is_active,
                    is_admin,
                    api_key_hash IS NOT NULL AS has_key,
                    created_at,
                    updated_at
                FROM users
                ORDER BY net_id
                """
            )
            return [dict(row) for row in rows]

    async def list_apps(self) -> list:
        """Return all apps. api_key_hash never included in output."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    app_name,
                    description,
                    created_by,
                    is_active,
                    semantic_cache_enabled,
                    api_key_hash IS NOT NULL AS has_key,
                    created_at,
                    updated_at
                FROM apps
                ORDER BY app_name
                """
            )
            return [dict(row) for row in rows]


# Global instance
postgres_db = PostgresDB()
