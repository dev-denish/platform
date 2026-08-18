"""Deterministic (system-report) equivalent of `ai_narrative.py`'s 5 AI-eligible
narrative fields, for all 13 analysis types - not just the 5 vegetation indices
`index_summary.py` already covers. Pure Python, byte-reproducible from the
stored `stats` dict, same guarantee `index_summary.py`'s own docstring makes
for its text.

Dispatches on the same stats-shape families `report_content.build_section_content`
already recognizes, not on `analysis_id` - a 14th analysis type that reuses one
of these shapes needs no new branch here. For the 5 vegetation indices this
RECOMBINES `index_summary.py`'s existing tested clause functions rather than
duplicating their logic - that module and its R-numbered bug history are
untouched by this wave."""
from __future__ import annotations

from typing import Any

from app.services.index_summary import (
    DESCRIPTIVE_ONLY_TRAILER,
    describe_level,
    describe_sample,
    describe_spatial_outliers,
    describe_trend,
    get_profile,
)
from app.services.report_content import has_change_data, has_temporal_data

__all__ = ["build_system_narrative"]


_NO_SPATIAL_DATA = (
    "This is a single raw scene - no classification or index values are computed "
    "for it, so there is no per-pixel spatial distribution to describe beyond the "
    "scene extent shown on the map above."
)


def _dominant_classes(class_area_ha: dict[str, float], n: int = 2) -> list[tuple[str, float]]:
    return sorted(class_area_ha.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _class_distribution_text(class_area_ha: dict[str, float]) -> str:
    total = sum(class_area_ha.values())
    if total <= 0 or not class_area_ha:
        return "No land-cover classes were recorded for this area."
    top = _dominant_classes(class_area_ha)
    parts = [f"{name} ({area / total * 100:.1f}%)" for name, area in top]
    if len(top) == 1:
        return f"{parts[0]} is the dominant land-cover class in the mapped area."
    return f"{parts[0]} and {parts[1]} are the dominant land-cover classes in the mapped area."


def _hansen_narrative(name: str, stats: dict[str, Any]) -> dict[str, str]:
    baseline = stats["baseline_forest_area_ha"]
    threshold = stats["canopy_cover_threshold_pct"]
    gain = stats["gain_area_ha_2000_2012"]
    loss_by_year = stats.get("loss_area_ha_by_year", {})
    total_loss = sum(loss_by_year.values())

    narrative = {
        "executive_summary": (
            f"{name} found {baseline:,.2f} ha of baseline forest (>{threshold:.0f}% canopy "
            f"cover) within the project boundary, {gain:,.2f} ha of gain over 2000-2012, and "
            f"{total_loss:,.2f} ha of cumulative tree-cover loss across the years recorded. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        ),
        "spatial_distribution": (
            f"Baseline forest ({baseline:,.2f} ha) is the dominant area figure; recorded loss "
            f"({total_loss:,.2f} ha total) is a comparatively small share of that baseline."
            if baseline > 0
            else "No baseline forest area was recorded for this boundary."
        ),
        "key_findings": "\n".join([
            f"Baseline forest area is {baseline:,.2f} ha at the {threshold:.0f}% canopy threshold.",
            f"Gain over the whole 2000-2012 period is {gain:,.2f} ha.",
            f"Cumulative loss across {len(loss_by_year)} recorded year(s) is {total_loss:,.2f} ha.",
        ]),
    }
    if has_change_data(stats):
        peak_year, peak_area = max(loss_by_year.items(), key=lambda kv: kv[1])
        narrative["change_analysis"] = (
            f"Tree-cover loss was recorded in {len(loss_by_year)} year(s), totalling "
            f"{total_loss:,.2f} ha. The largest single-year loss was {peak_area:,.2f} ha in "
            f"{peak_year}. Gain is reported only as a single whole-period figure "
            f"(2000-2012, {gain:,.2f} ha) - the dataset does not track gain by individual year."
        )
    return narrative


def _classified_narrative(
    name: str, class_area_ha: dict[str, float], year_label: str | None,
    by_year: dict[str, dict[str, float]] | None = None,
) -> dict[str, str]:
    total = sum(class_area_ha.values())
    year_phrase = f" for {year_label}" if year_label else ""
    narrative = {
        "executive_summary": (
            f"{name} classified {total:,.2f} ha across {len(class_area_ha)} land-cover "
            f"class(es){year_phrase}. {DESCRIPTIVE_ONLY_TRAILER}"
        ),
        "spatial_distribution": _class_distribution_text(class_area_ha),
        "key_findings": "\n".join(
            f"{cname} covers {area:,.2f} ha ({area / total * 100:.1f}% of the mapped area)."
            if total > 0 else f"{cname}: no area recorded."
            for cname, area in _dominant_classes(class_area_ha, n=3)
        ) or "No land-cover classes were recorded for this area.",
    }
    if by_year and len(by_year) >= 2:
        years = sorted(by_year, key=int)
        first, last = years[0], years[-1]
        first_classes, last_classes = by_year[first], by_year[last]
        changes = []
        for cname in sorted(set(first_classes) | set(last_classes)):
            delta = last_classes.get(cname, 0.0) - first_classes.get(cname, 0.0)
            if abs(delta) > 0:
                changes.append(f"{cname} {'gained' if delta > 0 else 'lost'} {abs(delta):,.2f} ha")
        narrative["temporal_analysis"] = (
            f"Between {first} and {last}: " + (", ".join(changes) + "." if changes else
            "no class recorded a change in area.")
        )
    return narrative


def _browse_narrative(name: str, stats: dict[str, Any]) -> dict[str, str]:
    scene_date = stats.get("scene_date")
    cloud_pct = stats.get("cloud_pct")
    coverage_pct = stats.get("coverage_pct")
    findings = []
    if scene_date:
        findings.append(f"Scene date: {scene_date}.")
    if cloud_pct is not None:
        findings.append(f"Cloud cover: {cloud_pct:.1f}%.")
    if coverage_pct is not None:
        findings.append(f"Boundary coverage: {coverage_pct:.1f}%.")
    return {
        "executive_summary": (
            f"{name} retrieved a single raw scene" + (f" dated {scene_date}" if scene_date else "")
            + f". {DESCRIPTIVE_ONLY_TRAILER}"
        ),
        "spatial_distribution": _NO_SPATIAL_DATA,
        "key_findings": "\n".join(findings) or "No scene metadata was recorded.",
    }


def _veg_index_narrative(analysis_id: str, name: str, stats: dict[str, Any]) -> dict[str, str]:
    series = stats.get("series") or {}
    distribution = stats.get("distribution") or {}
    years = sorted(distribution, key=int)
    year = next(
        (y for y in reversed(years) if (distribution[y] or {}).get("mean") is not None), None
    )
    profile = get_profile(analysis_id)
    if year is None:
        no_data = (
            f"{profile.label} produced no usable pixel statistics for any year attempted. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        )
        return {
            "executive_summary": no_data, "spatial_distribution": no_data, "key_findings": no_data,
        }

    entry = distribution[year] or {}
    hist = entry.get("histogram") or {}
    mean, std_dev = entry.get("mean"), entry.get("std_dev")
    minimum, maximum = entry.get("min"), entry.get("max")
    counts = hist.get("counts")

    level_clause = (
        describe_level(analysis_id, mean) or f"{profile.label} produced no mean for {year}."
    )
    sample_clause = describe_sample(counts)
    executive_summary = f"{year}: {level_clause}" + (f" {sample_clause}" if sample_clause else "")
    executive_summary += f" {DESCRIPTIVE_ONLY_TRAILER}"

    outliers_clause = describe_spatial_outliers(
        analysis_id, minimum, maximum, hist.get("bin_edges"), counts
    )
    spatial_distribution = outliers_clause or (
        f"Pixel values for {year} form a single, unremarkable cluster with no distinct "
        f"low or high tail (spread of about {std_dev:.2f})."
        if std_dev is not None else f"No distribution shape could be determined for {year}."
    )

    findings = [level_clause]
    if sample_clause:
        findings.append(sample_clause)
    if outliers_clause:
        findings.append(outliers_clause)

    narrative = {
        "executive_summary": executive_summary,
        "spatial_distribution": spatial_distribution,
        "key_findings": "\n".join(findings),
    }
    if has_temporal_data(stats):
        trend_clause = describe_trend(analysis_id, series)
        if trend_clause:
            narrative["temporal_analysis"] = trend_clause
    return narrative


def build_system_narrative(
    analysis_id: str, name: str, category: str, stats: dict[str, Any]
) -> dict[str, str]:
    """Returns exactly the keys applicable to this stats shape: always
    `executive_summary`/`spatial_distribution`/`key_findings`, plus
    `temporal_analysis`/`change_analysis` only when `has_temporal_data`/
    `has_change_data` say so - the same gating `report_pdf.py` uses to decide
    whether to render those headings at all."""
    if "canopy_cover_threshold_pct" in stats:
        return _hansen_narrative(name, stats)
    if "series" in stats or "distribution" in stats:
        return _veg_index_narrative(analysis_id, name, stats)
    if "class_area_ha_by_year" in stats:
        by_year = stats["class_area_ha_by_year"]
        latest = max(by_year, key=int)
        return _classified_narrative(name, by_year[latest], latest, by_year)
    if "class_area_ha" in stats:
        return _classified_narrative(name, stats["class_area_ha"], None)
    return _browse_narrative(name, stats)
