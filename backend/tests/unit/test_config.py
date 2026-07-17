"""Unit tests for the configuration layer's fail-fast secret hygiene: production-like
environments MUST reject weak/placeholder secrets and permissive CORS, while dev
generates a strong ephemeral secret so local runs never ship a hardcoded one."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_rejects_weak_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", jwt_secret="short", db_password="realpw",
            cors_allow_origins=["https://app.example.com"], storage_backend="s3",
        )


def test_production_rejects_wildcard_cors():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", jwt_secret="s" * 48, db_password="realpw",
            cors_allow_origins=["*"], storage_backend="s3",
        )


def test_production_rejects_local_storage():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", jwt_secret="s" * 48, db_password="realpw",
            cors_allow_origins=["https://app.example.com"], storage_backend="local",
        )


def test_production_accepts_strong_config():
    s = Settings(
        environment="production", jwt_secret="s" * 48, db_password="a-real-password",
        cors_allow_origins=["https://app.example.com"], storage_backend="s3",
        s3_bucket="dmrv-prod", debug=False,
        redis_url="redis://prod-redis.internal:6379/0",
    )
    assert s.is_production_like is True
    assert len(s.jwt_secret) >= 32


def test_production_rejects_localhost_redis_for_arq_backend():
    with pytest.raises(ValidationError):
        Settings(
            environment="production", jwt_secret="s" * 48, db_password="realpw",
            cors_allow_origins=["https://app.example.com"], storage_backend="s3",
            task_runner_backend="arq", redis_url="redis://localhost:6379/0",
        )


def test_production_allows_localhost_redis_for_threadpool_backend():
    # threadpool never talks to Redis, so a localhost redis_url is irrelevant to it.
    s = Settings(
        environment="production", jwt_secret="s" * 48, db_password="realpw",
        cors_allow_origins=["https://app.example.com"], storage_backend="s3",
        task_runner_backend="threadpool", redis_url="redis://localhost:6379/0",
    )
    assert s.task_runner_backend == "threadpool"


def test_dev_generates_ephemeral_secret():
    s = Settings(environment="dev")  # no secret provided
    assert s.jwt_secret and len(s.jwt_secret) >= 32


def test_cors_csv_is_parsed():
    s = Settings(environment="dev", cors_allow_origins="http://a.com, http://b.com")
    assert s.cors_allow_origins == ["http://a.com", "http://b.com"]
