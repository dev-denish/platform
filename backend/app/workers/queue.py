"""
Background work abstraction.

Existing implementation (MVP): the `async def` upload handler called synchronous,
CPU/IO-heavy raster work directly - blocking the entire event loop for every user
for the whole duration of an ingest.

Phase 1 solution: a `TaskRunner` interface with `ThreadPoolTaskRunner`, which
offloaded the blocking ingest to a bounded worker pool AND AWAITED it to
completion, returning the result inline - the request still blocked for the whole
ingest, just off the event loop's own thread.

Phase 2 contract change (the ONE adjustment to this seam): `run()` no longer awaits
the callable to completion. `POST /datasets/upload` now returns 202 as soon as a
job is queued; the outcome is discovered later via `GET /jobs/{id}`, which reads it
from the `jobs` table written by the job function itself (see workers/jobs.py). A
request-blocking await-to-completion is incompatible with that 202+polling
contract, so `run()` now means "dispatch for background execution, return once
accepted" - fire-and-forget from the caller's point of view, not "block until
done." `shutdown()` needs no change: it was always about releasing THIS process's
own resources on graceful exit, which is still true for both implementations below.

`ArqTaskRunner` is the distributed backend (arq on Redis) the roadmap calls for;
`ThreadPoolTaskRunner` remains as the dev fallback behind `DMRV_TASK_RUNNER_BACKEND`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol, TypeVar

from arq.connections import ArqRedis

from app.core.logging import get_logger

log = get_logger("dmrv.workers")

T = TypeVar("T")


class TaskRunner(Protocol):
    async def run(self, fn: Callable[..., T], /, *args, **kwargs) -> T: ...
    def shutdown(self) -> None: ...


class ThreadPoolTaskRunner:
    """In-process fallback (`DMRV_TASK_RUNNER_BACKEND=threadpool`): dispatches a job
    to a bounded worker pool WITHOUT awaiting completion, so callers still get 202
    semantics even without Redis/arq available.

    Deliberately narrowed from "executor of arbitrary callables" to "executor of
    ctx-shaped job functions" (`fn(ctx, *args, **kwargs)`, see workers/jobs.py):
    `ArqTaskRunner` can only dispatch a job by name to a function registered in a
    SEPARATE worker process, so both backends need to agree on one call shape.
    There is exactly one real caller of this seam (job dispatch) after this
    refactor, so narrowing it is reasonable, not over-engineering.

    ponytail: no retry scheduler here - a job that fails transiently is left
    however workers/jobs.py left it (`queued` with an error, or `dead_letter`);
    nothing re-runs it automatically. That's acceptable for a local-dev fallback;
    the upgrade path is simply running the arq backend, which does retry.
    """

    def __init__(self, max_workers: int = 4, ctx: dict[str, Any] | None = None) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ingest")
        # job_try is fixed at 1: there is no retry loop driving repeated attempts
        # in this backend (see class docstring).
        self._ctx: dict[str, Any] = {**(ctx or {}), "job_try": 1}

    async def run(self, fn: Callable[..., T], /, *args, **kwargs) -> None:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._pool, lambda: fn(self._ctx, *args, **kwargs))

        def _log_if_failed(fut: asyncio.Future[Any]) -> None:
            if fut.cancelled():
                return
            exc = fut.exception()
            if exc is not None:
                # Nothing else awaits this future, so its exception would
                # otherwise vanish as an "exception never retrieved" warning.
                log.error("threadpool.job.failed", error=str(exc))

        future.add_done_callback(_log_if_failed)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


class ArqTaskRunner:
    """Distributed backend (`DMRV_TASK_RUNNER_BACKEND=arq`, the default): enqueues a
    job onto Redis for a SEPARATE `arq worker` process to execute
    (see app/workers/arq_worker.py).

    arq jobs cross a process boundary via Redis, so `fn` must be a plain top-level
    function resolved by NAME in the worker's `WorkerSettings.functions` - not a
    bound method or closure referencing this process's DB pool, which the worker
    process could never execute. `*args`/`**kwargs` must be JSON/pickle-safe plain
    values (str/dict/None), not Pydantic model instances or DB connection objects.
    """

    def __init__(self, redis: ArqRedis) -> None:
        self.redis = redis

    async def run(self, fn: Callable[..., T], /, *args, **kwargs) -> None:
        await self.redis.enqueue_job(fn.__name__, *args, **kwargs)

    def shutdown(self) -> None:
        # No-op: the worker process lifecycle lives in the separate `arq worker`
        # deployment, not here. The Redis pool this class wraps is opened/closed
        # by app.main's lifespan (it owns the connection, this class just uses it).
        pass
