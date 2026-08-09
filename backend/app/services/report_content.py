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
re-worded. `stats["summary"]` (vegetation indices only) is the same
deterministic clause-generator paragraph from app/services/index_summary.py.
DESCRIPTIVE_ONLY_TRAILER is that same module's fixed disclaimer, appended to
every section regardless of analysis type (not just the ones whose summary
happens to already end with it)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.services.index_summary import DESCRIPTIVE_ONLY_TRAILER

# The 5 catalog ids whose stats carry a `series`/`distribution` year-series -
# see gee_analysis_service.py's `_annual_index_series`, the one function that
# ever populates those two keys. Duplicated as a literal (not imported from
# gee_analysis_service, which pulls in the whole `ee` import chain) - same
# "small, independent constant" convention index_summary.py's own
# INDEX_HISTOGRAM_BIN_WIDTH comment documents for the identical tradeoff.
MULTI_YEAR_INDEX_IDS = frozenset({"ndvi", "evi", "savi", "mndwi", "nbr"})


def is_multi_year_index(analysis_id: str) -> bool:
    return analysis_id in MULTI_YEAR_INDEX_IDS


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
    summary: str | None  # verbatim stats["summary"] - vegetation indices only
    note: str | None  # verbatim stats["note"]
    disclaimer: str
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

    if "distribution" in stats:
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

    return ReportSection(
        analysis_id=analysis_id,
        name=catalog_entry["name"],
        category=catalog_entry["category"],
        computed_at=computed_at,
        coverage_pct=stats.get("coverage_pct"),
        description=catalog_entry["description"],
        summary=stats.get("summary"),
        note=stats.get("note"),
        disclaimer=DESCRIPTIVE_ONLY_TRAILER,
        stats_grid=stats_grid,
        stats_grid_year=stats_grid_year,
        series=series,
        class_breakdown=class_breakdown,
        class_breakdown_year=class_breakdown_year,
    )
