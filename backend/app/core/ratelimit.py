"""Shared rate limiter.

Existing implementation (MVP-turned-bug): in-memory storage, which is per-process -
behind gunicorn's WEB_CONCURRENCY>1 (see deploy/docker-compose.yml) each worker counts
independently, so the real ceiling is ~N x the nominal limit for N workers. Confirmed
empirically: 429s landed at attempts 7/8/9/11 instead of the nominal 6th request.

Fix: back the limiter with the SAME Redis instance arq already uses (`settings.redis_url`
- no second Redis config), so all workers share one counter per `{ip}:{endpoint}` key.
slowapi/limits does this via Lua INCR+EXPIRE (fixed-window) - no custom counter code
needed. `in_memory_fallback_enabled` degrades to the old per-process behavior if Redis
is ever unreachable (also what makes this work in dev/tests without a real Redis),
rather than raising 500s on every rate-limited endpoint.

Known fixed-window gap (not solved here, doesn't matter at these limits): a client can
burst up to 2x the limit across a window boundary (e.g. 5 requests at 0:59, 5 more at
1:00). Upgrade path if that ever matters: limits' sliding-window-counter strategy.
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

# No global default limits: we opt specific routes in via @limiter.limit(...).
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=get_settings().redis_url,
    in_memory_fallback_enabled=True,
)
