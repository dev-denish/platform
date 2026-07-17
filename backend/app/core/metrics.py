"""
Prometheus metrics (Phase 2 observability).

Existing implementation (MVP/Phase 1): none - job throughput, failure rate, and
queue backlog were invisible; the only signal was reading application logs by hand.

Enterprise solution: the 5 series the roadmap calls for, scraped at `GET /metrics`
(mounted unauthenticated at the health-check tier - see app/main.py). `queue_depth`
and `worker_active` are sampled live from Redis at scrape time rather than kept as
in-process counters, since the thing being measured (arq's queue) lives in Redis,
not in this process.

KNOWN GAP (found by actually running the API + arq worker as two real processes
against real Redis/Postgres, not just unit tests): `prometheus_client`'s default
registry is per-process. `jobs_submitted_total` is incremented in the API process
(POST /datasets/upload) and so correctly shows up on the API's own `GET /metrics`.
`jobs_completed_total`/`job_duration_seconds`, however, are incremented inside
workers/jobs.py, which runs in the SEPARATE `arq worker` process - so they will
read as absent/zero on the API's `/metrics`, not the worker's real values. The
worker process never serves HTTP, so nothing currently scrapes its registry either.
Upgrade path: `prometheus_client`'s multiprocess mode (`PROMETHEUS_MULTIPROC_DIR`
+ `multiprocess.MultiProcessCollector`) is the standard fix, but it requires
Dockerfile/deployment wiring for both the API and worker containers, which is
outside this module's/PR's scope - flagging it here rather than shipping a metric
that silently reads wrong.
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

jobs_submitted_total = Counter(
    "jobs_submitted_total",
    "Background jobs accepted for processing (idempotent replays do not count).",
    ["kind"],
)
jobs_completed_total = Counter(
    "jobs_completed_total",
    "Background jobs that reached a terminal state.",
    ["kind", "status"],
)
job_duration_seconds = Histogram(
    "job_duration_seconds",
    "Wall-clock seconds a background job spent processing (from running to terminal).",
    ["kind"],
)
queue_depth = Gauge(
    "queue_depth",
    "Jobs currently sitting in the arq queue (ready + not-yet-due), sampled at scrape time.",
    ["queue_name"],
)
worker_active = Gauge(
    "worker_active",
    "Jobs currently being executed by any arq worker process, sampled at scrape time.",
)


async def sample_arq_gauges(runner: object) -> None:
    """Best-effort live sample of `queue_depth`/`worker_active` from Redis. A no-op
    for the ThreadPoolTaskRunner backend (nothing in Redis to sample).

    `queue_depth` is `ZCARD` on arq's own queue key - exact and cheap, part of the
    behaviour `ArqRedis` documents (`enqueue_job` writes to `_queue_name`, default
    `redis.default_queue_name`), so this reads as stable public-ish behaviour.

    `worker_active` is different: arq only writes a live job count into its
    health-check key on an interval measured in HOURS by default - far too coarse
    for a Prometheus scrape. The only live signal is the `arq:in-progress:<job_id>`
    key each worker sets while running a job (verified by reading
    arq.worker.Worker.start_jobs/run_job directly, arq 0.28.0). That key prefix
    (`arq.constants.in_progress_key_prefix`) is an internal implementation detail,
    not part of arq's public `__all__` - a ceiling worth naming: if a future arq
    release renames/removes it, this gauge silently reads 0. Upgrade path: swap to
    whatever live counter arq's own health-check API exposes if one ever lands.
    """
    from arq.constants import in_progress_key_prefix

    from app.workers.queue import ArqTaskRunner  # local import: avoid a load-time cycle

    if not isinstance(runner, ArqTaskRunner):
        return

    redis = runner.redis
    depth = await redis.zcard(redis.default_queue_name)
    queue_depth.labels(queue_name=redis.default_queue_name).set(depth)

    active = 0
    async for _ in redis.scan_iter(match=f"{in_progress_key_prefix}*", count=200):
        active += 1
    worker_active.set(active)
