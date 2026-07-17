"""Job submission (with idempotency) + read access. Constructed like
IngestionService/ProjectService from `db`/`settings`; keeps the upload endpoint
thin (validate -> call here) rather than inlining transaction/locking logic there."""
from __future__ import annotations

import uuid
from uuid import UUID

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import NotFoundError
from app.domain.dtos import JobOut
from app.repositories.jobs import JobRepository


class JobService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def submit(
        self,
        *,
        user_id: UUID,
        kind: str,
        idempotency_key: str | None,
        request_id: str | None,
    ) -> tuple[UUID, bool]:
        """Returns `(job_id, is_new)`. `is_new=False` means an existing job within
        the idempotency window was returned instead of inserting a duplicate - the
        caller must not re-enqueue in that case."""
        with self.db.transaction() as cur:
            repo = JobRepository(cur)
            if idempotency_key:
                repo.take_idempotency_lock(user_id, idempotency_key)
                existing = repo.find_recent(
                    user_id, idempotency_key, self.settings.job_idempotency_window_hours
                )
                if existing:
                    return existing["id"], False
            job_id = uuid.uuid4()
            repo.insert(
                job_id=job_id,
                user_id=user_id,
                kind=kind,
                idempotency_key=idempotency_key,
                request_id=request_id,
            )
            return job_id, True

    def mark_enqueue_failed(self, job_id: UUID, message: str) -> None:
        """Best-effort cleanup: the transaction that inserted the job already
        committed, so if enqueueing onto Redis then fails, this at least stops the
        row from being an orphaned `queued` job nothing will ever process."""
        with self.db.transaction() as cur:
            JobRepository(cur).mark_failed(
                job_id, {"code": "enqueue_failed", "message": message}
            )

    def get_for_user(self, job_id: UUID, user_id: UUID) -> JobOut:
        with self.db.connection() as conn, conn.cursor() as cur:
            row = JobRepository(cur).get_for_user(job_id, user_id)
        if not row:
            raise NotFoundError("Job not found.")
        return JobOut(**row)
