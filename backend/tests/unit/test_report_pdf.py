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
        coverage_pct=95.0, description=f"{name} description.", summary=None, note=None,
        disclaimer="Descriptive only - not a forest-definition, eligibility or carbon determination.",
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
        summary="2026: NDVI averages 0.55 across the boundary - moderate.",
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
