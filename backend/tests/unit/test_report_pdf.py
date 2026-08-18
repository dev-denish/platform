"""Unit tests for report_pdf.py + report_charts.py against plain fake
ReportSection objects - no DB/GEE/network. Verifies actual PDF structure via
pypdf (page count, extracted text), not just "didn't raise"."""
from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO

from pypdf import PdfReader

from app.services.report_charts import render_trend_chart_png
from app.services.report_content import ClassRow, ReportSection, StatRow
from app.services.report_pdf import build_report_pdf

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _section(analysis_id: str, name: str, **overrides) -> ReportSection:
    defaults = dict(
        analysis_id=analysis_id, name=name, category="Test Category", computed_at=_NOW,
        coverage_pct=95.0, description=f"{name} description.", note=None,
        disclaimer="Descriptive only - not a forest-definition, eligibility or carbon determination.",
        narrative={
            "executive_summary": "Executive summary text.",
            "spatial_distribution": "Spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
        methodology_text="Methodology text.",
        data_processing_text="Data & processing text.",
        data_quality_text="Data quality text.",
        carbon_project_relevance="Carbon project relevance text.",
        limitations_text="Limitations text.",
    )
    defaults.update(overrides)
    return ReportSection(**defaults)


def _extract_all_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() for page in reader.pages)


def test_report_with_a_single_analysis_has_a_cover_and_one_section():
    section = _section("hansen_gfc", "Global Forest Change (Hansen)",
                        class_breakdown=[ClassRow("Baseline forest area", 10.0, None)])
    pdf_bytes = build_report_pdf(
        project_name="Solo Project", project_id="p1", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    reader = PdfReader(BytesIO(pdf_bytes))
    assert len(reader.pages) >= 2  # cover + at least one section page
    text = _extract_all_text(pdf_bytes)
    assert "Solo Project" in text
    assert "Global Forest Change (Hansen)" in text
    assert "Included analyses (1)" in text


def test_report_with_multiple_analyses_includes_every_one():
    sections = [
        _section("hansen_gfc", "Global Forest Change (Hansen)"),
        _section("dynamic_world", "Dynamic World"),
        _section("esa_worldcover", "ESA WorldCover"),
    ]
    pdf_bytes = build_report_pdf(
        project_name="Multi Project", project_id="p2", generated_at=_NOW,
        sections=sections, map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    assert "Included analyses (3)" in text
    for s in sections:
        assert s.name in text


def test_multi_year_index_report_includes_the_trend_chart_image():
    """Confirms the trend-chart DATA (stats["series"]) actually makes it into
    the PDF as a rendered image, not just that a chart-shaped field exists on
    the section - this is the "full year-series trend, not a single-year
    snapshot" requirement for the 5 vegetation indices."""
    series = {"2023": 0.4, "2024": 0.45, "2025": 0.5, "2026": 0.55}
    section = _section(
        "ndvi", "NDVI",
        stats_grid=[StatRow("Mean", 0.55), StatRow("Variability", 0.1), StatRow("Min", -0.1), StatRow("Max", 0.9)],
        stats_grid_year="2026",
        series=series,
    )
    chart_png = render_trend_chart_png("NDVI", series)
    assert chart_png.startswith(b"\x89PNG")  # a real PNG, not empty/garbage bytes

    pdf_with_chart = build_report_pdf(
        project_name="Index Project", project_id="p3", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={"ndvi": chart_png},
    )
    pdf_without_chart = build_report_pdf(
        project_name="Index Project", project_id="p3", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_with_chart)
    assert "Year-series trend" in text
    assert "Distribution statistics (2026)" in text
    assert "Mean: 0.550" in text
    # The chart image measurably changes the PDF's byte size - proof the PNG
    # was actually embedded, not just that the "Year-series trend" label
    # (present regardless, from section.summary alone) happened to print.
    assert len(pdf_with_chart) > len(pdf_without_chart) + len(chart_png) * 0.5


def test_single_year_analysis_has_no_trend_chart_section():
    """The other side of the multi-year requirement: an analysis with no
    `series` at all must not render an empty/misleading "Year-series trend"
    heading."""
    section = _section("esa_worldcover", "ESA WorldCover",
                        class_breakdown=[ClassRow("Tree cover", 10.0, "#006400")])
    pdf_bytes = build_report_pdf(
        project_name="Snapshot Project", project_id="p4", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    assert "Year-series trend" not in text


def test_missing_map_image_does_not_fail_the_whole_report():
    """A single section's map-tile fetch failing (report_service.py's own
    try/except) must degrade to "no map image for that section", never sink
    report generation entirely."""
    section = _section("hansen_gfc", "Global Forest Change (Hansen)")
    pdf_bytes = build_report_pdf(
        project_name="No Map Project", project_id="p5", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    assert len(pdf_bytes) > 0
    assert "Global Forest Change (Hansen)" in _extract_all_text(pdf_bytes)


# --------------------------------------------------------------------------
# Wave: 11-section report restructure.
# --------------------------------------------------------------------------


def test_all_11_section_headings_appear_in_order_when_every_narrative_key_is_present():
    section = _section(
        "hansen_gfc", "Global Forest Change (Hansen)",
        class_breakdown=[ClassRow("Baseline forest area", 10.0, None)],
        narrative={
            "executive_summary": "Executive summary text.",
            "spatial_distribution": "Spatial distribution text.",
            "temporal_analysis": "Temporal analysis text.",
            "change_analysis": "Change analysis text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )
    pdf_bytes = build_report_pdf(
        project_name="Full Sections Project", project_id="p6", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    headings = [
        "1. Executive Summary", "2. Methodology", "3. Data & Processing", "4. Statistics",
        "5. Spatial Distribution", "6. Temporal Analysis", "7. Change Analysis",
        "8. Key Findings", "9. Carbon Project Relevance", "10. Data Quality", "11. Limitations",
    ]
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)  # every heading present, in this exact order


def test_temporal_and_change_analysis_headings_omitted_when_not_applicable():
    """esa_worldcover-shaped section: no temporal or change data - those two
    headings must not appear at all, not just render empty."""
    section = _section(
        "esa_worldcover", "ESA WorldCover",
        class_breakdown=[ClassRow("Tree cover", 10.0, "#006400")],
        narrative={
            "executive_summary": "Executive summary text.",
            "spatial_distribution": "Spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )
    pdf_bytes = build_report_pdf(
        project_name="Snapshot Project", project_id="p7", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    assert "6. Temporal Analysis" not in text
    assert "7. Change Analysis" not in text
    assert "5. Spatial Distribution" in text
    assert "9. Carbon Project Relevance" in text


def test_the_6_deterministic_sections_always_render_even_with_no_narrative_at_all():
    section = _section(
        "dynamic_world", "Dynamic World", narrative={},
        class_breakdown=[ClassRow("Tree cover", 10.0, "#006400")],
    )
    pdf_bytes = build_report_pdf(
        project_name="Bare Project", project_id="p8", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    for heading in (
        "2. Methodology", "3. Data & Processing", "4. Statistics",
        "9. Carbon Project Relevance", "10. Data Quality", "11. Limitations",
    ):
        assert heading in text
    for heading in ("1. Executive Summary", "5. Spatial Distribution", "8. Key Findings"):
        assert heading not in text


def test_a_character_outside_helvetica_does_not_crash_pdf_assembly():
    """Real dmrv-qa failure (hansen_gfc, 2026-08-12): a real Gemini narrative
    used "≥" (U+2265) - fpdf2's core Helvetica font only supports latin-1
    (ISO-8859-1, NOT cp1252 - see `_pdf_safe_text`'s own docstring) and raises
    on that character, which would otherwise dead-letter the whole report
    over one symbol in one field. Covers a known ASCII fallback (≥), a
    common typographic character outside latin-1 that also needs one (an em
    dash), and a genuinely unanticipated one (an emoji, degrading to "?"
    rather than crashing)."""
    section = _section(
        "hansen_gfc", "Global Forest Change (Hansen)",
        narrative={
            "executive_summary": "Baseline forest with canopy cover ≥15% — a good sign 🌳.",
            "spatial_distribution": "Spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )
    pdf_bytes = build_report_pdf(
        project_name="Unicode Project", project_id="p9", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = _extract_all_text(pdf_bytes)
    assert "canopy cover >=15% - a good sign ?." in text
    assert "≥" not in text
    assert "—" not in text
    assert "🌳" not in text
