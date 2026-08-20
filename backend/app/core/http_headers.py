"""Safe `Content-Disposition` header construction (Wave: HTML report
rendering, security fix pass).

Two distinct bugs prompted this, both found by appsec-reviewer against the
real `GET /reports/{job_id}/download` endpoint:

1. Availability: Starlette/httpx encode header values as latin-1. A filename
   built from a user-entered `project_name` (app.domain.dtos, `max_length=256`
   but no charset constraint) containing any non-latin-1 character - Kannada
   script is the concrete case that surfaced this, but this is true of most
   non-Western-European Unicode - raises a `UnicodeEncodeError` deep inside
   Starlette's response-header encoding, 500ing the download endpoint
   entirely for that one project.
2. Header injection: an unescaped `"`, `\r`, `\n`, or `;` in the filename can
   break `Content-Disposition`'s own quoting/parsing. Newly more consequential
   now that HTML is a browser-executable download format (Wave: HTML report
   rendering) - see reports.py's `download_report` docstring on why
   `attachment` + `nosniff` + a strict CSP are the layered mitigations for
   that - a broken/spoofed filename extension is one more thing to close off.

This module is the ONE place both problems are fixed, and is deliberately
generic (not report-specific) so any future download endpoint can reuse it
instead of re-deriving the same header by hand."""
from __future__ import annotations

import re
from urllib.parse import quote

# The four characters that can break Content-Disposition's own quoting/
# parsing (RFC 6266's `disp-ext-parm`/quoted-string grammar) - `"` ends a
# quoted-string early, `;` starts a new parameter, and a bare CR/LF is a
# header-injection primitive regardless of the parameter syntax around it.
_FORBIDDEN_CHARS = re.compile(r'["\r\n;]')


def strip_header_injection_chars(value: str) -> str:
    """Removes the characters above from `value`. Exposed (not just an
    internal helper of `content_disposition_attachment` below) so callers
    that construct a filename ahead of time - e.g.
    app.workers.report_jobs.run_generate_report_job, which persists the
    filename into `job.result` well before any HTTP response exists - can
    sanitise it once at the source, rather than relying solely on
    `content_disposition_attachment`'s own (still-applied, defense-in-depth)
    stripping at response time."""
    return _FORBIDDEN_CHARS.sub("", value)


def content_disposition_attachment(filename: str, *, fallback: str) -> str:
    """Builds a `Content-Disposition: attachment` header value that is both
    injection-safe and safe to hand to Starlette's latin-1 header encoding,
    regardless of what a caller-supplied `filename` (ultimately derived from
    a user-entered project name) contains.

    `fallback` must itself be a plain-ASCII, already-safe name (e.g.
    "report.pdf"/"report.html" - callers should pick one with the correct
    extension for the format being served) - it is used verbatim, never
    re-sanitised, so it must not itself come from user input.

    Emits the RFC 5987/6266 dual form so browsers that support it show the
    real (possibly non-ASCII) filename, while every browser falls back to
    the ASCII-safe `filename=` parameter:
        attachment; filename="<ascii-safe-fallback>"; filename*=UTF-8''<percent-encoded-original>
    """
    cleaned = strip_header_injection_chars(filename) or fallback

    # ASCII-safe fallback for the plain `filename=` parameter: strip (not
    # replace/transliterate - a best-effort transliteration table would need
    # constant upkeep for scripts nobody's tested yet, same reasoning as
    # report_pdf._pdf_safe_text's own "?" fallback for the one out-of-range
    # character it can't map) every non-ASCII character. If nothing
    # meaningful survives (e.g. a project name that is entirely Kannada with
    # no separator/extension around it), fall back to `fallback` outright
    # rather than shipping an empty or punctuation-only filename.
    ascii_name = cleaned.encode("ascii", errors="ignore").decode("ascii").strip()
    if not ascii_name or not ascii_name.strip("-_. "):
        ascii_name = fallback

    # `quote(..., safe="")` still leaves ASCII letters/digits/`-`/`_`/`.`/`~`
    # unescaped (Python's `quote` always treats these as safe, independent of
    # the `safe=` argument) - so a purely-ASCII `cleaned` round-trips through
    # this unchanged, and only genuinely non-ASCII/reserved bytes get
    # percent-encoded, exactly as RFC 5987's `ext-value` requires.
    encoded = quote(cleaned, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'
