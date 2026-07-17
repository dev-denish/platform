"""Shared rate limiter.

Existing implementation (MVP): none - login and upload were unbounded, so credential
stuffing and upload floods had no brake.

Enterprise solution: a slowapi limiter keyed on client address, applied per-route to
the sensitive endpoints (login, upload) rather than globally, so Kubernetes health
probes and normal reads are never throttled. In a multi-pod deployment the limiter
backing store moves to Redis (slowapi supports it via `storage_uri`) so limits are
enforced across pods; the in-memory store is per-pod for now.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

# No global default limits: we opt specific routes in via @limiter.limit(...).
limiter = Limiter(key_func=get_remote_address)
