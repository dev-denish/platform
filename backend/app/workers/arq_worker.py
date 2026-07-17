"""
arq worker entrypoint: `arq app.workers.arq_worker.WorkerSettings`.

Runs as a SEPARATE deployment of the SAME image (`arq worker` vs `gunicorn` for
the API) - the first real service extraction the roadmap calls for (Phase 2:
"Job/Worker runtime... separate deploy of same image"). Opens its own
`Database`/`Storage` once per worker process, mirroring app.main's lifespan,
because this is a different OS process with no access to the API process's pool.
"""
from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.db import Database
from app.core.logging import configure_logging, get_logger
from app.services.ingestion.storage import build_storage
from app.workers.jobs import run_ingest_job

log = get_logger("dmrv.worker")


async def on_startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    db = Database(settings)
    db.connect()
    ctx["settings"] = settings
    ctx["db"] = db
    ctx["storage"] = build_storage(settings)
    log.info("worker.startup", environment=settings.environment)


async def on_shutdown(ctx: dict[str, Any]) -> None:
    db: Database = ctx["db"]
    db.close()
    log.info("worker.shutdown")


class WorkerSettings:
    """Resolved by `arq`'s CLI via `get_kwargs()`: every attribute here that
    matches a `Worker.__init__` parameter name is passed through (see
    arq.worker.create_worker)."""

    functions = [run_ingest_job]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # Aligns arq's own retry bookkeeping with workers/jobs.py's dead-letter check
    # (`ctx["job_try"] >= settings.job_max_retries`) - the two systems must agree
    # on when to stop, or one gives up before/after the other expects.
    max_tries = get_settings().job_max_retries
