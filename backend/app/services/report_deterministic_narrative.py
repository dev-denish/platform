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
    _MODEST_SAMPLE_PIXELS,
    _PIXEL_AREA_HA,
    _THIN_SAMPLE_PIXELS,
    describe_level,
    describe_sample,
    describe_spatial_outliers,
    describe_trend,
    describe_variability,
    get_profile,
)
from app.services.report_content import has_change_data, has_temporal_data

__all__ = ["build_system_narrative"]


_NO_SPATIAL_DATA = (
    "This is a single raw scene - no classification or index values are computed "
    "for it, so there is no per-pixel spatial distribution to describe beyond the "
    "scene extent shown on the map above."
)

_NO_NARRATIVE_AVAILABLE = (
    "No system narrative is available for this analysis type's stored result shape."
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


def _no_narrative(name: str) -> dict[str, str]:
    """Neutral fallback for a stats shape none of the branches above (or
    `_browse_narrative`'s own, now-gated check) recognize - a 14th analysis
    type landing here should read as "nothing generated", never silently get
    mislabeled with a wrong (e.g. "single raw scene") narrative the way VNV
    band-index results used to fall through to `_browse_narrative` before
    this fix. See `build_system_narrative`'s own comment on the dispatch
    order this depends on."""
    text = f"{name}: {_NO_NARRATIVE_AVAILABLE}"
    return {"executive_summary": text, "spatial_distribution": text, "key_findings": text}


# carbon-mrv-vm0047 review (M3): VNV's PSRI is NOT the same formula as GEE's
# PSRI - it uses the standard NIR band as its denominator (this pipeline's
# 6-band raster has no red-edge band available at all), where the Earth
# Engine version uses the red-edge band (B6) Merzlyak et al. (1999)'s own
# reading is anchored to (see report_fixed_text.py's own vnv_psri limitations
# entry). `describe_level("psri", mean)` below still pulls that red-edge-
# anchored reading text (index_summary.py has no separate VNV-specific PSRI
# profile), so without a qualifier a vnv_psri report's own Executive Summary/
# Key Findings would silently attribute a literature reading to a formula its
# own Limitations section (11) says is different - a direct internal
# contradiction in one document.
_VNV_PSRI_QUALIFIER = (
    " Note: this reading follows Merzlyak et al. (1999)'s red-edge PSRI convention, but "
    "the VNV Pipeline computes PSRI on the standard NIR band instead - a different "
    "denominator - so the direction is indicative only and the value is not comparable "
    "with the Earth Engine PSRI elsewhere in this catalog."
)


def _vnv_sample_clause(valid_pixel_count: int | None, coverage_pct: float | None) -> str | None:
    """carbon-mrv-vm0047 review (M4): the most material of the 4 findings -
    `_vnv_band_index_narrative` used to build its executive summary from
    `mean`/`std_dev`/`scene_count` alone and never mentioned
    `valid_pixel_count`/`coverage_pct`, even though `vnv_analysis_jobs.py`'s
    stats dict already carries both - so a report could say "NDVI averages
    0.62 - high, dense canopy" with zero indication the mean rests on a
    small fraction of the boundary after masking. Mirrors
    `index_summary.describe_sample`'s own thin/modest-sample framing (same
    `_THIN_SAMPLE_PIXELS`/`_MODEST_SAMPLE_PIXELS`/`_PIXEL_AREA_HA` constants,
    reused rather than re-invented) but also folds in `coverage_pct`, which
    that function's GEE callers don't have available in the same call the
    way this one does."""
    if not valid_pixel_count:
        return (
            "No valid pixels remained inside the project boundary after cloud masking, "
            "so there is nothing to describe here - treat this window as missing, not as zero."
        )
    ha = valid_pixel_count * _PIXEL_AREA_HA
    base = f"Based on {valid_pixel_count:,} valid pixels (about {ha:,.2f} ha at 10 m)"
    if coverage_pct is not None:
        base += f", {coverage_pct:.1f}% of the project boundary"
    if valid_pixel_count < _THIN_SAMPLE_PIXELS:
        return base + " - too thin a sample to carry into any reporting."
    if valid_pixel_count < _MODEST_SAMPLE_PIXELS or (coverage_pct is not None and coverage_pct < 50):
        return base + " - fine for a visual read, thin for any claim about the whole boundary."
    return base + "."


def _vnv_band_index_narrative(name: str, stats: dict[str, Any]) -> dict[str, str]:
    """VNV Pipeline band-index result (Wave: VNV band indices, carbon-mrv
    -vm0047 report-generation fix). ONE per-pixel band-math result over a
    rolling 90-day, up-to-4-scene, least-cloud-first Sentinel-2 composite -
    the opposite of `_browse_narrative`'s "a single raw scene" framing this
    used to fall through to (that function's stats shape - flat min/max/mean/
    valid_pixel_count/total_pixel_count/coverage_pct/scene_count/note -
    matches none of `_hansen_narrative`/`_veg_index_narrative`/
    `_classified_narrative`'s recognized shapes, so it used to hit that
    function's fallback unconditionally).

    Deliberately NOT routed through `_veg_index_narrative` even though the
    job now emits a `stats["distribution"]` dict in that same shape (see
    vnv_analysis_jobs.py) - that function's `sorted(distribution, key=int)`
    assumes every key is a year string and would crash outright on this
    shape's "latest" key, and its wording ("2026: NDVI averages...") has no
    year to report in the first place. Reuses `index_summary.py`'s own
    clause builders (`describe_level`/`describe_variability`), keyed on the
    bare index name (`vnv_` prefix stripped) - the underlying formula and its
    literature-sourced reading text are the SAME regardless of which compute
    path produced the number for every VNV band index EXCEPT PSRI (see
    `_VNV_PSRI_QUALIFIER`'s own comment - VNV's PSRI genuinely uses a
    different band than GEE's, not just a different compute path); only the
    DATA behind the other 11 differs (a fixed 90-day composite, no
    caller-choosable season/year, SCL-based rather than Cloud Score+
    masking), which this text states explicitly rather than silently reusing
    GEE framing."""
    bare_id = str(stats.get("index", "")).removeprefix("vnv_")
    try:
        profile = get_profile(bare_id)
    except ValueError:
        # S7 hardening (carbon-mrv-vm0047 review): "index" in stats is
        # presumed unique to run_vnv_band_index_analysis's own stats dict,
        # but run_vnv_ndfi_analysis's stats are built entirely from an
        # external R sidecar's JSON payload (see vnv_analysis_jobs.py) - if a
        # future sidecar version ever emits its own key named "index",
        # dispatch in build_system_narrative would misroute that result here
        # and get_profile("ndfi") would raise ValueError, crashing report
        # generation entirely rather than just mislabeling one section.
        # Degrade to the same neutral message _no_narrative gives any other
        # unrecognized shape.
        return _no_narrative(name)

    latest = (stats.get("distribution") or {}).get("latest") or {}
    mean = latest.get("mean")
    std_dev = latest.get("std_dev")
    scene_count = stats.get("scene_count")
    valid_pixel_count = stats.get("valid_pixel_count")
    coverage_pct = stats.get("coverage_pct")

    if mean is None:
        no_data = (
            f"{profile.label} (VNV Pipeline band math) produced no usable pixel statistics "
            f"for the current 90-day trailing composite. {DESCRIPTIVE_ONLY_TRAILER}"
        )
        return {
            "executive_summary": no_data, "spatial_distribution": no_data, "key_findings": no_data,
        }

    level_clause = describe_level(bare_id, mean) or f"{profile.label} produced no mean."
    if bare_id == "psri":
        level_clause += _VNV_PSRI_QUALIFIER
    sample_clause = _vnv_sample_clause(valid_pixel_count, coverage_pct)
    scene_clause = (
        f"Computed from a rolling 90-day trailing Sentinel-2 composite "
        f"({scene_count} scene(s), least-cloud-first)."
        if scene_count is not None else ""
    )
    executive_summary = level_clause
    if sample_clause:
        executive_summary += f" {sample_clause}"
    if scene_clause:
        executive_summary += f" {scene_clause}"
    executive_summary += f" {DESCRIPTIVE_ONLY_TRAILER}"

    variability_clause = describe_variability(std_dev)
    spatial_distribution = variability_clause or (
        "No distribution shape could be determined for the current window."
    )

    findings = [level_clause]
    if sample_clause:
        findings.append(sample_clause)
    if scene_clause:
        findings.append(scene_clause)
    if variability_clause:
        findings.append(variability_clause)

    return {
        "executive_summary": executive_summary,
        "spatial_distribution": spatial_distribution,
        "key_findings": "\n".join(findings),
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
    # Checked BEFORE "series"/"distribution" below: VNV band-index stats also
    # carry a `stats["distribution"]` dict (Wave: VNV band indices, carbon-
    # mrv-vm0047 report-generation fix - see vnv_analysis_jobs.py), but keyed
    # "latest" rather than a year string, and `_veg_index_narrative` would
    # crash on that key (`sorted(distribution, key=int)`). `"index" in stats`
    # is a marker unique to that job's own stats dict (set once, right
    # alongside `distribution` - see its own comment) - no GEE stats dict
    # ever has an `"index"` key at the top level.
    if "index" in stats:
        return _vnv_band_index_narrative(name, stats)
    if "series" in stats or "distribution" in stats:
        return _veg_index_narrative(analysis_id, name, stats)
    if "class_area_ha_by_year" in stats:
        by_year = stats["class_area_ha_by_year"]
        latest = max(by_year, key=int)
        return _classified_narrative(name, by_year[latest], latest, by_year)
    if "class_area_ha" in stats:
        return _classified_narrative(name, stats["class_area_ha"], None)
    # Positive gate (carbon-mrv-vm0047 report-generation fix): `_browse_narrative`
    # was previously the unconditional fallback for ANY unrecognized stats
    # shape - which is exactly how VNV band-index results (fixed above) used
    # to get mislabeled "a single raw scene" despite being a per-pixel,
    # multi-scene composite. Only genuine single-scene browse results
    # (`s2_browse`/`s1_browse`/`landsat_browse` - see gee_analysis_service.py's
    # `_s2_browse`) ever set `scene_date`; a future 14th analysis type with a
    # stats shape none of the branches above recognize now gets a neutral
    # "no narrative available" message instead of a wrong one.
    if "scene_date" in stats:
        return _browse_narrative(name, stats)
    return _no_narrative(name)
