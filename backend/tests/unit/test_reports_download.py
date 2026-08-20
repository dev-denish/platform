"""Unit tests for `GET /reports/{job_id}/download` (app/api/v1/reports.py's
`download_report`) - specifically the format-derived media_type/headers
branch added by Wave: HTML report rendering, and the Content-Disposition
safety fix from the follow-up security fix pass.

No DB/Storage/Redis: `download_report` is a plain function whose `jobs`/
`storage` parameters are FastAPI DI placeholders, so it is called directly
here with small fakes - the same "call the endpoint function, fake its
dependencies" seam already used for other job-shaped endpoints in this
codebase (see tests/unit/test_report_jobs.py's own docstring for the
analogous choice at the job-worker layer).

The main thing under test in the first block below is the backward-compat
default: a job whose `result` dict predates this wave (Wave: PDF report,
before "format" was ever written) has no "format" key at all, and must
still download as a PDF exactly as it always did - see reports.py's own
comment on why this is a `.get(..., "pdf")` default with no DB migration.
Every literal Content-Disposition value asserted below now includes the
RFC 5987/6266 `filename*=UTF-8''...` parameter added by
app.core.http_headers.content_disposition_attachment - for a plain-ASCII
filename this is a harmless, always-present addition (see that module's
own docstring on why the dual form is emitted unconditionally rather than
only when the name happens to contain non-ASCII characters).

The second block (`test_*_content_disposition_*`) is the security fix
pass's own regression coverage: a Kannada project name (real non-latin-1
Unicode, not a contrived edge case) must not 500 the endpoint, and a
project name containing Content-Disposition-breaking characters must not
produce a header with an unescaped `"` in the filename value."""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import Response

from app.api.v1.reports import download_report
from app.core.errors import NotFoundError, ValidationError
from app.domain.dtos import CurrentUser
from app.domain.enums import Role

_JOB_ID = uuid4()
_USER = CurrentUser(user_id=uuid4(), username="denish", role=Role.ADMINISTRATOR)


class _FakeJobOut:
    def __init__(self, *, kind: str, status: str, result: dict | None):
        self.id = _JOB_ID
        self.kind = kind
        self.status = status
        self.result = result


class _FakeJobService:
    def __init__(self, job):
        self._job = job
        self.requested: tuple[UUID, UUID] | None = None

    def get_for_user(self, job_id: UUID, user_id: UUID):
        self.requested = (job_id, user_id)
        return self._job


class _FakeStorage:
    def __init__(self):
        self.opened_key: str | None = None

    def open_stream(self, key: str):
        # Records `key` EAGERLY (before returning), not inside the generator
        # body below - a generator function's body doesn't execute at all
        # until something iterates it, and `download_report` only ever wraps
        # the return value in `StreamingResponse` without iterating it itself
        # (iteration happens later, at actual ASGI send time) - a `yield`
        # here directly would make `opened_key` never get set in this test.
        self.opened_key = key

        def _gen():
            yield b"fake-bytes"

        return _gen()

    def save(self, key: str, src_path: str) -> str:
        raise AssertionError("download_report never calls save()")


def _succeeded_job(result: dict) -> _FakeJobOut:
    return _FakeJobOut(kind="generate_report", status="succeeded", result=result)


def test_legacy_job_with_no_format_key_downloads_as_pdf():
    """Simulates a job succeeded before this wave ever wrote a "format" key -
    must still behave exactly as it always did: application/pdf, no new
    security headers, default "report.pdf" filename behaviour."""
    job = _succeeded_job({"storage_key": "reports/p1/j1.pdf"})  # no "format", no "filename"
    jobs = _FakeJobService(job)
    storage = _FakeStorage()
    response = Response()

    result = download_report(_JOB_ID, _USER, jobs, storage, response)

    assert result.media_type == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"
    )
    assert "X-Content-Type-Options" not in response.headers
    assert "Content-Security-Policy" not in response.headers
    assert storage.opened_key == "reports/p1/j1.pdf"


def test_job_with_explicit_pdf_format_behaves_exactly_as_legacy():
    job = _succeeded_job({
        "storage_key": "reports/p1/j2.pdf", "filename": "MyProject-report-2026-08-20.pdf",
        "format": "pdf",
    })
    jobs = _FakeJobService(job)
    storage = _FakeStorage()
    response = Response()

    result = download_report(_JOB_ID, _USER, jobs, storage, response)

    assert result.media_type == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        "attachment; filename=\"MyProject-report-2026-08-20.pdf\"; "
        "filename*=UTF-8''MyProject-report-2026-08-20.pdf"
    )
    assert "X-Content-Type-Options" not in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_job_with_html_format_gets_html_media_type_and_security_headers():
    job = _succeeded_job({
        "storage_key": "reports/p1/j3.html", "filename": "MyProject-report-2026-08-20.html",
        "format": "html",
    })
    jobs = _FakeJobService(job)
    storage = _FakeStorage()
    response = Response()

    result = download_report(_JOB_ID, _USER, jobs, storage, response)

    assert result.media_type == "text/html; charset=utf-8"
    assert response.headers["Content-Disposition"] == (
        "attachment; filename=\"MyProject-report-2026-08-20.html\"; "
        "filename*=UTF-8''MyProject-report-2026-08-20.html"
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    )
    assert storage.opened_key == "reports/p1/j3.html"


def test_job_not_generate_report_kind_is_404():
    job = _FakeJobOut(kind="ingest", status="succeeded", result={"storage_key": "x"})
    jobs = _FakeJobService(job)
    storage = _FakeStorage()

    with pytest.raises(NotFoundError):
        download_report(_JOB_ID, _USER, jobs, storage, Response())


def test_job_not_yet_succeeded_is_a_validation_error():
    job = _FakeJobOut(kind="generate_report", status="running", result=None)
    jobs = _FakeJobService(job)
    storage = _FakeStorage()

    with pytest.raises(ValidationError):
        download_report(_JOB_ID, _USER, jobs, storage, Response())


def test_missing_storage_key_is_404():
    job = _succeeded_job({"format": "pdf"})  # succeeded but no storage_key at all
    jobs = _FakeJobService(job)
    storage = _FakeStorage()

    with pytest.raises(NotFoundError):
        download_report(_JOB_ID, _USER, jobs, storage, Response())


# --- Security fix pass: Content-Disposition safety
# (app.core.http_headers.content_disposition_attachment) -----------------


def test_non_latin1_project_name_downloads_without_500ing():
    """Real bug, not a contrived edge case: Starlette encodes header values
    as latin-1, so a Kannada (or any non-latin-1 Unicode) project name in
    `filename` used to raise a UnicodeEncodeError deep inside Starlette's
    header encoding and 500 this endpoint outright. Must now download
    successfully with a sane ASCII `filename=` fallback and a correct
    `filename*=UTF-8''...` parameter carrying the real name."""
    from urllib.parse import quote

    filename = "ಕನ್ನಡ Project-report-2026-08-20.html"
    job = _succeeded_job({
        "storage_key": "reports/p1/j4.html", "filename": filename, "format": "html",
    })
    jobs = _FakeJobService(job)
    storage = _FakeStorage()
    response = Response()

    # Must not raise (this is the availability bug: it used to 500 here).
    result = download_report(_JOB_ID, _USER, jobs, storage, response)

    assert result.media_type == "text/html; charset=utf-8"
    disposition = response.headers["Content-Disposition"]
    assert disposition == (
        'attachment; filename="Project-report-2026-08-20.html"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    # The ASCII fallback must be a real, latin-1-encodable header value -
    # this is exactly the encoding Starlette itself performs, so this
    # assertion fails the same way the pre-fix bug did if it regresses.
    disposition.encode("latin-1")


def test_project_name_with_injection_characters_does_not_break_header():
    """A project name containing `"`/`;` (Content-Disposition's own
    delimiter characters) must not let the resulting header contain an
    unescaped `"` inside the filename value or an extra `;`-delimited
    parameter - both of which could break the header's parsing or spoof the
    downloaded file's apparent name/extension. Modelled on a filename that
    reached this endpoint un-sanitised (e.g. a legacy job row from before
    app.workers.report_jobs started stripping these characters at
    construction time) - `download_report` must defend against this on its
    own, not merely rely on the writer having sanitised it first."""
    filename = 'Kanha "; filename="evil.html-report-2026-08-20.html'
    job = _succeeded_job({
        "storage_key": "reports/p1/j5.html", "filename": filename, "format": "html",
    })
    jobs = _FakeJobService(job)
    storage = _FakeStorage()
    response = Response()

    result = download_report(_JOB_ID, _USER, jobs, storage, response)

    assert result.media_type == "text/html; charset=utf-8"
    disposition = response.headers["Content-Disposition"]
    # Exactly the two quotes delimiting the `filename="..."` parameter's own
    # quoted-string - none of the `"` characters from the malicious input
    # survive to break out of it and start a second, attacker-controlled
    # `filename="evil.html"` parameter (the actual injection this input is
    # shaped to attempt).
    assert disposition.count('"') == 2
    assert '"; filename="evil' not in disposition
    # The raw `;`/`"` from the project name must not have made it through -
    # only the two `;` that separate this header's own two parameters remain.
    assert disposition.count(";") == 2
