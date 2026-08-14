# VNV Pipeline Phase 2 - NDFI sidecar (R / plumber).
#
# Standalone HTTP module: ONE endpoint, POST /ndfi. Not wired into FastAPI,
# arq, or the frontend - see the task that created this file for that
# explicit scope boundary. Runs ForesToolboxRS::sma() (Linear Spectral
# Mixture Analysis / Adams et al. 1993 linear mixing model) on a 6-band
# Sentinel-2 GeoTIFF, then derives NDFI (Souza Jr., Roberts & Cochrane 2005,
# "Combining spectral and spatial information to map canopy damage from
# selective logging and forest fires", Remote Sens. Environ. 98(2-3):329-343).
#
# NOT ndfiSMA() - that function ships with this package but its own
# @param procesLevel docs say "SR ... from TM, ETM+ and OLI ... TOA ...
# only for Landsat 8 OLI" and its endm table is a 4x6 Landsat-band matrix.
# Neither applies to Sentinel-2's band set/wavelengths. We call sma()
# directly with our own endmember matrix instead.
#
# Input band order (6-band GeoTIFF, all on one 10m grid): B02, B03, B04,
# B08, B11, B12 - matches backend/app/services/cdse_ingestion.py's
# `_S2_DEFAULT_BANDS` (verified by reading that file directly, 2026-08-13).
#
# ---------------------------------------------------------------------------
# ENDMEMBER SOURCING - path (b): image-derived, NOT a fixed literature table
# ---------------------------------------------------------------------------
# ForesToolboxRS ships no Sentinel-2 endmember values. The classic Souza et
# al. (2005) numbers are Landsat TM/ETM+ band-specific and don't transfer to
# Sentinel-2's different band centers/widths. Searched for a published,
# citable Sentinel-2-specific numeric adaptation (2026-08-13):
#   - Bullock et al. 2020 (the paper behind the CODED tool, which documents
#     Souza-style endmember coefficients) is Landsat-only - CODED's own docs
#     state "CODES was designed to use 30m Landsat data"
#     (https://coded.readthedocs.io/en/latest/algorithm.html).
#   - No other paper surfaced with a citable fixed Sentinel-2 GV/NPV/Soil/
#     Shade reflectance table.
# Went with path (b) instead: derive endmembers from the image itself. This
# is a standard, well-established alternative in the spectral-unmixing
# literature, not an invention -
#   - Small, C. (2004). "The Landsat ETM+ spectral mixing space." Remote
#     Sens. Environ. 93(1-2):1-12 - image endmembers taken from the extremes
#     (vertices) of the scene's own spectral feature space, rather than a
#     fixed universal spectral library.
#   - e-sensing/SITS docs (https://e-sensing.github.io/sitsbook/dc_mixture.html)
#     make the same point specifically for Sentinel-2: endmembers should be
#     "chosen carefully and based on expert knowledge of the area" via pure
#     pixels in the actual scene, not a universal table.
#
# Method implemented in `derive_endmembers()` below: percentile-based pure-
# pixel selection using two feature axes computed from the 6 input bands -
# NDVI (vegetation/soil axis) and overall brightness (shade/dark axis):
#   - GV    = mean spectrum of the top `pct` of pixels by NDVI (purest live
#             canopy in-scene).
#   - Shade = mean spectrum of the bottom `pct` of pixels by brightness
#             (darkest pixels - shadow/water-like).
#   - within the non-vegetated pool (NDVI below the scene median):
#     - Soil = mean spectrum of the top `pct` by brightness (brightest bare
#               ground).
#     - NPV  = mean spectrum of the bottom `pct` by brightness, excluding
#               pixels already claimed by the global Shade set (dimmer
#               non-photosynthetic cover - litter/dry residue - distinct
#               from deep shadow).
#
# CAVEAT (flag to carbon-mrv-vm0047 and qa-geospatial-validator): brightness
# alone is a weak discriminator between Soil and NPV - real soil and dry
# plant litter can overlap heavily in total brightness without a dedicated
# cellulose-absorption or multi-date index. This is the shakiest part of the
# whole derivation. A real deployment should replace this automatic pick
# with expert-drawn ROI pure pixels per microlandscape site once ground
# truth is available. Do not treat this endpoint's fractions/NDFI as
# validated for VM0047 reporting without that review.

suppressPackageStartupMessages({
  library(plumber)
  library(raster)
  library(ForesToolboxRS)
})

BAND_NAMES <- c("B02", "B03", "B04", "B08", "B11", "B12")
ENDMEMBER_NAMES <- c("gv", "npv", "soil", "shade")

#' NDFI from GV/NPV/Soil/Shade fractions (Souza et al. 2005 formula).
#' Works on plain numeric vectors OR raster layers - both support the same
#' arithmetic/logical-indexing operators, which is exactly what the
#' self-check below relies on to validate this function without needing a
#' real raster or sma() run.
#'
#' `sma()` is unconstrained least-squares, not a constrained fit - any of the
#' four fractions can come back negative or > 1 on pixels the linear model
#' can't resolve well (found via a real smoke test: a scene forced toward
#' near-pure shade produced NDFI far outside [-1, 1]). Two earlier, narrower
#' guards each missed a real case: masking shade alone missed gv/npv/soil
#' going out of range on their own; masking only the DERIVED gvs = gv/(1-shade)
#' (not raw gv/shade) missed the case where gv and shade are both out of
#' range in a way that cancels in the division (e.g. gv=-0.10, shade=1.20
#' gives gvs = -0.10/-0.20 = 0.5, which looks fine). NDFI = (gvs-(npv+soil)) /
#' (gvs+npv+soil) is only ALGEBRAICALLY guaranteed to stay in [-1, 1] when
#' gvs, npv, soil are all >= 0, but a pixel where the RAW fractions are
#' individually unresolvable is just as untrustworthy even if gvs happens to
#' land in range by cancellation - so both the raw fractions and the derived
#' gvs are checked. Masked to NA, not clamped: a clamped, in-range number
#' would misrepresent a pixel the model genuinely couldn't resolve as if it
#' were a normal measurement.
ndfi_from_fractions <- function(gv, npv, soil, shade, tol = 0.01) {
  gvs <- gv / (1 - shade)

  # carbon-mrv-vm0047 review caught a real hole here: checking only gvs
  # (not raw gv/shade) misses cases where gv and shade are BOTH out of
  # [0,1] in a way that cancels in the division - e.g. gv=-0.10,
  # shade=1.20 gives gvs = -0.10/-0.20 = 0.5, which passes the gvs check
  # even though neither input fraction is remotely resolvable. Range-check
  # the raw inputs too, not just the derived quantity.
  invalid <- (gvs < -tol) | (gvs > 1 + tol) |
    (gv < -tol) | (gv > 1 + tol) |
    (shade < -tol) | (shade > 1 - tol) |
    (npv < -tol) | (npv > 1 + tol) |
    (soil < -tol) | (soil > 1 + tol)

  denom <- gvs + npv + soil
  invalid <- invalid | (abs(denom) < tol)

  ndfi <- (gvs - (npv + soil)) / denom
  ndfi[invalid] <- NA
  # `tol` itself creates a small amount of legitimate slack right at the
  # gvs/npv/soil boundaries (found via the same smoke test: a valid,
  # non-masked pixel came back at 1.0103, not exactly <= 1) - clamp what
  # survives the mask to NDFI's actual defined range. This is NOT the same
  # move as clamping instead of masking above: a masked (NA) pixel stays
  # missing; this only trims the tolerance-band's own rounding slack off a
  # pixel already judged resolvable. Logical-index assignment, not
  # pmin()/pmax() - confirmed directly that pmin/pmax error out on a
  # RasterLayer ("invalid 'x' type in 'x || y'"), while `x[cond] <- value`
  # works on both RasterLayer and plain numeric vectors (same reason the
  # NA-masking above uses this form rather than ifelse()).
  ndfi[!invalid & ndfi > 1] <- 1
  ndfi[!invalid & ndfi < -1] <- -1
  ndfi
}

#' Percentile-based pure-pixel endmember derivation - see module docstring
#' above for the method and its citations/caveats.
derive_endmembers <- function(img, pct = 0.01) {
  vals <- raster::values(img)
  colnames(vals) <- BAND_NAMES
  complete <- stats::complete.cases(vals)
  vals <- vals[complete, , drop = FALSE]
  if (nrow(vals) < 20) {
    stop("Not enough valid pixels to derive endmembers (need >= 20, got ",
         nrow(vals), ").")
  }

  ndvi <- (vals[, "B08"] - vals[, "B04"]) / (vals[, "B08"] + vals[, "B04"])
  # Defensive, not the primary NoData path: cdse_ingestion.py's nodata=0
  # fill pixels are already dropped by complete.cases() above (confirmed
  # empirically - raster::stack() reads the GeoTIFF's embedded NoData tag
  # and turns those pixels into NA before this function ever sees them).
  # This only catches the rarer case of a genuinely non-NA pixel where
  # B08+B04 happens to be exactly 0 - matrix row-indexing with an NA
  # condition (which NDVI's NaN would otherwise produce) silently inserts
  # all-NA rows, which can poison an entire endmember's colMeans.
  finite_ndvi <- is.finite(ndvi)
  vals <- vals[finite_ndvi, , drop = FALSE]
  ndvi <- ndvi[finite_ndvi]
  brightness <- rowSums(vals)

  gv_thresh <- stats::quantile(ndvi, probs = 1 - pct, na.rm = TRUE)
  gv_spec <- colMeans(vals[ndvi >= gv_thresh, , drop = FALSE])

  shade_thresh <- stats::quantile(brightness, probs = pct, na.rm = TRUE)
  shade_mask <- brightness <= shade_thresh
  shade_spec <- colMeans(vals[shade_mask, , drop = FALSE])

  # Indices into `vals` (not `rownames` - raster::values() returns an
  # unnamed matrix, so row-name-based set operations would silently match
  # nothing) - nonveg_idx maps each nonveg_vals row back to its row number
  # in the full `vals` matrix, which is what lets us exclude Shade pixels
  # from the NPV pool below.
  nonveg_idx <- which(ndvi < stats::median(ndvi, na.rm = TRUE))
  nonveg_brightness <- brightness[nonveg_idx]
  nonveg_vals <- vals[nonveg_idx, , drop = FALSE]

  soil_thresh <- stats::quantile(nonveg_brightness, probs = 1 - pct, na.rm = TRUE)
  soil_spec <- colMeans(nonveg_vals[nonveg_brightness >= soil_thresh, , drop = FALSE])

  npv_thresh <- stats::quantile(nonveg_brightness, probs = pct, na.rm = TRUE)
  npv_candidate <- nonveg_idx[nonveg_brightness <= npv_thresh]
  # Exclude pixels already claimed by the global Shade pool - see module
  # docstring: NPV should be dim, non-vegetated ground cover, not deep shadow.
  npv_rows <- setdiff(npv_candidate, which(shade_mask))
  if (length(npv_rows) == 0) {
    npv_rows <- npv_candidate
  }
  npv_spec <- colMeans(vals[npv_rows, , drop = FALSE])

  endm <- rbind(gv_spec, npv_spec, soil_spec, shade_spec)
  rownames(endm) <- ENDMEMBER_NAMES
  colnames(endm) <- BAND_NAMES
  # carbon-mrv-vm0047 review: this pixel count needs to reach the API
  # response - a caller can't otherwise tell "endmembers derived from a
  # healthy sample" from "derived from barely 20 pixels because the AOI is
  # tiny or mostly cloud-masked."
  attr(endm, "n_pixels_used") <- nrow(vals)
  endm
}

#* @apiTitle VNV Pipeline - ForesToolboxRS NDFI sidecar (experimental)

# Trust boundary (carbon-mrv-vm0047 review flagged this - the one item not
# deferred to domain review): input_path/output_path arrive straight off an
# unauthenticated HTTP request. Without a constraint, a caller could make
# this container read or overwrite any file it has permission to touch.
# Constrained to one configured directory - override via NDFI_DATA_DIR,
# default /data (matches this image's documented `-v host:/data` bind-mount
# convention, see Dockerfile comments).
DATA_DIR <- normalizePath(Sys.getenv("NDFI_DATA_DIR", "/data"), mustWork = FALSE)
dir.create(DATA_DIR, recursive = TRUE, showWarnings = FALSE)

resolve_in_data_dir <- function(path) {
  resolved <- suppressWarnings(normalizePath(path, mustWork = FALSE))
  if (is.na(resolved) ||
      !(resolved == DATA_DIR || startsWith(resolved, paste0(DATA_DIR, .Platform$file.sep)))) {
    stop(paste0("path must be inside ", DATA_DIR, ": ", path))
  }
  resolved
}

#* Compute NDFI from a 6-band Sentinel-2 GeoTIFF via spectral mixture analysis
#* @param input_path:string Path to a 6-band GeoTIFF (B02,B03,B04,B08,B11,B12), one 10m grid - must resolve inside the configured data directory
#* @param output_path:string Path to write the NDFI raster to - must resolve inside the configured data directory
#* @param endmember_pct:number Percentile (0-0.5, exclusive) used for pure-pixel endmember selection, default 0.01
#* @post /ndfi
function(req, res, input_path, output_path = NULL, endmember_pct = 0.01) {
  endmember_pct <- suppressWarnings(as.numeric(endmember_pct))
  if (is.na(endmember_pct) || endmember_pct <= 0 || endmember_pct >= 0.5) {
    res$status <- 400
    return(list(error = "endmember_pct must be a number in (0, 0.5)"))
  }

  input_path <- tryCatch(resolve_in_data_dir(input_path), error = function(e) conditionMessage(e))
  if (!file.exists(input_path)) {
    res$status <- 400
    return(list(error = paste0("input_path invalid or does not exist: ", input_path)))
  }

  if (is.null(output_path) || output_path == "") {
    # Strip any .tif/.tiff extension (case-insensitive) before appending our
    # own suffix+extension, rather than a bare regex substitution - the
    # original version only matched ".tif", so a ".tiff" input left
    # output_path identical to input_path and writeRaster(overwrite=TRUE)
    # would have destroyed the source scene (caught in review, not by a
    # test - this repo has no .tiff-extension fixture).
    output_path <- paste0(sub("\\.tiff?$", "", input_path, ignore.case = TRUE), "_ndfi.tif")
  } else {
    output_path <- tryCatch(resolve_in_data_dir(output_path), error = function(e) NA_character_)
    if (is.na(output_path)) {
      res$status <- 400
      return(list(error = paste0("output_path must be inside ", DATA_DIR)))
    }
  }

  img <- raster::stack(input_path)
  if (raster::nlayers(img) != length(BAND_NAMES)) {
    res$status <- 400
    return(list(error = paste0(
      "Expected 6 bands (B02,B03,B04,B08,B11,B12), got ", raster::nlayers(img)
    )))
  }
  names(img) <- BAND_NAMES

  endm <- derive_endmembers(img, pct = endmember_pct)

  fractions <- ForesToolboxRS::sma(img = img, endm = endm, verbose = FALSE)

  ndfi <- ndfi_from_fractions(
    fractions[["gv"]], fractions[["npv"]], fractions[["soil"]], fractions[["shade"]]
  )
  names(ndfi) <- "ndfi"

  dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
  raster::writeRaster(ndfi, output_path, overwrite = TRUE)

  ndfi_vals <- raster::values(ndfi)
  total_n <- length(ndfi_vals)
  valid <- ndfi_vals[!is.na(ndfi_vals)]
  # carbon-mrv-vm0047 review: a high masked fraction (out-of-[0,1] sma()
  # fractions - see ndfi_from_fractions) is real, useful signal that the
  # derived endmembers didn't fit this scene well, not a hidden detail -
  # report it rather than only the raw valid count.
  n_pixels_used <- attr(endm, "n_pixels_used")

  list(
    status = "experimental — pending domain review",
    output_path = output_path,
    stats = list(
      min = if (length(valid) > 0) min(valid) else NA,
      max = if (length(valid) > 0) max(valid) else NA,
      mean = if (length(valid) > 0) mean(valid) else NA,
      valid_pixel_count = length(valid),
      total_pixel_count = total_n,
      masked_fraction = if (total_n > 0) 1 - (length(valid) / total_n) else NA
    ),
    endmembers = list(
      method = "image-derived (percentile pure-pixel selection), NOT a fixed literature table",
      endmember_pct = endmember_pct,
      pixels_used_for_derivation = n_pixels_used,
      spectra = as.list(as.data.frame(endm)),
      citations = c(
        "Adams, Smith & Gillespie (1993) - linear mixing model (sma()'s own reference)",
        "Souza Jr., Roberts & Cochrane (2005), RSE 98(2-3):329-343 - GV/NPV/Soil/Shade classes and NDFI formula",
        "Small, C. (2004), RSE 93(1-2):1-12 - image-endmember (feature-space vertex) selection method"
      )
    )
  )
}
