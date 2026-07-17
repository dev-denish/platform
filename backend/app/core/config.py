"""
Central configuration layer.

Existing implementation (MVP): module-level `os.environ.get("JWT_SECRET",
"dev-only-secret-change-in-production")` scattered across files, with WORKING
insecure fallbacks.

Why that was insufficient: a service that boots with a known default secret is
a full auth-bypass waiting for one missed environment variable. Config was also
duplicated in five files with no single source of truth and no validation.

Enterprise solution: one typed `Settings` object (pydantic-settings) loaded once,
validated at import time. Secrets have NO usable defaults in non-dev environments
- the app refuses to start if they are missing or weak. This is the pattern used
behind AWS Secrets Manager / GCP Secret Manager / Vault: secrets are injected as
env vars at runtime and asserted on boot.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["dev", "test", "staging", "production"]

# A short, obviously-placeholder value we ship in dev compose only. It is treated
# as "unset" everywhere else so it can never silently protect real data.
_INSECURE_PLACEHOLDERS = {
    "",
    "change-me",
    "dev-only-secret-change-in-production",
    "change-this-to-a-real-secret-before-any-real-deployment",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DMRV_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment ---
    environment: Environment = "dev"
    debug: bool = False
    service_name: str = "dmrv-api"
    api_v1_prefix: str = "/api/v1"

    # --- Database ---
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "dmrv"
    db_user: str = "postgres"
    db_password: str = Field(default="", repr=False)
    db_pool_min: int = 1
    db_pool_max: int = 10
    db_pool_timeout_s: float = 10.0
    db_statement_timeout_ms: int = 30_000

    # --- Auth / JWT ---
    jwt_secret: str = Field(default="", repr=False)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 14
    jwt_issuer: str = "dmrv-platform"
    jwt_audience: str = "dmrv-clients"

    # --- CORS (explicit allow-list, never "*") ---
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Storage ---
    # "local" for dev; "s3" in cloud. The service layer depends on the abstraction,
    # not on either backend, so this switch is the only thing that changes.
    storage_backend: Literal["local", "s3"] = "local"
    local_data_dir: str = "/var/lib/dmrv/data"
    # Phase 2: where an in-flight upload is staged before a job processes it. This
    # MUST be a volume shared between the API and worker containers - unlike
    # local_data_dir, it is never exposed via the /previews static mount, since the
    # raw uploaded file hasn't been validated yet.
    upload_staging_dir: str = "/var/lib/dmrv/staging"
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None  # for MinIO / localstack

    # --- Uploads / limits ---
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB hard cap
    allowed_raster_extensions: tuple[str, ...] = (".tif", ".tiff", ".img")
    raster_window_size: int = 2048  # windowed-read block edge, in pixels

    # --- Rate limiting ---
    rate_limit_login: str = "5/minute"
    rate_limit_upload: str = "20/hour"
    rate_limit_default: str = "120/minute"

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True

    # --- Background jobs / async ingestion (Phase 2) ---
    # Default in dev = arq; ThreadPoolTaskRunner is the fallback for a Redis-less
    # local run (see workers/queue.py).
    task_runner_backend: Literal["arq", "threadpool"] = "arq"
    # A benign default ENDPOINT, not a secret (same posture as db_host: str =
    # "localhost" above) - it just says where to look, it grants nothing on its own.
    redis_url: str = "redis://localhost:6379/0"
    job_idempotency_window_hours: int = 24
    job_max_retries: int = Field(default=5, ge=1)

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        # Allow a comma-separated env string as well as a JSON/list value.
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production_like(self) -> bool:
        return self.environment in ("staging", "production")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password} "
            f"options='-c statement_timeout={self.db_statement_timeout_ms}'"
        )

    @model_validator(mode="after")
    def _enforce_secret_hygiene(self) -> Settings:
        """Fail fast. A production-like service MUST have strong, explicit secrets."""
        problems: list[str] = []

        secret_is_weak = (
            self.jwt_secret in _INSECURE_PLACEHOLDERS or len(self.jwt_secret) < 32
        )
        db_pw_is_placeholder = self.db_password in _INSECURE_PLACEHOLDERS

        if self.is_production_like:
            if secret_is_weak:
                problems.append(
                    "DMRV_JWT_SECRET is missing/placeholder/too short (need >=32 random chars)."
                )
            if db_pw_is_placeholder:
                problems.append("DMRV_DB_PASSWORD is missing or a known placeholder.")
            if "*" in self.cors_allow_origins or not self.cors_allow_origins:
                problems.append(
                    "DMRV_CORS_ALLOW_ORIGINS must be an explicit allow-list in production."
                )
            if self.storage_backend == "local":
                problems.append(
                    "Local disk storage is not permitted in production; "
                    "set DMRV_STORAGE_BACKEND=s3."
                )
            if self.debug:
                problems.append("DMRV_DEBUG must be false in production.")
            if self.task_runner_backend == "arq":
                redis_host = urlparse(self.redis_url).hostname or ""
                if not self.redis_url or redis_host in ("localhost", "127.0.0.1", ""):
                    problems.append(
                        "DMRV_REDIS_URL must point at a real Redis endpoint in "
                        "production (not empty/localhost) when "
                        "DMRV_TASK_RUNNER_BACKEND=arq."
                    )

        if problems:
            raise ValueError(
                "Insecure configuration for a production-like environment:\n  - "
                + "\n  - ".join(problems)
            )

        # In dev/test, generate an ephemeral per-process secret if none was given,
        # so local runs work WITHOUT ever shipping a hardcoded one.
        if secret_is_weak and not self.is_production_like:
            object.__setattr__(self, "jwt_secret", secrets.token_urlsafe(48))

        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Import this everywhere; never read os.environ directly."""
    return Settings()  # type: ignore[call-arg]
