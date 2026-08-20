"""Renders an already-assembled report (report_content.ReportSection list +
pre-rendered map/chart PNGs) into a single self-contained HTML document via
Jinja2 - the HTML sibling of report_pdf.py's fpdf2 pipeline (Wave: HTML
report rendering). Same "layout only, never composes prose" boundary as
report_pdf.py: this module never talks to GEE/DB/network, and
report_content.py remains the one place that decides what a section's text
actually says.

Both renderers iterate the SAME `report_content.SECTION_PLAN` for section
order/gating so they cannot silently drift apart on which of the 11 sections
appears, in what order, or under what condition - see that module's own
docstring on `SECTION_PLAN`.

Autoescape is always on (`select_autoescape(["html"])`) and no template
under `report_templates/` ever uses the `|safe` filter - every narrative/
legend/project-name string reaching a template (AI-generated text, a
user-entered project name, an analysis category) goes through Jinja2's own
HTML-escaping, never a hand-rolled sanitisation step. This deliberately does
NOT reuse `report_pdf._pdf_safe_text` (the Unicode -> latin-1 ASCII-fallback
downgrade fpdf2's core-font encoding forces) - HTML has no such encoding
constraint, so this path emits real UTF-8 text and lets autoescaping (not a
character-substitution table) be the only safety mechanism."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.domain.enums import ReportType
from app.services.report_content import SECTION_PLAN, ReportSection
from app.services.report_pdf import (
    _COVER_TEXT_AI,
    _COVER_TEXT_SYSTEM,
    AI_NARRATIVE_DISCLOSURE_TEMPLATE,
)

_TEMPLATES_DIR = Path(__file__).parent / "report_templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)

# The "fixed" (always-render) sections' body text, keyed by SECTION_PLAN
# number - report_pdf.py keeps its own identical-by-necessity copy of this
# mapping (see report_content.SectionPlanItem's own docstring for why this
# lives in each renderer rather than in the shared plan).
_FIXED_SECTION_FIELD: dict[int, str] = {
    2: "methodology_text",
    3: "data_processing_text",
    9: "carbon_project_relevance",
    10: "data_quality_text",
    11: "limitations_text",
}


def _format_area(ha: float) -> str:
    return f"{ha:,.2f} ha"


def _split_bullets(text: str) -> list[str]:
    """Splits a `\\n`-joined `key_findings` string into individual bullet
    strings for `<li>` rendering. `key_findings` is stored/plumbed as a
    single `\\n`-joined string SYSTEM-WIDE (ai_narrative.py's Gemini JSON
    contract, its grounding verifier) - changing that shape is out of scope
    here, so this is a small, LOCAL split. Deliberately duplicated rather
    than shared with report_pdf.py's own `_bulleted` (which performs the
    identical split, just to feed fpdf2's `multi_cell` line-by-line instead
    of building an HTML list) - hoisting this into report_content.py would
    make report_content.py own a rendering-shape detail (bullet-list markup)
    it otherwise never owns, the same "order + gate only, never content
    shape" boundary `SECTION_PLAN` itself deliberately keeps thin."""
    return [line.strip() for line in text.split("\n") if line.strip()]


def _b64_png(png: bytes | None) -> str | None:
    if not png:
        return None
    return base64.b64encode(png).decode("ascii")


@dataclass(frozen=True)
class _StatBlockRow:
    label: str
    value: str


def _build_section_blocks(section: ReportSection, chart_png: bytes | None) -> list[dict[str, Any]]:
    """Mirrors `report_pdf.py::_section_page`'s per-item gating exactly,
    iterating the same `SECTION_PLAN` - see that function's own docstring.
    Returns plain dicts (not a further dataclass layer) so `_section.html`
    only ever does `{% if block.kind == ... %}` + simple loops, never any
    business-rule evaluation of its own - all gating/shaping happens here in
    Python, matching this module's own docstring on where that boundary
    sits."""
    blocks: list[dict[str, Any]] = []
    for item in SECTION_PLAN:
        if item.kind == "narrative":
            text = section.narrative.get(item.narrative_key)
            if not text:
                continue
            if item.narrative_key == "key_findings":
                blocks.append(
                    {"kind": "bulleted", "heading": item.heading, "bullets": _split_bullets(text)}
                )
            else:
                blocks.append({"kind": "paragraph", "heading": item.heading, "text": text})

        elif item.kind == "statistics":
            if section.class_breakdown or section.stats_grid:
                class_label = None
                class_rows: list[dict[str, Any]] = []
                if section.class_breakdown:
                    class_label = "Class breakdown" if section.class_breakdown_year is None else (
                        f"Class breakdown ({section.class_breakdown_year})"
                    )
                    class_rows = [
                        {"name": row.name, "area_ha": _format_area(row.area_ha), "color": row.color}
                        for row in section.class_breakdown
                    ]
                stats_label = None
                stats_rows: list[_StatBlockRow] = []
                if section.stats_grid:
                    # Same None-safe "(year)" suffix idiom as report_pdf.py's
                    # own statistics block - see that module's comment on why
                    # a VNV band-index result's `stats_grid_year` is None.
                    stats_label = (
                        "Distribution statistics" if section.stats_grid_year is None
                        else f"Distribution statistics ({section.stats_grid_year})"
                    )
                    stats_rows = [
                        _StatBlockRow(
                            row.label,
                            f"{row.value:.3f}" if row.value is not None else "no data",
                        )
                        for row in section.stats_grid
                    ]
                blocks.append({
                    "kind": "statistics", "heading": item.heading,
                    "class_label": class_label, "class_rows": class_rows,
                    "stats_label": stats_label, "stats_rows": stats_rows,
                })
            if chart_png:
                blocks.append({
                    "kind": "chart", "heading": "Year-series trend",
                    "chart_png_b64": _b64_png(chart_png),
                })

        elif item.kind == "fixed":
            text = getattr(section, _FIXED_SECTION_FIELD[item.number])
            blocks.append({
                "kind": "paragraph", "heading": item.heading, "text": text,
                "small": item.number == 11,
            })
    return blocks


# carbon-mrv-vm0047 review (follow-up, HTML-only scope): the base disclosure
# text (AI_NARRATIVE_DISCLOSURE_TEMPLATE) stays single-sourced from
# report_pdf.py for everything else - see report_html.py's own module
# docstring above on why both renderers must share one copy of that text.
# This one extra sentence is deliberately NOT added to the shared constant,
# because it describes post-download editability, a property that is true of
# an HTML file (openable and alterable in any plain text editor, with no
# specialized tools) and NOT true of the PDF this same constant is also used
# for - folding it into the shared template would make it false every time
# report_pdf.py renders it. Appending it locally, to the same string that
# feeds the single `{{ ai_disclosure }}` template variable (see
# report_templates/_cover.html), keeps it inside the identical styled div as
# the rest of the disclosure - same font-size/emphasis, same paragraph - with
# no second template variable needed.
def _ai_disclosure_html(ai_model: str) -> str:
    base = AI_NARRATIVE_DISCLOSURE_TEMPLATE.format(model=ai_model)
    html_only_note = (
        "This HTML file remains editable after download: its text can be altered using an "
        "ordinary text editor, unlike the PDF version of this report."
    )
    return f"{base} {html_only_note}"


def build_report_html(
    *,
    project_name: str,
    project_id: str,
    generated_at: datetime,
    sections: list[ReportSection],
    map_images: dict[str, bytes],
    chart_images: dict[str, bytes],
    report_type: str = ReportType.SYSTEM,
    ai_model: str | None = None,
) -> bytes:
    """HTML sibling of `report_pdf.build_report_pdf` - same signature shape,
    same section content/gating (`SECTION_PLAN`), same cover-page text
    (imported, never duplicated, from `report_pdf.py` - see that module's
    own R1/R2 comments on why `_COVER_TEXT_SYSTEM`/`_COVER_TEXT_AI`/
    `AI_NARRATIVE_DISCLOSURE_TEMPLATE` must have exactly one source of
    truth). Returns UTF-8-encoded bytes of one self-contained HTML document.

    `map_images`/`chart_images` (PNG bytes, keyed by `analysis_id`, absent
    keys simply render that section without the image - same tolerance as
    the PDF path) are embedded inline as `data:image/png;base64,...` - there
    is no separate file/URL contract for a downloaded report.

    `ai_model` MUST be the caller's own `ai_narrative.GEMINI_MODEL` (a fixed
    constant) - never anything derived from a section's narrative or other
    model-generated content, same requirement `build_report_pdf` documents
    for the identical reason (so the disclosure can't be spoofed to name a
    different model than the one that actually ran). Required whenever
    `report_type="ai"`."""
    if report_type == ReportType.AI:
        assert ai_model, "ai_model is required when report_type=ai"  # noqa: S101

    section_views = []
    for section in sections:
        section_views.append({
            "section": section,
            "map_png_b64": _b64_png(map_images.get(section.analysis_id)),
            "blocks": _build_section_blocks(section, chart_images.get(section.analysis_id)),
        })

    template = _env.get_template("report.html")
    html = template.render(
        project_name=project_name,
        project_id=project_id,
        generated_at=generated_at,
        sections=sections,
        section_views=section_views,
        cover_text=_COVER_TEXT_AI if report_type == ReportType.AI else _COVER_TEXT_SYSTEM,
        is_ai=report_type == ReportType.AI,
        ai_disclosure=(
            _ai_disclosure_html(ai_model)
            if report_type == ReportType.AI else None
        ),
    )
    return html.encode("utf-8")
