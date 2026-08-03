"""
XYZ map tile rendering from a Cloud-Optimized GeoTIFF (Phase 3 Wave A).

Dynamic (render-on-request) tiling via rio-tiler (pinned) - not a pre-generated
tile pyramid. This is the standard modern approach for exactly this scale: it
needs no cache-warming step, serves every zoom level from one COG, and the COG's
own overviews (built by ingestion/cog.py) keep each request's I/O bounded to
roughly one overview level's worth of data, not the full-resolution raster.

Format: PNG, not WEBP. Classified rasters are discrete class values, not
photographic RGB - PNG is lossless, so hard class boundaries render pixel-exact
with no compression artefacts blurring a boundary between two classes (a real
interpretive/compliance concern for an MRV product, not just a quality nicety).
PNG also needs no browser feature-detection and matches the format
`raster.render_preview` already uses for the same reason.

Bugfix (Phase 3 Wave C): a classified colormap was being built and applied
unconditionally, regardless of whether the COG was actually single-band
classified data - a real multi-band "Satellite / Raw Imagery" scene made
rio_tiler raise `InvalidFormat("Source data must be 1 band")` on every tile
request. Branch on the COG's actual band count instead of assuming.

Bugfix (Phase 3 Wave E, superseded): a rotated/irregular real scene
reprojected onto an axis-aligned lat/lng grid (raster.reproject_to_4326) used
to leave warp-fill padding around its actual footprint indistinguishable
from real data when the SOURCE raster never had a `nodata` value set - "every
requested band exactly 0" was treated as an additional mask on top of
whatever rio-tiler already masked.

Wave: geometric padding fix. `reproject_to_4326` now writes a REAL internal
mask band onto the COG's source file itself (a genuine per-pixel record of
where the warp actually placed real source data, geometric - not a guess
from pixel values), which rio-cogeo carries through COG conversion
unchanged. rio-tiler's `Reader` already reads that mask natively, so this
module no longer needs its own value-based padding guess for the case that
mask covers - the old `_mask_warp_fill`/`padding_value`/`legend_defines`
machinery driven purely off `nodata`/rotation is gone.

Bugfix (Wave: production render audit): the geometric mask only ever covers
padding a WARP introduces. It reports "fully valid" for an upload whose own
source array already had fill baked into ordinary pixel values before
reaching this app - confirmed against a real ad-hoc upload rendering as
white/washed-out patches despite `dataset_mask()` reporting 100% valid.
`_mask_uninitialized_fill` reinstates a narrow value-based check for
exactly that gap, scoped to a real (>=3 distinct band) raw composite only -
see its docstring for why that scope keeps it safe from the class-0
ambiguity the old, removed heuristic had.

Symbology (Phase 3 Wave F): band-to-channel assignment, stretch percentiles,
and per-class color overrides, all applied live at request time - no
re-ingestion. Two independent render modes:
  - "classified": a persisted `class_legend` colors band 1 by class value
    (LULC etc.) - `color_overrides` lets a caller override specific class
    colors without touching the stored legend.
  - "raw bands": an explicit band composite (1 band -> grayscale, 3 bands ->
    RGB) with a configurable percentile stretch - the mode Wave C/E's
    band-composite logic already used as the only option for unclassified
    data, now also reachable (and reconfigurable) for a classified layer's
    raw underlying bands if a caller explicitly asks for it.
Mode selection: `bands` given (even for a 1-band COG) -> raw bands, always.
No `bands` AND a legend exists -> classified. Otherwise -> raw bands with the
Wave C/E defaults (first 3 bands or grayscale, 2nd-98th percentile) - i.e.
identical output to before this wave when no new params are passed at all.
"""
from __future__ import annotations

import math

import numpy as np
from rio_tiler.io import Reader

from app.core.errors import ValidationError
from app.services.ingestion.raster import color_for_value

_TILE_FORMAT = "PNG"
_DEFAULT_STRETCH = (2, 98)


def _colormap_for_uint8(
    legend: dict | None = None,
    overrides: dict[str, str] | None = None,
) -> dict[int, tuple[int, int, int, int]]:
    """Full 0-255 colormap so rio-tiler can map every possible class byte value.
    `overrides` (value-string -> "#rrggbb") take precedence over the legend's
    own color for that value, which takes precedence over DEFAULT_PALETTE - the
    same fallback chain `color_for_value` already implements.

    No padding/nodata special-casing here at all (Wave: geometric padding
    fix) - the COG's own real internal mask (written by reproject_to_4326)
    already tells rio-tiler which pixels are padding; `img.render()` renders
    a masked pixel fully transparent regardless of what the colormap dict
    says for its raw value (verified directly against a real COG), so a
    colormap only ever needs to define real class colors.

    Wave: editable class legend - a value with no CURRENT entry in `legend`
    maps to fully transparent (0, 0, 0, 0) here too, not a cycling
    DEFAULT_PALETTE fallback color: removing a class from a layer's
    persisted legend (see ClassLegendService) must make it render transparent
    immediately, live, on the very next tile request - no re-ingestion, no
    separate "hide this class" control. `overrides` never applies to an
    unmatched value either - there's no real class there to override."""
    cmap: dict[int, tuple[int, int, int, int]] = {}
    for v in range(256):
        if legend.get(str(v)) is None:
            cmap[v] = (0, 0, 0, 0)
            continue
        override = overrides.get(str(v)) if overrides else None
        hexc = (override or color_for_value(v, legend)).lstrip("#")
        r, g, b = (int(hexc[i : i + 2], 16) for i in (0, 2, 4))
        cmap[v] = (r, g, b, 235)
    return cmap


def _percentile_ranges(stats, lo_pct: int, hi_pct: int) -> list[tuple[float, float]]:
    lo_key, hi_key = f"percentile_{lo_pct}", f"percentile_{hi_pct}"
    ranges = []
    for v in stats.values():
        d = v.model_dump()
        lo, hi = d[lo_key], d[hi_key]
        if math.isnan(lo) or math.isnan(hi):
            # Every pixel in this band is masked (e.g. an all-padding tile) -
            # the range is moot, nothing will render but transparency anyway.
            lo, hi = 0.0, 1.0
        elif hi <= lo:
            hi = lo + 1
        ranges.append((lo, hi))
    return ranges


def _mask_uninitialized_fill(img) -> None:
    """Value-based fallback padding mask, for a real (>=3 distinct band) raw
    composite ONLY - excludes a pixel that is an exact `0` in every
    composited band simultaneously, on top of whatever the COG's own
    geometric mask (Wave: geometric padding fix) already excluded.

    Why this is still needed even with a real geometric mask: that mask can
    only catch padding a WARP introduced (rotation/reprojection/resampling).
    An upload whose own source array already had fill baked into ordinary
    pixel values before it ever reached this app - no `nodata` tag, no
    geometric mismatch for `add_alpha` to reveal (see raster.py's "Known
    limitation") - passes straight through as "fully valid" (verified
    against a real ad-hoc upload: `dataset_mask()` reports 100% valid while
    ~40% of pixels are an exact `0,0,0` across all three composited bands,
    confined to an irregular blob rather than a stray pixel here and there).

    Why exact-zero-across-every-band is a safe signal here and wasn't for
    the value-based heuristic this module already removed once: that
    heuristic single-banded a CLASSIFIED layer, where a legend can validly
    define class value 0 (e.g. Water) - one band's 0 is real data. A raw,
    unclassified multi-band composite has no such legend; a genuine
    reflectance/DN measurement varies at least slightly band-to-band, so
    every requested band landing on the identical exact sentinel at once is
    what synthetic/uninitialized fill looks like, not real imagery -
    confirmed empirically: real per-tile percentile stats computed WITH
    these pixels included get dragged toward 0, which is exactly what
    washes real data out toward white after the stretch (the visible bug
    this fixes) - excluding them fixes both the transparency and the
    stretch in one place. Tested against a real dark feature too (deep
    water/heavy shadow: low but per-pixel-noisy DN, not a fixed value) -
    genuine sensor noise essentially never lands on an exact 0 in 3
    independent bands at once, so it renders normally, not as fill.

    Known residual limitation, stated plainly: a real feature some upstream
    tool already hard-clipped to a literal integer 0 in every band (not just
    very low values - e.g. an 8-bit product that saturates a dark region
    flat) is still indistinguishable from fill. Nothing short of a real
    per-file `nodata` tag resolves that; this only closes the gap for the
    ordinary case where fill is a literal, exact, uniform sentinel and real
    data is not."""
    data = np.ma.getdata(img.array)
    uninitialized = (data == 0).all(axis=0)
    img.array.mask = np.ma.getmaskarray(img.array) | uninitialized[np.newaxis, :, :]


def read_pixel(cog_path: str, lon: float, lat: float) -> list[float | None]:
    """Phase 3 Wave D: the real per-band value at one EPSG:4326 lon/lat, for
    pixel/attribute inspection. Raw numbers only, in native band order - no
    colormap/legend interpretation here (the caller already has the layer's
    class_legend from GET /projects/{id}/layers and maps a value to its label
    itself, same as the frontend's own Symbology panel already does - no
    reason to duplicate that lookup server-side).

    No `legend` parameter (Wave: geometric padding fix - it used to be
    consulted only for the now-removed padding heuristic below): `cog.point`
    already reads the COG's own real internal mask (written by
    reproject_to_4326), so a legend-defined class 0 (e.g. Water=0) and
    genuine warp-fill padding are already correctly distinguished by the
    file itself - nothing left here needs to special-case value 0 at all.

    Raises `rio_tiler.errors.PointOutsideBounds` if the point isn't covered by
    this raster - same "not a failure, just no data here" contract as
    `render_tile`'s `TileOutsideBounds`, mapped by the caller to a clean 404.
    """
    with Reader(cog_path) as cog:
        point = cog.point(lon, lat)
    data = np.ma.getdata(point.array)
    mask = np.ma.getmaskarray(point.array)
    return [None if m else float(v) for v, m in zip(data, mask, strict=True)]


def _validate_bands(bands: tuple[int, ...], band_count: int) -> None:
    for b in bands:
        if b < 1 or b > band_count:
            raise ValidationError(
                f"Band {b} is out of range - this layer has {band_count} band(s)."
            )


def render_tile(
    cog_path: str,
    z: int,
    x: int,
    y: int,
    *,
    legend: dict | None = None,
    bands: tuple[int, ...] | None = None,
    stretch: tuple[int, int] | None = None,
    color_overrides: dict[str, str] | None = None,
) -> bytes:
    """Render one PNG tile. Raises `rio_tiler.errors.TileOutsideBounds` if the
    requested tile doesn't intersect the raster - the normal, expected outcome
    for XYZ requests at a viewport's edges, not a failure. The caller (the tiles
    API route) maps that to a clean 404, matching how any XYZ tile server
    behaves for out-of-coverage tiles. Raises `ValidationError` (-> 422) for an
    out-of-range band index - a client input mistake, not a server failure."""
    with Reader(cog_path) as cog:
        band_count = cog.dataset.count
        if bands is not None:
            _validate_bands(bands, band_count)

        if bands is None and legend:
            # Classified: band 1 holds class values by convention (matches
            # compute_stats' own assumption for legend-driven data).
            img = cog.tile(x, y, z)
            colormap = _colormap_for_uint8(legend=legend, overrides=color_overrides)
            return img.render(img_format=_TILE_FORMAT, colormap=colormap)

        # Raw band composite: there are no discrete "classes" here (or the
        # caller explicitly asked to bypass classification), so a colormap
        # doesn't apply - render a real band composite instead (1 band ->
        # grayscale, 3 bands -> RGB), contrast-stretched from this tile's own
        # percentile range so raw reflectance values land in a visible 0-255
        # range.
        # ponytail: the stretch is per-tile, not one fixed per-layer range, so
        # exposure can vary slightly tile-to-tile - a real fix means storing a
        # per-layer statistic. Not done here: it's a bigger, separate change
        # (would need the stat computed once at ingest and persisted), and
        # per-tile stretching already gives correct, just not perfectly
        # uniform, results.
        indexes = bands if bands is not None else ((1, 2, 3) if band_count >= 3 else (1,))
        lo_pct, hi_pct = stretch if stretch is not None else _DEFAULT_STRETCH

        # `cog.tile()` already reads the COG's own real internal geometric
        # mask (written by reproject_to_4326) for any requested band
        # combination. The 3-distinct-band case additionally runs
        # `_mask_uninitialized_fill` - see its docstring for why the
        # geometric mask alone isn't always enough. Not applied to the
        # grayscale/solo branch below: with only one real band there is no
        # cross-band signal to tell real (possibly legitimately dark) data
        # apart from fill.
        if len(indexes) == 3:
            img = cog.tile(x, y, z, indexes=indexes)
            _mask_uninitialized_fill(img)
            stats = img.statistics(percentiles=[lo_pct, hi_pct])
            ranges = _percentile_ranges(stats, lo_pct, hi_pct)
        else:
            solo = cog.tile(x, y, z, indexes=indexes)
            stats = solo.statistics(percentiles=[lo_pct, hi_pct])
            ranges = _percentile_ranges(stats, lo_pct, hi_pct) * 3
            img = cog.tile(x, y, z, indexes=indexes * 3)
        img.rescale(in_range=ranges, out_range=((0, 255),) * 3)
        return img.render(img_format=_TILE_FORMAT)
