"""
Application factory + wiring.

Existing implementation (MVP): a module-global `app`, `CORS allow_origins=["*"]`, no
lifespan (so the DB pool/graceful shutdown had nowhere to live), no exception
handlers (so domain errors and crashes leaked stack/DB text), no rate limiting.

Enterprise solution: `create_app()` builds a fully-wired app with:
  * a lifespan that opens the DB pool and worker pool on startup and closes them on
    graceful shutdown (SIGTERM from Kubernetes drains cleanly),
  * request-context middleware (request/correlation IDs + access logs),
  * global handlers that map DomainError -> HTTP and turn any unhandled exception
    into a generic 500 (full detail logged, nothing leaked),
  * CORS restricted to the configured allow-list,
  * rate limiting (login/upload/default buckets),
  * the versioned API mounted at DMRV_API_V1_PREFIX,
  * /livez, /readyz, /healthz mounted at the root for probes.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import arq
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.errors import DomainError
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import limiter
from app.services.ingestion.storage import LocalStorage, build_storage
from app.workers.queue import ArqTaskRunner, TaskRunner, ThreadPoolTaskRunner

log = get_logger("dmrv.app")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(settings)
        db.connect()
        storage = build_storage(settings)

        # The job function needs the same db/storage/settings whichever backend
        # calls it (see workers/jobs.py); ArqTaskRunner's worker process builds its
        # own ctx independently in arq_worker.py, since it's a separate OS process.
        job_ctx = {"db": db, "storage": storage, "settings": settings}
        redis_pool: arq.ArqRedis | None = None
        runner: TaskRunner
        if settings.task_runner_backend == "arq":
            redis_pool = await arq.create_pool(
                arq.connections.RedisSettings.from_dsn(settings.redis_url)
            )
            runner = ArqTaskRunner(redis_pool)
        else:
            runner = ThreadPoolTaskRunner(max_workers=4, ctx=job_ctx)

        app.state.settings = settings
        app.state.db = db
        app.state.storage = storage
        app.state.task_runner = runner
        app.state.limiter = limiter
        log.info(
            "app.startup", environment=settings.environment,
            task_runner=settings.task_runner_backend,
        )
        try:
            yield
        finally:
            runner.shutdown()
            if redis_pool is not None:
                await redis_pool.aclose()
            db.close()
            log.info("app.shutdown")

    app = FastAPI(
        title="dMRV Analytical Platform API",
        version="1.0.0",
        description="Enterprise dMRV platform API (v1).",
        docs_url="/docs",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )
    app.state.limiter = limiter

    # --- middleware
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins or ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
    )

    # --- exception handlers (nothing internal leaks)
    @app.exception_handler(DomainError)
    async def _domain_handler(request: Request, exc: DomainError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("domain.error", code=exc.code, error=str(exc))
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RateLimitExceeded)
    async def _ratelimit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "rate_limited", "message": "Too many requests."}},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled.exception", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "An unexpected error occurred."}
            },
        )

    # --- routes
    app.include_router(_lazy_health(settings))
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # --- dev-only static previews (S3 presigned URLs replace this in cloud)
    if settings.storage_backend == "local":
        store = LocalStorage(settings.local_data_dir)
        app.mount(
            "/previews",
            StaticFiles(directory=str(store.root), check_dir=False),
            name="previews",
        )

    return app


def _lazy_health(settings: Settings):
    """Health router that resolves the live DB from app.state at call time, so it uses
    the pooled connection opened in the lifespan (not a throwaway)."""
    from fastapi import APIRouter
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    from app.api.v1.health import VERSION
    from app.core.metrics import sample_arq_gauges
    from app.domain.dtos import HealthStatus, ReadyStatus

    r = APIRouter(tags=["health"])

    @r.get("/livez", response_model=HealthStatus)
    def _livez() -> HealthStatus:
        return HealthStatus(status="ok", service=settings.service_name,
                            environment=settings.environment, version=VERSION)

    @r.get("/readyz", response_model=ReadyStatus)
    def _readyz(request: Request, response: Response) -> ReadyStatus:
        db: Database = request.app.state.db
        ok = db.check()
        checks = {"database": "ok" if ok else "unreachable"}
        if not ok:
            response.status_code = 503
            return ReadyStatus(status="not_ready", checks=checks)
        return ReadyStatus(status="ready", checks=checks)

    @r.get("/healthz", response_model=HealthStatus)
    def _healthz() -> HealthStatus:
        return HealthStatus(status="ok", service=settings.service_name,
                            environment=settings.environment, version=VERSION)

    @r.get("/metrics")
    async def _metrics(request: Request) -> Response:
        # Intentionally UNAUTHENTICATED - this is scraped by an in-cluster
        # Prometheus, not exposed publicly; protect it at the network/firewall
        # layer (e.g. no ingress rule), not with app-level auth middleware here.
        await sample_arq_gauges(request.app.state.task_runner)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return r
