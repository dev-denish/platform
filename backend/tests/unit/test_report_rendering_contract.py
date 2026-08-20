"""Cross-renderer contract tests (Wave: HTML report rendering) - the same
section-heading-order/gating assertions `test_report_pdf.py` already locked
down for `report_pdf.build_report_pdf`, run AGAINST BOTH renderers with the
SAME fake `ReportSection` fixtures, so `report_pdf.py` and `report_html.py`
cannot silently drift apart on section order or optional-section gating.

Both renderers already claim to iterate the one shared
`report_content.SECTION_PLAN` (see that module's own docstring) - these
tests are the proof, not just the claim.

Text extraction:
- PDF: reuses the same `pypdf`-based `_extract_all_text` helper
  `test_report_pdf.py` already has (pypdf is a pinned dev dependency for
  exactly this purpose).
- HTML: a minimal local tag-strip (`re.sub(r"<[^>]+>", " ", html)`) - no new
  dependency (`beautifulsoup4` is not in `pyproject.toml`'s `dev` extra;
  checked before choosing this approach).

Also includes the escaping regression test (task 3): proof that Jinja2
autoescaping is actually wired end-to-end for `report_html.build_report_html`,
not just configured on the `Environment`."""
from __future__ import annotations

import re

import pytest

from app.services.report_content import ClassRow
from app.services.report_html import build_report_html
from app.services.report_pdf import build_report_pdf
from tests.unit.test_report_pdf import _NOW, _extract_all_text, _section

# Both renderers share this exact call signature (see report_html.py's own
# docstring: "same signature shape" as report_pdf.build_report_pdf), so one
# parametrised list of (build fn, text-extraction fn) drives every test here.


def _extract_html_text(html_bytes: bytes) -> str:
    """Strips tags for a plain-text-style substring/order assertion - not a
    real HTML parser, deliberately: this only needs to answer "does heading A
    appear before heading B in the rendered text", not validate markup."""
    html = html_bytes.decode("utf-8")
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse the printable HTML entities the templates might emit for plain
    # ASCII punctuation so a stripped comparison isn't tripped up by them.
    text = text.replace("&amp;", "&").replace("&nbsp;", " ")
    return text


_RENDERERS = pytest.mark.parametrize(
    "build_fn, extract_fn",
    [
        pytest.param(build_report_pdf, _extract_all_text, id="pdf"),
        pytest.param(build_report_html, _extract_html_text, id="html"),
    ],
)


@_RENDERERS
def test_all_11_section_headings_appear_in_order_when_every_narrative_key_is_present(
    build_fn, extract_fn,
):
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
    output = build_fn(
        project_name="Full Sections Project", project_id="p6", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = extract_fn(output)
    headings = [
        "1. Executive Summary", "2. Methodology", "3. Data & Processing", "4. Statistics",
        "5. Spatial Distribution", "6. Temporal Analysis", "7. Change Analysis",
        "8. Key Findings", "9. Carbon Project Relevance", "10. Data Quality", "11. Limitations",
    ]
    positions = [text.index(h) for h in headings]
    assert positions == sorted(positions)  # every heading present, in this exact order


@_RENDERERS
def test_temporal_and_change_analysis_headings_omitted_when_not_applicable(build_fn, extract_fn):
    """esa_worldcover-shaped section: no temporal or change data - those two
    headings must not appear at all, not just render empty, in EITHER
    renderer's output."""
    section = _section(
        "esa_worldcover", "ESA WorldCover",
        class_breakdown=[ClassRow("Tree cover", 10.0, "#006400")],
        narrative={
            "executive_summary": "Executive summary text.",
            "spatial_distribution": "Spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )
    output = build_fn(
        project_name="Snapshot Project", project_id="p7", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = extract_fn(output)
    assert "6. Temporal Analysis" not in text
    assert "7. Change Analysis" not in text
    assert "5. Spatial Distribution" in text
    assert "9. Carbon Project Relevance" in text


@_RENDERERS
def test_the_6_deterministic_sections_always_render_even_with_no_narrative_at_all(
    build_fn, extract_fn,
):
    section = _section(
        "dynamic_world", "Dynamic World", narrative={},
        class_breakdown=[ClassRow("Tree cover", 10.0, "#006400")],
    )
    output = build_fn(
        project_name="Bare Project", project_id="p8", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    text = extract_fn(output)
    for heading in (
        "2. Methodology", "3. Data & Processing", "4. Statistics",
        "9. Carbon Project Relevance", "10. Data Quality", "11. Limitations",
    ):
        assert heading in text
    for heading in ("1. Executive Summary", "5. Spatial Distribution", "8. Key Findings"):
        assert heading not in text


# --------------------------------------------------------------------------
# Task 3: Jinja2 autoescaping end-to-end regression test (HTML only - there
# is no analogous injection surface in the PDF path, which never interprets
# markup in its text at all).
# --------------------------------------------------------------------------

_XSS_PAYLOAD = "<script>alert(1)</script>"


def test_build_report_html_escapes_a_script_payload_in_the_section_name():
    section = _section("evil_1", f"Evil Section {_XSS_PAYLOAD}")
    html_bytes = build_report_html(
        project_name="Escaping Project", project_id="p-esc-1", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    html = html_bytes.decode("utf-8")
    assert "&lt;script&gt;" in html
    assert _XSS_PAYLOAD not in html


def test_build_report_html_escapes_a_script_payload_in_executive_summary_narrative():
    section = _section(
        "evil_2", "Evil Section",
        narrative={
            "executive_summary": f"Findings include {_XSS_PAYLOAD} in the data.",
            "spatial_distribution": "Spatial distribution text.",
            "key_findings": "Finding one.\nFinding two.",
        },
    )
    html_bytes = build_report_html(
        project_name="Escaping Project", project_id="p-esc-2", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    html = html_bytes.decode("utf-8")
    assert "&lt;script&gt;" in html
    assert _XSS_PAYLOAD not in html


def test_build_report_html_escapes_a_script_payload_in_a_class_row_name():
    section = _section(
        "evil_3", "Evil Section",
        class_breakdown=[ClassRow(f"Tree cover {_XSS_PAYLOAD}", 10.0, "#006400")],
    )
    html_bytes = build_report_html(
        project_name="Escaping Project", project_id="p-esc-3", generated_at=_NOW,
        sections=[section], map_images={}, chart_images={},
    )
    html = html_bytes.decode("utf-8")
    assert "&lt;script&gt;" in html
    assert _XSS_PAYLOAD not in html
