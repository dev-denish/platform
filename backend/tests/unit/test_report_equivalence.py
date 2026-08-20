"""PDF/HTML output-equivalence tests (Wave: HTML report rendering) - given
the SAME `ReportSection` input, the two renderers must show the same
numbers and the same never-AI-eligible fixed text, even though their
underlying markup/formatting machinery differs.

Two things are checked, deliberately NOT via raw-substring comparison (the
two renderers are allowed to differ in whitespace/line-wrapping/markup):

1. Every stat value (`stats_grid`) and class-breakdown area number
   (`class_breakdown`) that appears in the PDF also appears in the HTML,
   compared as PARSED floats (not formatted strings) - see this module's own
   `_extract_stat_value`/`_extract_area_value` helpers.
2. The five never-AI-eligible fixed-text sections (2/3/9/10/11 -
   methodology_text/data_processing_text/carbon_project_relevance/
   data_quality_text/limitations_text) render IDENTICAL content in both
   outputs for the same section, once whitespace is normalised (PDF
   line-wraps text that HTML does not, but must never MODIFY it).

Each section is rendered as its own single-section document (not combined
into one multi-section report) - the 11 heading strings otherwise repeat
once per section in a real multi-section report, which would make
heading-based text slicing ambiguous. That has no bearing on the "true"
report_service.py path, which always assembles all sections into one
document; the layout logic under test here is identical either way (see
`report_pdf._section_page`/`report_html._build_section_blocks`, both of
which lay out one section at a time regardless of how many sections a
document has)."""
from __future__ import annotations

import re

from app.services.report_content import ClassRow, StatRow
from app.services.report_html import build_report_html
from app.services.report_pdf import build_report_pdf
from tests.unit.test_report_pdf import _NOW, _extract_all_text, _section
from tests.unit.test_report_rendering_contract import _extract_html_text

_FIXED_HEADINGS_IN_ORDER = [
    "1. Executive Summary", "2. Methodology", "3. Data & Processing", "4. Statistics",
    "5. Spatial Distribution", "6. Temporal Analysis", "7. Change Analysis",
    "8. Key Findings", "9. Carbon Project Relevance", "10. Data Quality", "11. Limitations",
]

_FIXED_TEXT_HEADINGS = [
    "2. Methodology", "3. Data & Processing", "9. Carbon Project Relevance",
    "10. Data Quality", "11. Limitations",
]


def _sections_by_heading(text: str, headings: list[str]) -> dict[str, str]:
    """Slices `text` into {heading: body_up_to_next_present_heading}, for
    whichever of `headings` actually occur in it - order-independent (sorts
    by position found), so it works for either renderer's own heading order
    (both should already agree, but this doesn't assume it)."""
    present = sorted(((h, text.index(h)) for h in headings if h in text), key=lambda t: t[1])
    out: dict[str, str] = {}
    for i, (heading, pos) in enumerate(present):
        start = pos + len(heading)
        end = present[i + 1][1] if i + 1 < len(present) else len(text)
        out[heading] = text[start:end]
    return out


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _extract_stat_value(text: str, label: str) -> float:
    match = re.search(re.escape(label) + r":\s*(-?[\d,]+\.\d+)", text)
    assert match, f"stat label {label!r} not found in text:\n{text}"
    return float(match.group(1).replace(",", ""))


def _extract_area_value(text: str, class_name: str) -> float:
    match = re.search(re.escape(class_name) + r":\s*(-?[\d,]+\.\d+)\s*ha", text)
    assert match, f"class row {class_name!r} not found in text:\n{text}"
    return float(match.group(1).replace(",", ""))


def _render_both(section) -> tuple[str, str]:
    pdf_bytes = build_report_pdf(
        project_name="Equivalence Project", project_id="p-equiv", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    html_bytes = build_report_html(
        project_name="Equivalence Project", project_id="p-equiv", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    return _extract_all_text(pdf_bytes), _extract_html_text(html_bytes)


def _index_style_section():
    return _section(
        "ndvi", "NDVI",
        stats_grid=[
            StatRow("Mean", 0.552), StatRow("Variability", 0.081),
            StatRow("Min", -0.123), StatRow("Max", 0.941),
        ],
        stats_grid_year="2026",
        series={"2023": 0.4, "2024": 0.45, "2025": 0.5, "2026": 0.552},
        narrative={
            "executive_summary": "NDVI executive summary text.",
            "spatial_distribution": "NDVI spatial distribution text.",
            "temporal_analysis": "NDVI temporal analysis text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )


def _classified_style_section():
    return _section(
        "esa_worldcover", "ESA WorldCover",
        class_breakdown=[
            ClassRow("Tree cover", 1234.56, "#006400"),
            ClassRow("Bare soil", 78.9, "#c2a76a"),
            ClassRow("Water", 3.25, "#0064c8"),
        ],
        narrative={
            "executive_summary": "Land cover executive summary text.",
            "spatial_distribution": "Land cover spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )


def test_stat_grid_values_match_between_pdf_and_html():
    section = _index_style_section()
    pdf_text, html_text = _render_both(section)
    for row in section.stats_grid:
        assert row.value is not None
        pdf_value = _extract_stat_value(pdf_text, row.label)
        html_value = _extract_stat_value(html_text, row.label)
        assert abs(pdf_value - html_value) < 1e-6, (
            f"{row.label}: pdf={pdf_value} html={html_value}"
        )
        assert abs(pdf_value - row.value) < 1e-3  # both renderers round to 3dp


def test_class_breakdown_area_values_match_between_pdf_and_html():
    section = _classified_style_section()
    pdf_text, html_text = _render_both(section)
    for row in section.class_breakdown:
        pdf_value = _extract_area_value(pdf_text, row.name)
        html_value = _extract_area_value(html_text, row.name)
        assert abs(pdf_value - html_value) < 1e-6, (
            f"{row.name}: pdf={pdf_value} html={html_value}"
        )
        assert abs(pdf_value - row.area_ha) < 1e-2  # both renderers round to 2dp


def test_fixed_text_sections_are_identical_content_between_pdf_and_html():
    """Sections 2/3/9/10/11 - never AI-eligible, must be byte-for-byte the
    same PROSE (not layout) in both renderers."""
    for section in (_index_style_section(), _classified_style_section()):
        pdf_text, html_text = _render_both(section)
        pdf_blocks = _sections_by_heading(pdf_text, _FIXED_HEADINGS_IN_ORDER)
        html_blocks = _sections_by_heading(html_text, _FIXED_HEADINGS_IN_ORDER)
        for heading in _FIXED_TEXT_HEADINGS:
            assert heading in pdf_blocks, f"{heading} missing from PDF for {section.analysis_id}"
            assert heading in html_blocks, f"{heading} missing from HTML for {section.analysis_id}"
            pdf_body = _normalize_ws(pdf_blocks[heading])
            html_body = _normalize_ws(html_blocks[heading])
            # Both bodies also carry the trailing `section.disclaimer` text
            # appended right after them (neither renderer puts a heading
            # between the last fixed section and the disclaimer) - strip it
            # off both sides identically before comparing so this test
            # verifies the FIXED-TEXT content, not the shared disclaimer.
            disclaimer = _normalize_ws(section.disclaimer)
            if heading == "11. Limitations":
                assert pdf_body.endswith(disclaimer)
                assert html_body.endswith(disclaimer)
                pdf_body = pdf_body[: -len(disclaimer)].strip()
                html_body = html_body[: -len(disclaimer)].strip()
            assert pdf_body == html_body, f"{heading} content differs for {section.analysis_id}"
