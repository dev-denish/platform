"""Unit tests for `generate_report_pdf_bytes`'s `report_type` branching
(Wave: ai-report-narrative, Phase 3) - no real DB/GEE. DB reads
(ForestDefinitionRepository/AnalysisResultRepository) and the GEE map-tile
fetch (`_compute_cached`) are monkeypatched to canned values (same
"canned"-fixture convention as tests/integration/test_gee_analysis_service.py's
own `fake_compute`), so this suite is fast/deterministic/CI-safe and exercises
only the actual new logic: which report types call `generate_ai_summaries`,
that its dict sources every AI-report section's narrative, that
maps/stats/notes/disclaimers are otherwise byte-identical, and that
`AiNarrativeError` is never caught here (it must reach the job runner)."""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest
from pypdf import PdfReader

from app.domain.enums import ReportType
from app.services import report_service
from app.services.ai_narrative import GEMINI_MODEL, AiNarrativeError
from app.services.report_pdf import AI_NARRATIVE_DISCLOSURE_TEMPLATE

_NOW = datetime(2026, 8, 1, tzinfo=UTC)
_AI_DISCLOSURE = AI_NARRATIVE_DISCLOSURE_TEMPLATE.format(model=GEMINI_MODEL)

_HANSEN_STATS = {
    "canopy_cover_threshold_pct": 15.0, "baseline_forest_area_ha": 100.0,
    "gain_area_ha_2000_2012": 5.0, "loss_area_ha_by_year": {"2010": 2.0},
    "coverage_pct": 100.0, "note": "Gain is a whole-period 2000-2012 figure only.",
}
_DW_STATS = {"class_area_ha": {"Trees": 20.0}, "coverage_pct": 90.0}

_ROWS = {
    "hansen_gfc": {
        "computed_at": _NOW, "stats": _HANSEN_STATS, "legend": None, "params_key": "default",
    },
    "dynamic_world": {
        "computed_at": _NOW, "stats": _DW_STATS, "legend": None, "params_key": "default",
    },
}


class _FakeCur:
    """Never actually queried - every repository call this test exercises is
    monkeypatched below to ignore its `cur` argument entirely."""


class _FakeConn:
    def cursor(self):
        return contextlib.nullcontext(_FakeCur())


class _FakeDb:
    def connection(self):
        return contextlib.nullcontext(_FakeConn())


@pytest.fixture(autouse=True)
def _stub_db_and_gee(monkeypatch):
    monkeypatch.setattr(
        report_service, "ForestDefinitionRepository",
        lambda cur: type("_F", (), {"get": staticmethod(lambda: {"canopy_cover_pct": 15.0})})(),
    )
    monkeypatch.setattr(
        report_service, "AnalysisResultRepository",
        lambda cur: type(
            "_A", (), {"get": staticmethod(lambda project_id, aid: _ROWS[aid])}
        )(),
    )
    # No map tile - keeps this suite free of report_map_image/PNG rendering.
    monkeypatch.setattr(report_service, "_compute_cached", lambda *a, **k: (None, None, None))


def _extract_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def _normalized(text: str) -> str:
    """Collapses fpdf2's own line-wrap newlines (inserted mid-sentence inside
    a long multi_cell paragraph, e.g. _AI_DISCLOSURE) to single
    spaces, so a verbatim multi-line paragraph can still be checked as one
    substring - the wrapping is a PDF-layout artifact, not a change to the
    string itself."""
    return " ".join(text.split())


def test_system_report_never_calls_generate_ai_summaries(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("generate_ai_summaries must not be called for report_type=system")

    monkeypatch.setattr(report_service, "generate_ai_summaries", _boom)
    pdf_bytes = report_service.generate_report_pdf_bytes(
        _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
        report_type=ReportType.SYSTEM,
    )
    assert _AI_DISCLOSURE not in _normalized(_extract_text(pdf_bytes))


def test_ai_report_sources_every_summary_from_generate_ai_summaries_and_nothing_else_changes(
    monkeypatch,
):
    captured_sections = {}

    def _fake_generate_ai_summaries(sections, *, project_name=None, **kwargs):
        captured_sections["value"] = sections
        return {aid: {"executive_summary": f"AI summary for {aid}."} for aid, _, _, _ in sections}

    monkeypatch.setattr(report_service, "generate_ai_summaries", _fake_generate_ai_summaries)

    system_pdf = report_service.generate_report_pdf_bytes(
        _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
        report_type=ReportType.SYSTEM,
    )
    ai_pdf = report_service.generate_report_pdf_bytes(
        _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
        report_type=ReportType.AI,
    )

    # generate_ai_summaries got exactly the (analysis_id, name, category, stats)
    # tuples build_section_content itself was built from - one batched call.
    ids = [s[0] for s in captured_sections["value"]]
    assert ids == ["hansen_gfc", "dynamic_world"]
    assert captured_sections["value"][1] == ("dynamic_world", "Dynamic World", "Land Cover", _DW_STATS)

    ai_text = _extract_text(ai_pdf)
    system_text = _extract_text(system_pdf)
    assert "AI summary for dynamic_world." in ai_text
    assert "AI summary for dynamic_world." not in system_text
    assert _AI_DISCLOSURE in _normalized(ai_text)
    assert _AI_DISCLOSURE not in _normalized(system_text)

    # Untouched content: the note (hansen has one), class-breakdown rows and
    # coverage make it into both PDFs identically.
    for shared in (
        "Gain is a whole-period 2000-2012 figure only.",
        "Baseline forest area", "Trees: 20.00 ha", "Coverage: 90.0%",
    ):
        assert shared in ai_text
        assert shared in system_text


def test_ai_and_system_cover_pages_make_different_claims(monkeypatch):
    """R1: the cover-page paragraph must not claim (for an AI report) that
    every section "reproduces the same summary... shown in the live
    application" - that's false for report_type=ai. system keeps its
    existing claim unchanged; ai gets a different, accurate one - not the
    same paragraph with a line appended."""
    monkeypatch.setattr(
        report_service, "generate_ai_summaries",
        lambda sections, *, project_name=None, **k: {
            aid: {"executive_summary": f"AI summary for {aid}."} for aid, _, _, _ in sections
        },
    )
    system_text = _normalized(_extract_text(report_service.generate_report_pdf_bytes(
        _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
        report_type=ReportType.SYSTEM,
    )))
    ai_text = _normalized(_extract_text(report_service.generate_report_pdf_bytes(
        _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
        report_type=ReportType.AI,
    )))

    system_only_claim = "reproduces the same summary, statistics, and methodology text"
    assert system_only_claim in system_text
    assert system_only_claim not in ai_text
    assert "identical to the system-generated report" in ai_text
    assert "NOT what the live application shows" in ai_text


def test_disclosure_names_the_configured_model_and_never_says_validated():
    """R2: the disclosure must name the actual model (a fixed constant,
    never anything from the narrative path) and must not use "validated" -
    that word carries a specific Verra/VVB endorsement meaning."""
    assert GEMINI_MODEL in _AI_DISCLOSURE
    assert "validated" not in _AI_DISCLOSURE.lower()
    for required in (
        "NOT reproducible", "NOT been reviewed or verified",
        "identical to it", "not intended for submission to a validation/verification body",
    ):
        assert required in _AI_DISCLOSURE


def test_get_options_includes_the_formatted_ai_disclosure(monkeypatch):
    """Phase 4: GET .../report/options must expose the exact same disclosure
    paragraph the AI report's cover page renders, pre-formatted with the
    live model - so the frontend never hardcodes a second copy."""
    monkeypatch.setattr(
        report_service, "require_project_view", lambda cur, project_id, user: None,
    )
    monkeypatch.setattr(
        report_service, "ProjectRepository",
        lambda cur: type("_P", (), {"get": staticmethod(lambda project_id: {"name": "Test Project"})})(),
    )
    monkeypatch.setattr(
        report_service, "AnalysisResultRepository",
        lambda cur: type(
            "_A", (), {"list_for_project": staticmethod(lambda project_id: {"hansen_gfc": _NOW})}
        )(),
    )
    project_id = uuid4()
    options = report_service.ReportService(_FakeDb()).get_options(
        project_id, type("_U", (), {"user_id": uuid4(), "role": None})()
    )
    assert options.ai_narrative_disclosure == _AI_DISCLOSURE


def test_ai_narrative_error_propagates_uncaught(monkeypatch):
    def _raise(*a, **k):
        raise AiNarrativeError("section 'dynamic_world' produced an ungrounded number")

    monkeypatch.setattr(report_service, "generate_ai_summaries", _raise)
    with pytest.raises(AiNarrativeError):
        report_service.generate_report_pdf_bytes(
            _FakeDb(), "proj-1", "Test Project", ["hansen_gfc", "dynamic_world"], {},
            report_type=ReportType.AI,
        )
