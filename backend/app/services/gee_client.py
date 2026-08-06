"""
Google Earth Engine session init, shared by GEEAnalysisService and the
standalone scripts/gee_phase1_agb_proxy.py.

DEV-ONLY AUTH: credentials are a personal Google account's `earthengine
authenticate` token (mounted read-only into the container, see
deploy/docker-compose.yml), not a GCP service account. That's fine for this
testing/development setup, but a personal OAuth credential is NOT how a
production deployment should authenticate to GEE - move this to a service
account (ee.ServiceAccountCredentials) before any real production reliance.

ee.Initialize(project=...) is required - a bare ee.Initialize() raises
"no project found" on this account. Cached per-process (lru_cache) since
gunicorn workers are long-lived and re-initializing on every request would
re-touch the OAuth token for no reason.
"""
from __future__ import annotations

from functools import lru_cache

import ee

from app.core.config import get_settings


@lru_cache(maxsize=1)
def init_ee() -> None:
    ee.Initialize(project=get_settings().gee_project_id)
