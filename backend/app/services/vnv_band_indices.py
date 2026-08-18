"""Pure band-math vegetation/water/soil/burn indices (Wave: VNV Pipeline band
indices). A DIFFERENT, much simpler compute path than `vnv_ndfi`'s spectral
unmixing (see app/workers/vnv_analysis_jobs.py's NDFI-specific job) - direct
arithmetic on the same 6-band (B02/B03/B04/B08/B11/B12) Sentinel-2 AOI raster
`CDSEClient.prepare_sentinel2_aoi_raster` already produces (app/services/
cdse_ingestion.py). No endmembers, no R sidecar, no seasonal-reference
dependency - none of `vnv_ndfi`'s confirmed failure mode (up to 97.66% of
pixels masked on forest-heavy scenes) applies here, since there is nothing
here for a spectral-unmixing solver to fail to converge on.

Formulas below are implemented EXACTLY as given in the project's reference
document ("Comprehensive List of Remote Sensing Indices for Carbon, Forestry
& Blue Carbon Projects"), not silently "corrected" to a different published
version. Two real discrepancies between that document and outside published
literature are flagged explicitly rather than resolved silently - see NDBI's
and NDDI's own docstrings below.

Reflectance scaling: `CDSEClient.prepare_sentinel2_aoi_raster` writes bands
as raw Sentinel-2 L2A digital numbers (uint16, ~0-10000 representing surface
reflectance x10000 - verified against a real output raster via
scripts/verify_cdse_ingestion.py, whose printed band stats are bare integers,
not 0-1 floats). Every formula below that adds a constant (EVI's `+1`,
SAVI's `+0.5`) is calibrated for 0-1 reflectance, not raw DN - `_to_
reflectance` divides by 10000 before any formula runs. The pure normalized-
difference formulas (NDVI, NDWI, ...) are scale-invariant and would be
unaffected either way, but every band is converted once, up front, for one
consistent unit across all 13 rather than converting on a per-formula basis.

NDFI-style masking does not apply here: an input pixel is "no data" only
when EVERY band is exactly 0 (`_INVALID_SCL_CLASSES`-masked or outside-AOI
pixels in cdse_ingestion.py are zeroed across all bands simultaneously - see
that module's `_process_s2_scene`/`_merge_and_clip`). Otherwise, this module
never rejects a pixel on its own; a formula's own zero-sum denominator (both
terms cancel to zero) is the only other source of a NaN output pixel, an
inherent, expected degenerate case of a normalized-difference ratio, not a
masking policy.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

# Matches CDSEClient.prepare_sentinel2_aoi_raster's default band order
# exactly (app/services/cdse_ingestion.py's `_S2_DEFAULT_BANDS`) - the 6-band
# raster this module reads has bands in this order and no other.
BAND_ORDER: tuple[str, ...] = ("B02", "B03", "B04", "B08", "B11", "B12")

_REFLECTANCE_SCALE = 10000.0
# Sentinel-2 L2A Processing Baseline >=04.00 (ESA, effective 2022-01-25 -
# every scene this pipeline can ever fetch is past this cutover, since
# vnv_analysis_jobs.py's `_trailing_window` only ever requests a 90-day
# window ending TODAY, never a caller-chosen historical range) applies a
# BOA_ADD_OFFSET to the encoded digital numbers: true reflectance =
# (dn + BOA_ADD_OFFSET) / 10000, NOT dn / 10000. Confirmed BOTH from ESA's
# published spec AND independently from this platform's own real CDSE STAC
# queries (2026-08-17): every one of 10 real scenes covering this module's
# own Bandipur-forest verification AOI, across all 6 bands, reported the
# STAC raster-extension fields `raster:scale=0.0001`/`raster:offset=-0.1`
# (i.e. true_reflectance = dn*0.0001 - 0.1 = (dn-1000)/10000) - so
# BOA_ADD_OFFSET=-1000 is not assumed, it is directly what CDSE's own
# metadata reports for every scene tested. Real-data impact (before this
# fix): every index's absolute value was measurably biased (VNV NDVI meaned
# 0.44 vs 0.67 from this platform's independent GEE compute path on the
# identical AOI/date window) - see vnv_band_indices.py's own module
# docstring and the PR evidence for the full cross-check.
#
# Hardcoded rather than read per-scene from each STAC asset's own
# `raster:scale`/`raster:offset` (which cdse_ingestion.py's
# `prepare_sentinel2_aoi_raster` does not currently thread through to its
# output raster or `SceneMeta`) because this pipeline's fixed 90-day
# trailing window can only ever touch already-Baseline-04.00 scenes -
# BUT this assumption breaks (and would need a real per-scene metadata
# read added to cdse_ingestion.py, not a bigger constant here) if this
# pipeline is ever extended to a caller-chosen HISTORICAL window that could
# span the 2022-01-25 cutover or predate it entirely.
_BOA_ADD_OFFSET_REFLECTANCE = -0.1  # = -1000 raw DN / 10000


def to_reflectance(dn: np.ndarray) -> np.ndarray:
    """Raw Sentinel-2 L2A digital numbers -> true 0-1 surface reflectance,
    including the mandatory Processing Baseline >=04.00 BOA_ADD_OFFSET
    correction (see `_BOA_ADD_OFFSET_REFLECTANCE`'s own comment for why this
    must happen before EVI/SAVI's additive constants are applied, and for
    the real evidence backing the exact offset value)."""
    return dn.astype(np.float32) / _REFLECTANCE_SCALE + _BOA_ADD_OFFSET_REFLECTANCE


def _ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """(numerator / denominator) elementwise, NaN wherever denominator is
    exactly zero - a masked/nodata input pixel (every band 0, see module
    docstring) or a genuine zero-sum edge case, never a crash (division by
    zero) or an inf/-inf value silently baked into a mean/min/max stat."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator != 0, numerator / denominator, np.nan).astype(np.float32)


def _ndi(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """The shared (a-b)/(a+b) normalized-difference shape - NDVI, NDWI,
    MNDWI, NDMI, NBR, NDBI, GNDVI, BSI, ARVI, and NDDI (composed from two
    already-computed normalized differences) are all this exact operation on
    a different pair of inputs, not independently hand-written formulas."""
    return _ratio(a - b, a + b)


# Real-data evidence (Bandipur forest AOI, 2026-08-17): EVI's denominator
# (unlike every _ndi-based denominator above, which is a sum of two
# non-negative reflectance-derived quantities and can only approach zero at
# an already-masked nodata pixel) is a signed linear combination that can
# cross zero on real, otherwise-valid pixels via its `-7.5*blue` term.
# `_ratio`'s exact-zero-only guard let those pixels through as enormous but
# finite values (real run: EVI swung to [-191.79, 312.38] against a
# documented [-1,1]) - not a crash, but exactly the "no crash, but also no
# signal" case division-by-zero handling exists to prevent. `_EPS` masks any
# denominator smaller in magnitude than this threshold as NaN, same as an
# exact zero - a real instability guard, not a value clamp (a clamp would
# fabricate a plausible-looking number at the boundary; this reports
# "unusable" instead). 0.3 is not an arbitrary guess: real EVI-denominator
# percentiles on that same run were p50=1.26, p1=0.70, p0.1=0.33 - a sharp,
# thin low tail - so eps=0.3 masks only ~0.1% of pixels (205/241194) while
# bringing max down from 7.97 (at eps=0.1) to 2.90, and eps=0.1 alone was
# verified NOT sufficient (see vnv_nddi's own module-level comment below for
# why the same technique fails outright for NDDI's denominator instead).
_EPS = 0.3


def _guarded_ratio(numerator: np.ndarray, denominator: np.ndarray, eps: float = _EPS) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(denominator) >= eps, numerator / denominator, np.nan).astype(np.float32)


def ndvi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    return _ndi(nir, red)


def evi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
        swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    # `_guarded_ratio`, not `_ratio` - real-data evidence showed this
    # denominator (unlike a plain reflectance sum) crosses zero on real,
    # non-masked pixels (haze/shadow/dark-target edge cases), producing
    # values in the hundreds against a documented [-1,1] range. See
    # `_guarded_ratio`'s own docstring.
    return _guarded_ratio(2.5 * (nir - red), nir + 6 * red - 7.5 * blue + 1)


def savi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    # Document formula: (NIR-Red)/(NIR+Red+0.5) x 1.5 - algebraically the
    # standard Huete 1988 SAVI with soil-brightness factor L=0.5 (whose
    # (1+L) term is exactly 1.5), implemented in the document's own literal
    # shape rather than re-derived from L.
    return _ratio(nir - red, nir + red + 0.5) * 1.5


def ndwi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """McFeeters' surface-water index, per the reference document. See
    `nddi` below for a real discrepancy this specific definition creates."""
    return _ndi(green, nir)


def mndwi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
          swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    return _ndi(green, swir1)


def ndmi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """Also listed as NDII and LSWI in the reference document - three names
    for the identical (NIR-SWIR1)/(NIR+SWIR1) formula (confirmed by
    comparing all three rows). Implemented once, here, per the task's own
    scope note - never triplicated."""
    return _ndi(nir, swir1)


def nbr(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
        swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    return _ndi(nir, swir2)


def bsi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
        swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    return _ndi(swir1 + red, nir + blue)


def ndbi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """DISCREPANCY FLAG: the reference document writes NDBI generically as
    "(SWIR - NIR) / (SWIR + NIR)" without saying SWIR1 vs SWIR2 - unlike its
    own NBR row, which explicitly says SWIR2. Implemented here with SWIR1
    (B11), matching the original published NDBI (Zha et al. 2003, Landsat
    band 5 / the SWIR1-equivalent band) and this same document's own MNDWI/
    NDMI rows, which both explicitly say SWIR1 for their generic "SWIR"
    band. Flagging rather than silently assuming: if the document's authors
    meant SWIR2 specifically for NDBI, this is a discrepancy, not a bug."""
    return _ndi(swir1, nir)


def arvi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """Plain `_ndi`, NOT `_guarded_ratio` - investigated and deliberately
    NOT given an EVI-style epsilon guard. Real-data evidence (Bandipur
    forest AOI) showed ARVI's max exceeding the documented [-1,1] range
    (observed max 1.74), but the mechanism is different from EVI/NDDI's
    near-zero-denominator instability: ARVI's denominator, NIR+(2*Red-Blue),
    sits at a real, non-degenerate percentile-0.1 of 0.23 in this scene (not
    near zero) - the excursion comes from `2*Red-Blue` legitimately going
    negative for real surfaces, a structural property of this formula's
    sign relationships, not a divide-by-near-zero artifact. Confirmed by
    testing: an epsilon guard large enough to bring the max back under 1.0
    (eps=0.5) masked 97.8% of the scene, while eps=0.1/0.3 (which tame EVI)
    left the max completely unchanged - there is no useful epsilon here.
    ARVI is documented in the remote-sensing literature as occasionally
    exceeding +-1 in practice (its purpose - aerosol/haze resistance via
    the same red/blue relationship - trades some of NDVI's strict
    boundedness for that robustness) - this is a known formula
    characteristic to disclose, not a bug to mask around."""
    return _ndi(nir, 2 * red - blue)


def gndvi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
          swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    return _ndi(nir, green)


def nddi(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """DISCREPANCY FLAG: the reference document defines NDDI as
    (NDVI-NDWI)/(NDVI+NDWI) using its OWN NDWI row - McFeeters'
    (Green-NIR)/(Green+NIR) surface-water index. The original published NDDI
    (Gu et al. 2007, "A five-year analysis of MODIS NDVI and NDWI for
    grassland drought assessment") instead uses Gao's NDWI, (NIR-SWIR)/
    (NIR+SWIR) - the SAME formula this document lists separately as NDMI/
    NDII/LSWI. These are two different bands entirely (Green vs SWIR1), so
    this is a real, unresolved conflict between the source document and
    outside published literature, not a matter of naming. Implemented here
    EXACTLY as the source document states - composed from THIS module's own
    `ndvi`/`ndwi` (McFeeters), not `ndmi` - per instructions to implement the
    document's formula as given and flag the conflict rather than pick a
    version silently.

    CONFIRMED DEGENERATE on vegetated land, not just theoretically risky:
    real-run evidence over the Bandipur forest AOI put NDVI+NDWI (this
    formula's own denominator) at the 99.9th percentile of only 0.14 across
    the ENTIRE scene - i.e. near-zero EVERYWHERE, not a rare tail the way
    EVI's denominator instability is (see `_EPS`'s own comment). A
    `_guarded_ratio` eps large enough to tame the output (0.3) masks the
    WHOLE scene; an eps small enough to leave any pixels unmasked (0.1)
    still leaves those survivors ranging to [-6.3, 11.2] - there is no
    working epsilon. This is why `vnv_nddi` is `status: "in-development"`
    in analysis_catalog.py despite this function being fully implemented -
    the function stays here, tested and ready, for whenever the
    document-vs-literature conflict above is resolved (most likely: switch
    to Gao's NDWI/this module's own `ndmi`, which does NOT near-cancel
    against NDVI over vegetated land - real-run NDVI=0.44 vs NDMI=0.09 sums
    to a well-behaved 0.53)."""
    # `_guarded_ratio`, not `_ndi`/`_ratio` - see this function's own
    # docstring above for the real-data evidence this denominator is
    # degenerate, not just theoretically unstable.
    ndvi_val = ndvi(blue, green, red, nir, swir1, swir2)
    ndwi_val = ndwi(blue, green, red, nir, swir1, swir2)
    return _guarded_ratio(ndvi_val - ndwi_val, ndvi_val + ndwi_val)


def psri(blue: np.ndarray, green: np.ndarray, red: np.ndarray, nir: np.ndarray,
         swir1: np.ndarray, swir2: np.ndarray) -> np.ndarray:
    """The reference document's (Red-Blue)/NIR (rather than the original
    Merzlyak et al. 1999 (Red-Blue)/RedEdge) is not a discrepancy worth
    flagging here specifically: this module's input raster has no red-edge
    band at all (B02/B03/B04/B08/B11/B12 only, see BAND_ORDER) - a
    red-edge-denominator version could not be computed from this pipeline's
    inputs regardless of which formula were preferred."""
    return _ratio(red - blue, nir)


INDEX_FUNCS: dict[str, Callable[..., np.ndarray]] = {
    "vnv_ndvi": ndvi,
    "vnv_evi": evi,
    "vnv_savi": savi,
    "vnv_ndwi": ndwi,
    "vnv_mndwi": mndwi,
    "vnv_ndmi": ndmi,
    "vnv_nbr": nbr,
    "vnv_bsi": bsi,
    "vnv_ndbi": ndbi,
    "vnv_arvi": arvi,
    "vnv_gndvi": gndvi,
    "vnv_nddi": nddi,
    "vnv_psri": psri,
}

# The documented/commonly-reported value range per index - not enforced or
# clamped anywhere, purely a reference for sanity-checking real compute
# output against (see the module's own test/verification evidence, not this
# module itself). NDDI is not bounded to [-1, 1] the way a single normalized
# difference is: it is a ratio of two already-bounded quantities, which can
# swing wider whenever their sum approaches zero (see `nddi`'s own
# docstring) - the range below is the commonly-reported empirical range for
# vegetated/drought-monitoring scenes, not a hard mathematical bound.
EXPECTED_RANGES: dict[str, tuple[float, float]] = {
    "vnv_ndvi": (-1.0, 1.0),
    "vnv_evi": (-1.0, 1.0),
    "vnv_savi": (-1.5, 1.5),
    "vnv_ndwi": (-1.0, 1.0),
    "vnv_mndwi": (-1.0, 1.0),
    "vnv_ndmi": (-1.0, 1.0),
    "vnv_nbr": (-1.0, 1.0),
    "vnv_bsi": (-1.0, 1.0),
    "vnv_ndbi": (-1.0, 1.0),
    "vnv_arvi": (-1.0, 1.0),
    "vnv_gndvi": (-1.0, 1.0),
    "vnv_nddi": (-2.0, 2.0),
    "vnv_psri": (-1.0, 1.0),
}


def compute_index(analysis_id: str, bands: dict[str, np.ndarray]) -> np.ndarray:
    """`bands` keyed by BAND_ORDER's own names (B02..B12), each a float32 2D
    reflectance array (already `to_reflectance`-converted by the caller) on
    the SAME grid - guaranteed by CDSEClient, which delivers every band on
    one 10m grid regardless of native resolution (see cdse_ingestion.py's
    own docstring). Raises KeyError for an analysis_id this module doesn't
    implement - callers only ever call this for an id already confirmed
    present in INDEX_FUNCS (app/domain/analysis_catalog.py's CATALOG is the
    single source of truth for which ids exist)."""
    fn = INDEX_FUNCS[analysis_id]
    return fn(bands["B02"], bands["B03"], bands["B04"], bands["B08"], bands["B11"], bands["B12"])
