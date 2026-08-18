"""Unit tests for `run_generate_report_job`'s failure handling (Wave:
ai-report-narrative, Phase 3) - specifically the ONE deliberate carve-out
from this job's usual generic-retry behaviour: `AiNarrativeError` must
dead-letter the job immediately, never schedule a retry (see
app/workers/report_jobs.py's own module docstring for why). No real DB/
Storage/Redis - `generate_report_pdf_bytes` itself is monkeypatched (its own
`report_type` branching is covered separately by tests/unit/test_report_
service.py), so this suite isolates exactly what this job function does
once that call raises."""
from __future__ import annotations

import asyncio
import contextlib

import pytest
from arq.worker import Retry

from app.services.ai_narrative import AiNarrativeError
from app.workers import report_jobs


class _FakeCur:
    pass


class _FakeDb:
    """Records every `mark_*`/`record_*` call made against it via a fake
    JobRepository, monkeypatched into `report_jobs` below - `transaction()`
    is the only Database method this job ever calls."""

    def transaction(self):
        return contextlib.nullcontext(_FakeCur())

    def connection(self):
        return contextlib.nullcontext(_FakeCur())


class _FakeSettings:
    job_max_retries = 5


def _ctx(db: _FakeDb, job_try: int = 1) -> dict:
    return {"db": db, "storage": object(), "job_try": job_try, "settings": _FakeSettings()}


@pytest.fixture(autouse=True)
def _stub_job_repository(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    class _FakeJobRepository:
        def __init__(self, cur):
            pass

        def mark_running(self, job_id):
            calls.append(("mark_running", (job_id,)))

        def mark_dead_letter(self, job_id, error):
            calls.append(("mark_dead_letter", (job_id, error)))

        def record_retry_error(self, job_id, error):
            calls.append(("record_retry_error", (job_id, error)))

    monkeypatch.setattr(report_jobs, "JobRepository", _FakeJobRepository)
    return calls


def test_ai_narrative_error_dead_letters_without_retrying(monkeypatch, _stub_job_repository):
    def _raise(*a, **k):
        raise AiNarrativeError("section 'ndvi' produced an ungrounded number")

    monkeypatch.setattr(report_jobs, "generate_report_pdf_bytes", _raise)

    db = _FakeDb()
    asyncio.run(
        report_jobs.run_generate_report_job(
            _ctx(db, job_try=1),
            job_id="job-1", project_id="proj-1", project_name="Test Project",
            analysis_ids=["ndvi"], boundary_geojson={}, actor={"user_id": "u1", "username": "u"},
            report_type="ai",
        )
    )

    kinds = [c[0] for c in _stub_job_repository]
    assert "mark_dead_letter" in kinds
    assert "record_retry_error" not in kinds
    dead_letter_call = next(c for c in _stub_job_repository if c[0] == "mark_dead_letter")
    assert dead_letter_call[1][1]["code"] == "ai_narrative_error"


def test_a_generic_error_still_retries_instead_of_dead_lettering_on_first_try(
    monkeypatch, _stub_job_repository
):
    """The pre-existing behaviour for every OTHER exception type must be
    unchanged by adding the AiNarrativeError carve-out above it."""

    def _raise(*a, **k):
        raise RuntimeError("GEE quota exceeded")

    monkeypatch.setattr(report_jobs, "generate_report_pdf_bytes", _raise)

    db = _FakeDb()
    with pytest.raises(Retry):
        asyncio.run(
            report_jobs.run_generate_report_job(
                _ctx(db, job_try=1),
                job_id="job-2", project_id="proj-1", project_name="Test Project",
                analysis_ids=["ndvi"], boundary_geojson={}, actor={"user_id": "u1", "username": "u"},
                report_type="system",
            )
        )

    kinds = [c[0] for c in _stub_job_repository]
    assert "record_retry_error" in kinds
    assert "mark_dead_letter" not in kinds
