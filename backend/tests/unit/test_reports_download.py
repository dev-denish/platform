"""Tests for `GET /api/v1/reports/{job_id}/download` (app/api/v1/reports.py's
`download_report`) - specifically the format-derived media_type/headers
branch added by Wave: HTML report rendering, and the Content-Disposition
safety fix from the follow-up security fix pass.

Wave: headers-dropped fix. These tests used to call `download_report` as a
plain Python function, passing a manually-constructed `Response()` object,
then asserted on THAT object's `.headers` afterward. That only proved the
code *set* headers on the object it was handed - it never proved those
headers reached a real HTTP response, because `download_report` returns its
own `StreamingResponse`, and FastAPI does NOT merge headers set on a
separately-injected `response: Response` parameter into an endpoint's own
returned `Response` subclass (see fastapi/routing.py's
`isinstance(raw_response, Response)` branch - only `background` tasks get
merged across the two). That gap is exactly how Content-Disposition,
X-Content-Type-Options and the CSP header were silently dropped in
production while this test file stayed green.

Rewritten to drive the REAL FastAPI app through TestClient (same
API-contract convention as tests/integration/test_api_contract.py - `client`
fixture from tests/conftest.py, per-test `dependency_overrides` for the
services this endpoint needs), and assert on the actual httpx response's
`.headers` - the object the ASGI stack itself produced, not an intermediate
one. This is structurally incapable of missing the headers-dropped bug: if
`download_report` reverted to mutating an injected `response: Response`
param instead of passing `headers=` to `StreamingResponse`'s constructor,
every assertion on `r.headers[...]` below on a non-empty header would raise
`KeyError` (confirmed empirically - see the fix's commit/PR notes).

No DB/Redis: `get_job_service`/`get_storage` are overridden with small
fakes, same "override the service-layer dependency with a fake" seam
`test_api_contract.py` already uses for `get_tile_service`/
`get_gee_analysis_service`.

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

from urllib.parse import quote
from uuid import UUID, uuid4

from app.api import deps
from app.core.errors import NotFoundError
from tests.conftest import _ADMIN_ID

AUTH = {"Authorization": "Bearer admin-token"}

_JOB_ID = uuid4()


class _FakeJobOut:
    def __init__(self, *, kind: str, status: str, result: dict | None):
        self.id = _JOB_ID
        self.kind = kind
        self.status = status
        self.result = result


class _FakeJobService:
    """Mirrors the real JobService.get_for_user ownership check (a job
    belonging to someone else, or a wrong id, 404s) closely enough to drive
    this endpoint's own not-found/validation branches without a real DB."""

    def __init__(self, job):
        self._job = job

    def get_for_user(self, job_id: UUID, user_id: UUID):
        if job_id != self._job.id or user_id != _ADMIN_ID:
            raise NotFoundError("Job not found.")
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


def _wire(client, job) -> _FakeStorage:
    """Overrides this endpoint's two service dependencies on the REAL app
    behind `client`, so the request that follows travels through actual
    FastAPI routing/dependency-resolution/response-construction - not a
    directly-called Python function."""
    storage = _FakeStorage()
    client.app.dependency_overrides[deps.get_job_service] = lambda: _FakeJobService(job)
    client.app.dependency_overrides[deps.get_storage] = lambda: storage
    return storage


def _download(client, job_id=_JOB_ID):
    return client.get(f"/api/v1/reports/{job_id}/download", headers=AUTH)


def test_legacy_job_with_no_format_key_downloads_as_pdf(client):
    """Simulates a job succeeded before this wave ever wrote a "format" key -
    must still behave exactly as it always did: application/pdf, no new
    security headers, default "report.pdf" filename behaviour."""
    job = _succeeded_job({"storage_key": "reports/p1/j1.pdf"})  # no "format", no "filename"
    storage = _wire(client, job)

    r = _download(client)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["Content-Disposition"] == (
        "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"
    )
    assert "X-Content-Type-Options" not in r.headers
    assert "Content-Security-Policy" not in r.headers
    assert r.content == b"fake-bytes"
    assert storage.opened_key == "reports/p1/j1.pdf"


def test_job_with_explicit_pdf_format_behaves_exactly_as_legacy(client):
    job = _succeeded_job({
        "storage_key": "reports/p1/j2.pdf", "filename": "MyProject-report-2026-08-20.pdf",
        "format": "pdf",
    })
    _wire(client, job)

    r = _download(client)

    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["Content-Disposition"] == (
        "attachment; filename=\"MyProject-report-2026-08-20.pdf\"; "
        "filename*=UTF-8''MyProject-report-2026-08-20.pdf"
    )
    assert "X-Content-Type-Options" not in r.headers
    assert "Content-Security-Policy" not in r.headers


def test_job_with_html_format_gets_html_media_type_and_security_headers(client):
    job = _succeeded_job({
        "storage_key": "reports/p1/j3.html", "filename": "MyProject-report-2026-08-20.html",
        "format": "html",
    })
    storage = _wire(client, job)

    r = _download(client)

    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    assert r.headers["Content-Disposition"] == (
        "attachment; filename=\"MyProject-report-2026-08-20.html\"; "
        "filename*=UTF-8''MyProject-report-2026-08-20.html"
    )
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Content-Security-Policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    )
    assert storage.opened_key == "reports/p1/j3.html"


def test_job_not_generate_report_kind_is_404(client):
    job = _FakeJobOut(kind="ingest", status="succeeded", result={"storage_key": "x"})
    _wire(client, job)

    r = _download(client)

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_job_not_yet_succeeded_is_a_validation_error(client):
    job = _FakeJobOut(kind="generate_report", status="running", result=None)
    _wire(client, job)

    r = _download(client)

    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_missing_storage_key_is_404(client):
    job = _succeeded_job({"format": "pdf"})  # succeeded but no storage_key at all
    _wire(client, job)

    r = _download(client)

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_job_belonging_to_a_different_user_is_404(client):
    """Ownership check: same job_id, but the fake's get_for_user only
    recognizes _ADMIN_ID (the user FakeAuthService maps "admin-token" to) -
    a different caller must not be able to download someone else's report."""
    job = _succeeded_job({"storage_key": "reports/p1/j6.pdf"})
    _wire(client, job)

    r = client.get(
        f"/api/v1/reports/{_JOB_ID}/download",
        headers={"Authorization": "Bearer viewer-token"},
    )

    assert r.status_code == 404


# --- Security fix pass: Content-Disposition safety
# (app.core.http_headers.content_disposition_attachment) -----------------


def test_non_latin1_project_name_downloads_without_500ing(client):
    """Real bug, not a contrived edge case: Starlette encodes header values
    as latin-1, so a Kannada (or any non-latin-1 Unicode) project name in
    `filename` used to raise a UnicodeEncodeError deep inside Starlette's
    header encoding and 500 this endpoint outright. Must now download
    successfully with a sane ASCII `filename=` fallback and a correct
    `filename*=UTF-8''...` parameter carrying the real name."""
    filename = "ಕನ್ನಡ Project-report-2026-08-20.html"
    job = _succeeded_job({
        "storage_key": "reports/p1/j4.html", "filename": filename, "format": "html",
    })
    _wire(client, job)

    # Must not raise/500 (this is the availability bug: it used to 500 here).
    r = _download(client)

    assert r.status_code == 200
    assert r.headers["content-type"] == "text/html; charset=utf-8"
    disposition = r.headers["Content-Disposition"]
    assert disposition == (
        'attachment; filename="Project-report-2026-08-20.html"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def test_project_name_with_injection_characters_does_not_break_header(client):
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
    _wire(client, job)

    r = _download(client)

    assert r.status_code == 200
    disposition = r.headers["Content-Disposition"]
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
