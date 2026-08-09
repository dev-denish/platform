"""
Deterministic plain-language summary of one vegetation-index result.

Pure Python - no `ee`, DB, network or LLM. Given the per-year numbers
_annual_index_series() already computes (mean/std_dev/min/max/histogram),
this builds ONE English paragraph by string composition. Identical inputs
always produce byte-identical output, which is the point: this text lands in
an auditable analysis result a VVB may read, so it must be reproducible from
the stored numbers, not re-worded by a model on the next run.

WHAT THIS TEXT IS AND IS NOT
It is a DESCRIPTIVE reading of a pixel distribution. It is NOT an
eligibility, forest-definition, additionality or carbon determination, and no
clause here is phrased as one. Forest/non-forest runs through canopy cover
(the `canopy_cover_pct` forest-definition setting, consumed by the Hansen
pathway), NOT through an index mean; removals come from plot-based biomass
sampling (VM0047 s9.2), not from index values. "NDVI 0.55, therefore forest"
would be a VVB finding. Wording stays on "consistent with"/"worth checking",
deliberately, and every summary carries DESCRIPTIVE_ONLY_TRAILER.

WHERE THE NUMBERS BELOW COME FROM
Exactly ONE index has a genuine literature-standard cut point:
  * MNDWI at 0.0 - Xu, H. (2006), "Modification of normalised difference
    water index (NDWI) to enhance open water features in remotely sensed
    imagery", Int. J. Remote Sensing 27(14):3025-3033. Open water is
    positive, vegetation/soil/built-up negative, 0 is the split.
  * NDVI's bands are the conventional DESCRIPTIVE ranges published by USGS
    (Landsat NDVI) and NASA Earthdata: <~0.1 barren rock/sand/snow, ~0.2-0.5
    sparse grass/shrub/senescing, ~0.6-0.9 dense green canopy. A convention
    for reading a picture, not a standard's threshold - used here only to
    pick an adjective.
  * EVI, SAVI and NBR have NO universal absolute cut point. Rather than
    invent per-index numbers they REUSE NDVI's edges, with the consequences
    stated in their own caveat text:
      - EVI and SAVI (L=0.5 here) read systematically LOWER than NDVI over
        identical vegetation, so reusing NDVI's edges biases their adjective
        DOWNWARD (conservative), never upward.
      - NBR's severity classes (Key & Benson 2006, FIREMON Landscape
        Assessment) are defined on dNBR - pre-fire minus post-fire - not on
        the single-date absolute NBR this series computes. So NBR wording
        describes NIR-vs-SWIR contrast and names no severity class.

The variability and outlier rules are keyed to the histogram's own 0.1-wide
bucket (_VEG_INDEX_HISTOGRAM_RANGE/_BINS in gee_analysis_service.py: 20 bins
over [-1, 1]) rather than to fresh invented constants.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "INDEX_RANGE", "INDEX_HISTOGRAM_BIN_WIDTH", "DESCRIPTIVE_ONLY_TRAILER",
    "IndexProfile", "INDEX_PROFILES", "get_profile",
    "describe_level", "describe_variability", "describe_spatial_outliers",
    "describe_sample", "describe_trend", "compose_summary", "summarize_index_result",
]

# Natural range all 5 indices are read on, and the range the map tile is
# stretched over (visualize(min=-1, max=1) in gee_analysis_service.py).
INDEX_RANGE = (-1.0, 1.0)

# One histogram bucket: _VEG_INDEX_HISTOGRAM_RANGE (-1, 1) over
# _VEG_INDEX_HISTOGRAM_BINS (20). Duplicated as a literal rather than imported
# so this module stays free of the GEE service's `ee` import chain - if the
# service's binning changes, this must change with it.
INDEX_HISTOGRAM_BIN_WIDTH = 0.1

DESCRIPTIVE_ONLY_TRAILER = (
    "Descriptive only - not a forest-definition, eligibility or carbon determination."
)


@dataclass(frozen=True)
class IndexProfile:
    """Everything the clause builders need about one index.

    `edges` ascending, len(words) == len(readings) == len(edges) + 1.
    `edge_source` is carried so the provenance of every number here is
    readable from the object itself (tests assert it is non-empty)."""
    label: str
    edges: tuple[float, ...]
    words: tuple[str, ...]
    readings: tuple[str, ...]
    edge_source: str
    caveat: str | None = None
    trend_up_note: str | None = None
    trend_down_note: str | None = None


# USGS Landsat NDVI / NASA Earthdata descriptive ranges. All three edges land
# on an existing 0.1-wide histogram bin edge, so an adjective change is always
# visible as a bar boundary on the chart the frontend already draws.
_GREEN_EDGES = (0.1, 0.2, 0.5)
_GREEN_WORDS = ("very low", "low", "moderate", "high")
_GREEN_READINGS = (
    "consistent with bare ground, built-up surface, water or masked gaps rather than vegetation",
    "consistent with sparse or stressed vegetation",
    "consistent with grass, shrub, cropland or senescing vegetation rather than closed canopy",
    "consistent with dense green canopy",
)
_GREEN_SOURCE = (
    "USGS Landsat NDVI / NASA Earthdata descriptive ranges (<~0.1 barren, ~0.2-0.5 sparse "
    "grass/shrub/senescing, ~0.6-0.9 dense green canopy) - a descriptive convention for "
    "reading imagery, not a standard's threshold"
)
_TREND_UP_NOTE = (
    "A rising index is consistent with vegetation gain but is not a measurement of it - "
    "VM0047 removals come from plot-based biomass sampling (s9.2); this series is "
    "supporting evidence, not the number."
)
_TREND_DOWN_NOTE = (
    "A falling index is worth investigating before it is reported - harvest, fire, grazing, "
    "a drought year, or simply a drier pre-monsoon compositing window can all produce it."
)

INDEX_PROFILES: dict[str, IndexProfile] = {
    "ndvi": IndexProfile(
        label="NDVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE,
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "evi": IndexProfile(
        label="EVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE + "; reused for EVI, which has no universal absolute cut point",
        caveat=(
            "EVI reads lower than NDVI over identical vegetation, so this wording is "
            "deliberately conservative and is not comparable word-for-word with NDVI's."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "savi": IndexProfile(
        label="SAVI", edges=_GREEN_EDGES, words=_GREEN_WORDS, readings=_GREEN_READINGS,
        edge_source=_GREEN_SOURCE + "; reused for SAVI, which has no universal absolute cut point",
        caveat=(
            "SAVI (L=0.5) reads lower than NDVI over identical vegetation, so this wording is "
            "deliberately conservative and is not comparable word-for-word with NDVI's."
        ),
        trend_up_note=_TREND_UP_NOTE, trend_down_note=_TREND_DOWN_NOTE,
    ),
    "nbr": IndexProfile(
        label="NBR", edges=_GREEN_EDGES, words=_GREEN_WORDS,
        readings=(
            "high SWIR relative to NIR - consistent with recently burned, bare or built-up surface",
            "consistent with sparse or stressed vegetation",
            "consistent with partially vegetated or recovering surface",
            "consistent with dense, unburned, moist vegetation",
        ),
        edge_source=(
            _GREEN_SOURCE + "; reused for NBR, which has no universal absolute cut point "
            "(published severity classes are defined on dNBR, not single-date NBR)"
        ),
        caveat=(
            "Burn-severity classes (Key & Benson 2006, FIREMON) are defined on dNBR - a "
            "pre-fire minus post-fire difference - not on the single-date NBR shown here, so "
            "no severity class is asserted."
        ),
        trend_up_note=(
            "Rising NBR is consistent with vegetation recovery or regrowth after disturbance."
        ),
        trend_down_note=(
            "Falling NBR can indicate fire, clearing or drying - check the disturbance record "
            "for the drop year before reporting it as anything."
        ),
    ),
    "mndwi": IndexProfile(
        label="MNDWI", edges=(0.0,),
        words=("below the 0 open-water cut point (Xu 2006)", "above the 0 open-water cut point (Xu 2006)"),
        readings=(
            "consistent with land - vegetation, soil or built-up surface - rather than open water",
            "the boundary mean itself reads as open water, which for a land-based project "
            "boundary usually means a tank, reservoir or river covers a large share of the "
            "area - check the boundary before using the mean",
        ),
        edge_source=(
            "Xu, H. (2006), Int. J. Remote Sensing 27(14):3025-3033 - open water positive, "
            "vegetation/soil/built-up negative, 0 the split"
        ),
        trend_up_note="Rising MNDWI usually means more standing water (season, tank filling), not vegetation change.",
        trend_down_note="Falling MNDWI usually means less standing water, not vegetation change.",
    ),
}


def get_profile(index_id: str) -> IndexProfile:
    try:
        return INDEX_PROFILES[index_id]
    except KeyError:
        raise ValueError(
            f"no index summary profile for {index_id!r}; known: {sorted(INDEX_PROFILES)}"
        ) from None


# ------------------------------------------------------------- small helpers


def _band_index(edges: tuple[float, ...], value: float) -> int:
    """How many ascending cut points `value` meets or exceeds -> band index."""
    return sum(1 for edge in edges if value >= edge)


def _and_list(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _bin_centre(bin_edges: list[float], i: int) -> float:
    return (bin_edges[i] + bin_edges[i + 1]) / 2.0


# ------------------------------------------------------------ clause: level


def describe_level(index_id: str, mean: float | None) -> str | None:
    """Adjective for the boundary mean. See module docstring for where each
    index's band edges come from - only MNDWI's is a real literature cut
    point. Prints 2dp for readability; the stored value stays 4dp."""
    profile = get_profile(index_id)
    if mean is None:
        return None
    band = _band_index(profile.edges, mean)
    sentence = (
        f"{profile.label} averages {mean:.2f} across the boundary - "
        f"{profile.words[band]}, {profile.readings[band]}."
    )
    return f"{sentence} {profile.caveat}" if profile.caveat else sentence


# ------------------------------------------------------ clause: variability


# Keyed to the histogram bucket width, not to invented numbers: under half a
# bucket = the mean describes everything; over two buckets = it describes
# nothing in particular. Index-independent by construction - all five share
# the same fixed [-1, 1] / 20-bin histogram, which is why there is no
# index_id parameter here.
_VAR_UNIFORM = 0.5 * INDEX_HISTOGRAM_BIN_WIDTH   # 0.05
_VAR_FAIRLY_UNIFORM = 1.0 * INDEX_HISTOGRAM_BIN_WIDTH  # 0.10
_VAR_MODERATE = 2.0 * INDEX_HISTOGRAM_BIN_WIDTH  # 0.20


def describe_variability(std_dev: float | None) -> str | None:
    """Wording is deliberately plain-language (a field-team reviewer, not
    just a VVB auditor, reads this callout) - "a spread of about X" instead
    of "std dev X", no "histogram bin" - while still carrying the real
    number, not just an adjective, so nothing quantitative is lost."""
    if std_dev is None:
        return None
    if std_dev < _VAR_UNIFORM:
        return (
            f"Pixel values are uniform across the boundary (a spread of about {std_dev:.2f}), "
            "so the mean is a fair description of the whole area."
        )
    if std_dev < _VAR_FAIRLY_UNIFORM:
        return (
            f"Pixel values are fairly uniform across the boundary (a spread of about "
            f"{std_dev:.2f})."
        )
    if std_dev < _VAR_MODERATE:
        return (
            f"Pixel values are moderately variable across the boundary (a spread of about "
            f"{std_dev:.2f}), so the boundary covers noticeably different surfaces."
        )
    return (
        f"Pixel values vary widely across the boundary (a spread of about {std_dev:.2f}), so "
        "the boundary mean averages over quite different surfaces and should not be read as "
        "one uniform condition."
    )


# --------------------------------------------------------- clause: outliers


_BULK_BIN_MIN_FRACTION = 0.01   # a bin holding <1% of pixels is not "the bulk"
_TAIL_BIN_GAP = 2               # min/max this many bins beyond the bulk = a real tail
_PEAK_MIN_FRACTION = 0.15       # a peak must be >=15% of the tallest bin
_PEAK_MIN_SEPARATION = 2        # peaks closer than this are one plateau
_VALLEY_MAX_FRACTION = 0.50     # two peaks need a valley <=50% of the smaller peak


def _dominant_peaks(counts: list[int]) -> list[int]:
    """Bin indices of real modes. Local maxima >= _PEAK_MIN_FRACTION of the
    tallest bin, plateaus/near-neighbours collapsed to the tallest, and a
    second mode only counted when a genuine valley separates it from the
    previous one - so a single fat skewed peak does not read as bimodal."""
    if not counts:
        return []
    top = max(counts)
    if top <= 0:
        return []
    candidates = [
        i for i, c in enumerate(counts)
        if c >= top * _PEAK_MIN_FRACTION
        and c >= (counts[i - 1] if i > 0 else 0)
        and c >= (counts[i + 1] if i + 1 < len(counts) else 0)
    ]
    kept: list[int] = []
    for i in candidates:
        if kept and i - kept[-1] < _PEAK_MIN_SEPARATION:
            if counts[i] > counts[kept[-1]]:
                kept[-1] = i
            continue
        kept.append(i)
    peaks = kept[:1]
    for prev, cur in zip(kept, kept[1:]):
        valley = min(counts[prev + 1:cur])
        if valley <= _VALLEY_MAX_FRACTION * min(counts[prev], counts[cur]):
            peaks.append(cur)
    return peaks


def _bulk_span(counts: list[int], total: int) -> tuple[int, int]:
    """Lowest/highest bin index holding at least _BULK_BIN_MIN_FRACTION of the
    pixels. With a small total this floor admits single-pixel bins, which makes
    the tail test fire LESS often - the safe direction."""
    floor = total * _BULK_BIN_MIN_FRACTION
    occupied = [i for i, c in enumerate(counts) if c >= floor]
    if not occupied:
        peak = max(range(len(counts)), key=lambda i: counts[i])
        return peak, peak
    return occupied[0], occupied[-1]


def describe_spatial_outliers(
    index_id: str,
    minimum: float | None,
    maximum: float | None,
    bin_edges: list[float] | None,
    counts: list[int] | None,
) -> str | None:
    """Returns None when the distribution is a single unremarkable cluster -
    no filler text. Fires only on a real multi-modal histogram or a min/max
    sitting >= _TAIL_BIN_GAP bins beyond where the pixels actually are."""
    profile = get_profile(index_id)
    if not counts or not bin_edges or len(bin_edges) != len(counts) + 1:
        return None
    total = sum(counts)
    if total <= 0:
        return None
    bin_width = bin_edges[1] - bin_edges[0]
    if bin_width <= 0:
        return None

    parts: list[str] = []

    peaks = _dominant_peaks(counts)
    if len(peaks) >= 2:
        centres = _and_list([f"{_bin_centre(bin_edges, i):.2f}" for i in peaks])
        parts.append(
            f"Values cluster in two or more separate groups (around {centres}), so the "
            f"boundary contains two or more distinct surface types and the single "
            f"{profile.label} mean is averaging across them."
        )

    lo_bin, hi_bin = _bulk_span(counts, total)
    if minimum is not None:
        bulk_low = bin_edges[lo_bin]
        if bulk_low - minimum >= _TAIL_BIN_GAP * bin_width:
            parts.append(
                f"A thin low tail runs down to {minimum:.2f}, well below the bulk of the pixels "
                f"(which start around {bulk_low:.2f}) - typically water, bare ground, cloud "
                "shadow or a sliver of neighbouring land caught by the boundary edge; worth "
                "checking before reading the mean as representative."
            )
    if maximum is not None:
        bulk_high = bin_edges[hi_bin + 1]
        if maximum - bulk_high >= _TAIL_BIN_GAP * bin_width:
            parts.append(
                f"A thin high tail reaches {maximum:.2f}, well above the bulk of the pixels "
                f"(which end around {bulk_high:.2f})."
            )

    return " ".join(parts) if parts else None


# ----------------------------------------------------------- clause: sample


# 10 m x 10 m Sentinel-2 pixel = 0.01 ha - the scale=10 used in the
# reduceRegion that produced these counts.
_PIXEL_AREA_HA = 0.01
_THIN_SAMPLE_PIXELS = 100     # ~1 ha of valid pixels
_MODEST_SAMPLE_PIXELS = 1000  # ~10 ha


def describe_sample(counts: list[int] | None) -> str | None:
    """Caveat when the year's cloud-free sample is too thin to lean on.

    NOTE: `counts` counts IN-RANGE pixels only - gee_analysis_service.py's
    `_annual_index_series` masks each index to its natural [-1, 1] range
    BEFORE reduceRegion, so mean/std_dev/min/max above are computed over
    that same in-range set and `counts` is an EXACT count of it, not a lower
    bound (EVI's denominator approaching zero, e.g. under thin haze/cloud
    edges, is the main source of out-of-range pixels; that count is surfaced
    separately as `distribution[year]["out_of_range_pixel_count"]`, not
    folded in here)."""
    total = sum(counts) if counts else 0
    if total <= 0:
        return (
            "No in-range cloud-free pixels were available inside the boundary for this year, "
            "so there is nothing to describe - treat this year as missing, not as zero."
        )
    if total < _THIN_SAMPLE_PIXELS:
        return (
            f"Only {total} valid pixels (about {total * _PIXEL_AREA_HA:.2f} ha at 10 m) survived "
            "cloud masking inside the boundary, so every number above is indicative only - too "
            "thin a sample to carry into any reporting."
        )
    if total < _MODEST_SAMPLE_PIXELS:
        return (
            f"The sample is small ({total} valid pixels, about {total * _PIXEL_AREA_HA:.1f} ha at "
            "10 m after cloud masking) - fine for a visual read, thin for a trend claim."
        )
    return None


# ------------------------------------------------------------ clause: trend


_TREND_MIN_CHANGE = 0.5 * INDEX_HISTOGRAM_BIN_WIDTH  # 0.05 - half a histogram bin


def describe_trend(index_id: str, series: dict[str, float | None] | None) -> str | None:
    """First vs last year that actually produced a mean. Deliberately a plain
    endpoint difference, not a fitted slope: a regression slope invites being
    read as a rate, and this is not the VM0047 stocking-index Z-test (Appendix
    1) - that runs on control vs project plots, not on one boundary mean."""
    years = sorted((y for y, v in (series or {}).items() if v is not None), key=int)
    if len(years) < 2:
        return None
    profile = get_profile(index_id)
    first, last = years[0], years[-1]
    start, end = series[first], series[last]
    change = end - start
    if abs(change) < _TREND_MIN_CHANGE:
        return (
            f"Across {first}-{last} the boundary mean is essentially flat ({start:.2f} to "
            f"{end:.2f}, change {change:+.2f})."
        )
    direction = "risen" if change > 0 else "fallen"
    note = profile.trend_up_note if change > 0 else profile.trend_down_note
    sentence = (
        f"Across {first}-{last} the boundary mean has {direction} from {start:.2f} to "
        f"{end:.2f} ({change:+.2f})."
    )
    return f"{sentence} {note}" if note else sentence


# --------------------------------------------------------------- composers


def compose_summary(
    index_id: str,
    year: str | int,
    mean: float | None,
    std_dev: float | None,
    minimum: float | None,
    maximum: float | None,
    bin_edges: list[float] | None,
    counts: list[int] | None,
    series: dict[str, float | None] | None = None,
) -> str:
    """Joins whichever clauses fired into one paragraph. Fixed clause order
    (level, spread, shape, sample, trend) so two runs on the same numbers
    produce the same string. Prefixed "<year>: " rather than "In <year>, "
    so no clause ever has to be re-capitalised depending on which fired."""
    profile = get_profile(index_id)
    clauses = [
        describe_level(index_id, mean),
        describe_variability(std_dev),
        describe_spatial_outliers(index_id, minimum, maximum, bin_edges, counts),
        describe_sample(counts),
        describe_trend(index_id, series),
    ]
    body = " ".join(c for c in clauses if c)
    if not body:
        return (
            f"{year}: {profile.label} produced no usable pixel statistics for this year. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        )
    return f"{year}: {body} {DESCRIPTIVE_ONLY_TRAILER}"


def summarize_index_result(
    index_id: str,
    series: dict[str, float | None],
    distribution: dict[str, dict],
) -> str:
    """Entry point for _annual_index_series(): summarise the most recent year
    that actually produced a mean (not simply the last key - a cloud-blown
    latest year would otherwise silently produce an empty summary), then let
    compose_summary() append the whole-series trend."""
    profile = get_profile(index_id)
    years = sorted(distribution or {}, key=int)
    year = next(
        (y for y in reversed(years) if (distribution[y] or {}).get("mean") is not None), None
    )
    if year is None:
        return (
            f"{profile.label} produced no usable pixel statistics for any of the {len(years)} "
            f"years attempted - no cloud-free coverage inside the boundary. "
            f"{DESCRIPTIVE_ONLY_TRAILER}"
        )
    entry = distribution[year] or {}
    hist = entry.get("histogram") or {}
    return compose_summary(
        index_id, year,
        entry.get("mean"), entry.get("std_dev"), entry.get("min"), entry.get("max"),
        hist.get("bin_edges"), hist.get("counts"), series,
    )
