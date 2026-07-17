"""
Job status polling (Phase 2).

Every authenticated user may see the status of THEIR OWN jobs - this is not a
role gate, so auth is `get_current_user` directly, not `require_role`. Ownership
is enforced in the SQL (`JobRepository.get_for_user` filters by `user_id`), not as
an app-level check after a generic fetch, so a job that belongs to someone else
returns the SAME 404 as one that doesn't exist at all - no 403-vs-404 existence
leak for other users' jobs.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUserDep, get_job_service
from app.domain.dtos import JobOut
from app.services.jobs_service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut, summary="Poll a background job's status/result")
async def get_job(
    job_id: UUID,
    svc: Annotated[JobService, Depends(get_job_service)],
    user: CurrentUserDep,
) -> JobOut:
    return svc.get_for_user(job_id, user.user_id)
