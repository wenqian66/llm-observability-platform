"""
Configuration management for LLM Gateway
"""

from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM Backends
    general_llm_url: str
    general_llm_api_key: str
    general_llm_concurrent_limit: int = 1
    coding_llm_url: str
    coding_llm_api_key: str
    coding_llm_concurrent_limit: int = 1
    llm_health_check_timeout: int = 5

    # GPU Metrics — internal ClusterIP scrape targets.
    # Must point directly to the vLLM pod service, NOT through Traefik.
    # The /metrics endpoint is not exposed externally.
    # Leave empty in local dev — scraper skips gracefully if unset.
    general_llm_metrics_url: str = ""
    coding_llm_metrics_url: str = ""

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    semantic_cache_timeout_ms: int = 5000
    semantic_cache_channel: str = "semantic_cache_results"

    # PostgreSQL (User Management & API Keys)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "llm_gateway"
    postgres_user: str = "admin"
    postgres_password: str = ""
    postgres_pool_min_size: int = 5
    postgres_pool_max_size: int = 20

    # TimescaleDB (Request Logs & Metrics)
    timescale_host: str = "localhost"
    timescale_port: int = 5432
    timescale_db: str = "llm_metrics"
    timescale_user: str = "admin"
    timescale_password: str = ""
    timescale_pool_min_size: int = 5
    timescale_pool_max_size: int = 20

    # Authentication toggle
    enable_auth: bool = False

    # Gateway
    gateway_host: str = "0.0.0.0"
    gateway_port: int = 8000

    # Logging
    log_level: str = "INFO"

    # CORS
    # Dev default: wildcard origin, no credentials.
    # Production: set explicit origins and enable credentials.
    # IMPORTANT: cors_allow_credentials=True is invalid when cors_origins=["*"].
    # The validator below enforces this and raises a clear error at startup.
    cors_origins: List[str] = ["*"]
    cors_allow_credentials: bool = False

    # Bootstrap
    # Used once to create the initial admin when the DB is empty.
    # Set to a strong secret in .env — leave empty to disable the endpoint entirely.
    # After first bootstrap, remove or rotate this key — it has no further use.
    # TODO: move to Vault-sourced secret (currently in ConfigMap plaintext)
    bootstrap_api_key: str = ""

    @field_validator("cors_allow_credentials")
    @classmethod
    def credentials_require_explicit_origins(
        cls, allow_credentials: bool, info
    ) -> bool:
        """
        Browsers reject credentialed requests when the server responds with
        Access-Control-Allow-Origin: *. Catch this misconfiguration at startup
        rather than letting it silently fail in the browser.
        """
        origins = info.data.get("cors_origins", ["*"])
        if allow_credentials and origins == ["*"]:
            raise ValueError(
                "cors_allow_credentials=True is invalid when cors_origins=['*']. "
                "Set CORS_ORIGINS to explicit domain(s) in your .env, "
                'e.g. CORS_ORIGINS=["https://yourapp.illinois.edu"]'
            )
        return allow_credentials

    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
