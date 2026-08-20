"""Assembles one PDF report section's CONTENT from an already-computed analysis
result - pure Python, no GEE/DB/PDF-library import here, so this is fully
unit-testable and independently verifiable against the live UI's own content
(see AnalysisPanel.jsx's `AnalysisStats` component, which this mirrors field
for field).

Every piece of prose here is taken VERBATIM from the same two backend-sourced
fields the frontend already renders unconditionally and inline - `description`
(app/domain/analysis_catalog.py, the imagery-source citation) and
`stats["note"]` (gee_analysis_service.py, the methodology/dataset-caveat text,
generated once at compute time and stored in the DB row) - never re-derived or
re-worded. DESCRIPTIVE_ONLY_TRAILER is index_summary.py's fixed disclaimer,
appended to every section regardless of analysis type.

Wave: 11-section report restructure. `narrative` (the 5 AI-eligible fields) is
built here by `report_deterministic_narrative.build_system_narrative` for
every analysis type - not just the 5 vegetation indices `index_summary.py`
covers - and is the SYSTEM-report wording by default; `report_service.py`
overwrites it wholesale with AI wording for `report_type=ai`. The other 6
sections (methodology/data-processing/statistics/carbon-relevance/data-
quality/limitations, sourced from `report_fixed_text.py`) are identical
between both report types and never AI-eligible."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.index_summary import DESCRIPTIVE_ONLY_TRAILER
from app.services.report_fixed_text import (
    carbon_project_relevance,
    data_processing_text,
    data_quality_text,
    limitations_text,
    methodology_dict,
    methodology_text,
)

# The 15 catalog ids whose stats carry a `series`/`distribution` year-series -
# see gee_analysis_service.py's `_annual_index_series`, the one function that
# ever populates those two keys. Duplicated as a literal (not imported from
# gee_analysis_service, which pulls in the whole `ee` import chain) - same
# "small, independent constant" convention index_summary.py's own
# INDEX_HISTOGRAM_BIN_WIDTH comment documents for the identical tradeoff.
MULTI_YEAR_INDEX_IDS = frozenset({
    "ndvi", "evi", "savi", "mndwi", "nbr", "ndwi", "gndvi", "ndbi",
    "ndmi", "lswi", "bsi", "arvi", "nddi", "cmri", "psri",
})


def is_multi_year_index(analysis_id: str) -> bool:
    return analysis_id in MULTI_YEAR_INDEX_IDS


def has_temporal_data(stats: dict[str, Any]) -> bool:
    """A real multi-point time series exists in THIS stored result - not a
    per-analysis-id lookup. io_lulc/modis_lulc are multi-year CAPABLE but
    usually stored single-year (the catalog's own `year_mode_default`), so
    only the stats dict actually in hand knows which one this row is; a
    static table could only ever be wrong for one side of that."""
    if "series" in stats:
        return sum(v is not None for v in stats["series"].values()) >= 2
    if "class_area_ha_by_year" in stats:
        return len(stats["class_area_ha_by_year"]) >= 2
    return False


def has_change_data(stats: dict[str, Any]) -> bool:
    """Real annual before/after change data - only hansen_gfc's
    `loss_area_ha_by_year` today."""
    return bool(stats.get("loss_area_ha_by_year"))


@dataclass(frozen=True)
class StatRow:
    label: str
    value: float | None


@dataclass(frozen=True)
class ClassRow:
    name: str
    area_ha: float
    color: str | None


@dataclass(frozen=True)
class ReportSection:
    """Everything one report page/section needs, with no PDF-layout concerns
    mixed in - `report_pdf.py` only lays this out, it never composes text."""

    analysis_id: str
    name: str
    category: str
    computed_at: datetime
    coverage_pct: float | None
    description: str
    note: str | None  # verbatim stats["note"] - consumed internally by limitations_text()
    disclaimer: str
    # The 5 AI-eligible sections (Wave: 11-section report restructure). Keys:
    # executive_summary/spatial_distribution/key_findings always present,
    # temporal_analysis/change_analysis only when has_temporal_data/
    # has_change_data say so - report_pdf.py omits that heading entirely when
    # its key is absent, same truthy-check idiom as every other optional
    # block here. System-report wording by default (report_content.py builds
    # it below); report_service.py overwrites this dict wholesale with the AI
    # wording for report_type=ai.
    narrative: dict[str, str] = field(default_factory=dict)
    # The remaining 6 sections - always populated, never AI-eligible, always
    # identical between system and AI reports.
    methodology_text: str = ""
    data_processing_text: str = ""
    data_quality_text: str = ""
    carbon_project_relevance: str = ""
    limitations_text: str = ""
    # Latest-year Mean/Variability/Min/Max, same 4 labels/values
    # IndexDistribution renders (AnalysisPanel.jsx) - vegetation indices only.
    stats_grid: list[StatRow] = field(default_factory=list)
    stats_grid_year: str | None = None
    # {year: mean}, unmodified from stats["series"] - vegetation indices only,
    # the trend-chart data source.
    series: dict[str, float | None] = field(default_factory=dict)
    # Single-year OR latest-year (multi-year) class -> area breakdown, for the
    # 4 classified land-cover analyses. Hansen's own fields render as a
    # dedicated ClassRow-shaped list too (see _hansen_rows) so the PDF has one
    # shape to lay out regardless of analysis family.
    class_breakdown: list[ClassRow] = field(default_factory=list)
    class_breakdown_year: str | None = None


@dataclass(frozen=True)
class SectionPlanItem:
    """One of the 11 fixed-order sections every analysis type's report page
    renders (Wave: 11-section report restructure, formalised as a shared
    plan for the Wave: HTML report rendering). The single source of truth
    for section NUMBER/HEADING/ORDER and which sections are optional -
    consumed by BOTH `report_pdf.py` and `report_html.py` so the two
    renderers cannot silently drift apart on order or gating.

    Deliberately thin: it owns ONLY order + heading + which field/condition
    gates a section's appearance - never a section's internal content SHAPE
    (e.g. section 4/Statistics' table-vs-list layout, or which exact
    `ReportSection` attribute backs a "fixed" section's body text). Each
    renderer keeps its own small, renderer-specific mapping for that (see
    `report_pdf.py`'s and `report_html.py`'s own `_FIXED_SECTION_FIELD`) -
    this is not a second content-shaping layer on top of `ReportSection`.

    `kind`: "narrative" sections gate on `section.narrative.get(narrative_key)`
    truthiness (empty/absent -> heading omitted entirely, never rendered
    empty); "fixed" sections always render (`always_render=True`); the one
    "statistics" section (4) has its own compound gate
    (`class_breakdown or stats_grid`), read directly by each renderer rather
    than modelled as a single boolean here."""

    number: int
    heading: str
    kind: str  # "narrative" | "fixed" | "statistics"
    narrative_key: str | None = None
    always_render: bool = True


SECTION_PLAN: tuple[SectionPlanItem, ...] = (
    SectionPlanItem(
        1, "1. Executive Summary", "narrative", "executive_summary", always_render=False
    ),
    SectionPlanItem(2, "2. Methodology", "fixed"),
    SectionPlanItem(3, "3. Data & Processing", "fixed"),
    SectionPlanItem(4, "4. Statistics", "statistics", always_render=False),
    SectionPlanItem(
        5, "5. Spatial Distribution", "narrative", "spatial_distribution", always_render=False
    ),
    SectionPlanItem(
        6, "6. Temporal Analysis", "narrative", "temporal_analysis", always_render=False
    ),
    SectionPlanItem(7, "7. Change Analysis", "narrative", "change_analysis", always_render=False),
    SectionPlanItem(8, "8. Key Findings", "narrative", "key_findings", always_render=False),
    SectionPlanItem(9, "9. Carbon Project Relevance", "fixed"),
    SectionPlanItem(10, "10. Data Quality", "fixed"),
    SectionPlanItem(11, "11. Limitations", "fixed"),
)


def _legend_color(legend: list[dict[str, Any]] | None, class_name: str) -> str | None:
    for entry in legend or []:
        if entry.get("name") == class_name:
            return entry.get("color")
    return None


def _class_breakdown_rows(
    class_area_ha: dict[str, float], legend: list[dict[str, Any]] | None
) -> list[ClassRow]:
    return [
        ClassRow(name=name, area_ha=area, color=_legend_color(legend, name))
        for name, area in class_area_ha.items()
    ]


def _hansen_rows(stats: dict[str, Any]) -> list[ClassRow]:
    """Hansen has no `class_area_ha` at all (see gee_analysis_service.py's
    `_hansen_forest_change`) - its own fields (baseline/gain/loss-by-year) are
    reshaped into the same ClassRow list every other classified analysis uses,
    so report_pdf.py lays out exactly one "labelled area" table shape rather
    than a Hansen-specific one. Values are the exact same numbers
    HansenStats/AnalysisPanel.jsx already displays, just relabelled rows."""
    rows = [
        ClassRow(
            name=f"Baseline forest area (>{stats['canopy_cover_threshold_pct']:.0f}% canopy cover)",
            area_ha=stats["baseline_forest_area_ha"],
            color=None,
        ),
        ClassRow(name="Gain, 2000-2012", area_ha=stats["gain_area_ha_2000_2012"], color=None),
    ]
    for year, area in sorted(stats.get("loss_area_ha_by_year", {}).items()):
        rows.append(ClassRow(name=f"Loss, {year}", area_ha=area, color=None))
    return rows


def build_section_content(
    catalog_entry: dict[str, Any], analysis_id: str, computed_at: datetime, stats: dict[str, Any],
    legend: list[dict[str, Any]] | None,
) -> ReportSection:
    """Mirrors AnalysisStats' own render order (AnalysisPanel.jsx) field for
    field: coverage -> summary -> Hansen/class breakdown -> trend -> per-year
    distribution stats -> note -> (added here) the fixed disclaimer every
    section gets regardless of what fired above."""
    stats_grid: list[StatRow] = []
    stats_grid_year: str | None = None
    series: dict[str, float | None] = {}
    class_breakdown: list[ClassRow] = []
    class_breakdown_year: str | None = None

    if "canopy_cover_threshold_pct" in stats:
        class_breakdown = _hansen_rows(stats)
    elif "class_area_ha" in stats:
        class_breakdown = _class_breakdown_rows(stats["class_area_ha"], legend)
    elif "class_area_ha_by_year" in stats:
        by_year = stats["class_area_ha_by_year"]
        class_breakdown_year = max(by_year, key=int)
        class_breakdown = _class_breakdown_rows(by_year[class_breakdown_year], legend)

    if "series" in stats:
        series = stats["series"]

    if "index" in stats:
        # VNV Pipeline band-index result (Wave: VNV band indices, carbon-mrv
        # -vm0047 fix). ONE rolling 90-day trailing-window composite, no year
        # axis - `stats["distribution"]` here is keyed "latest", not a year
        # string, so this branch reads it directly rather than falling into
        # the `"distribution" in stats` branch below, whose
        # `sorted(..., key=int)` would crash outright on a non-numeric key
        # (see vnv_analysis_jobs.py's own comment on this shape). Checked
        # BEFORE that branch for exactly that reason.
        latest = (stats.get("distribution") or {}).get("latest") or {}
        stats_grid_year = None
        stats_grid = [
            StatRow("Mean", latest.get("mean")),
            StatRow("Variability", latest.get("std_dev")),
            StatRow("Min", latest.get("min")),
            StatRow("Max", latest.get("max")),
        ]
    elif "distribution" in stats:
        years = sorted(stats["distribution"], key=int)
        stats_grid_year = years[-1] if years else None
        year_stats = stats["distribution"].get(stats_grid_year) or {} if stats_grid_year else {}
        # Same 4 labels IndexDistribution uses - "Variability" is the UI's
        # label for the backend's `std_dev` field, not a renamed field.
        stats_grid = [
            StatRow("Mean", year_stats.get("mean")),
            StatRow("Variability", year_stats.get("std_dev")),
            StatRow("Min", year_stats.get("min")),
            StatRow("Max", year_stats.get("max")),
        ]

    # Deferred import: report_deterministic_narrative.py imports
    # has_temporal_data/has_change_data FROM this module, so a top-level
    # import here would be circular - same reasoning as
    # report_service.py's own deferred import of report_jobs.
    from app.services.report_deterministic_narrative import build_system_narrative

    note = stats.get("note")
    methodology = stats.get("methodology")
    out_of_range = None
    if stats_grid_year and "distribution" in stats:
        out_of_range = (stats["distribution"].get(stats_grid_year) or {}).get(
            "out_of_range_pixel_count"
        )

    return ReportSection(
        analysis_id=analysis_id,
        name=catalog_entry["name"],
        category=catalog_entry["category"],
        computed_at=computed_at,
        coverage_pct=stats.get("coverage_pct"),
        description=catalog_entry["description"],
        note=note,
        disclaimer=DESCRIPTIVE_ONLY_TRAILER,
        narrative=build_system_narrative(
            analysis_id, catalog_entry["name"], catalog_entry["category"], stats
        ),
        methodology_text=methodology_text(analysis_id, catalog_entry["description"], methodology),
        data_processing_text=data_processing_text(analysis_id, methodology),
        data_quality_text=data_quality_text(
            stats.get("coverage_pct"),
            out_of_range_pixel_count=out_of_range,
            # Resolved through `methodology_dict` (real `stats["methodology"]`
            # if present, else `METHODOLOGY_FALLBACK`) - NOT the raw
            # `methodology` local above. VNV's real stats never carry a
            # `methodology` key at all, so the raw check was always False for
            # every VNV analysis even after `METHODOLOGY_FALLBACK` gained a
            # `cloud_masking` entry for them - this must resolve the same
            # fallback `methodology_text`/`data_processing_text` already do.
            has_cloud_masking="cloud_masking" in methodology_dict(analysis_id, methodology),
        ),
        carbon_project_relevance=carbon_project_relevance(analysis_id),
        limitations_text=limitations_text(analysis_id, note),
        stats_grid=stats_grid,
        stats_grid_year=stats_grid_year,
        series=series,
        class_breakdown=class_breakdown,
        class_breakdown_year=class_breakdown_year,
    )
