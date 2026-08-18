# Self-check for plumber.R's NDFI logic. Run from THIS directory with:
#   Rscript test_ndfi.R
#
# Builds a synthetic 2-pixel, 6-band raster from KNOWN GV/NPV/Soil/Shade
# fractions times four made-up (test-fixture-only, NOT real Sentinel-2)
# endmember spectra, runs it through the REAL ForesToolboxRS::sma() and this
# module's own ndfi_from_fractions(), and asserts the recovered fractions and
# NDFI match hand-computed expected values (verified independently in Python/
# numpy before writing this file - see the task's chat history).
#
# This checks the sma()+NDFI-formula pipeline itself, not the percentile
# pure-pixel derive_endmembers() heuristic - that heuristic is inherently
# statistical (depends on real scene content) and isn't something a 2-pixel
# exact-value assertion can honestly validate. Real endmember quality needs
# review against actual imagery, see plumber.R's module docstring.

suppressPackageStartupMessages({
  library(raster)
  library(ForesToolboxRS)
})
source("plumber.R")

# Test-fixture-only spectra (arbitrary numbers, NOT sourced from any
# literature or real imagery - only used to construct a synthetic pixel
# whose true fractions we already know).
gv    <- c(200, 300, 100, 4000, 1200, 500)
npv   <- c(1500, 1600, 1400, 3000, 4000, 2500)
soil  <- c(2500, 3000, 3200, 3800, 4500, 3600)
shade <- c(50, 60, 55, 70, 65, 60)
endm <- rbind(gv, npv, soil, shade)
rownames(endm) <- ENDMEMBER_NAMES
colnames(endm) <- BAND_NAMES

known_fractions <- list(
  c(gv = 0.7, npv = 0.1, soil = 0.1, shade = 0.1),
  c(gv = 0.1, npv = 0.1, soil = 0.1, shade = 0.7)
)
expected_ndfi <- c(0.5909090909, 0.25)  # hand-computed via numpy, see plumber.R comment history

M <- t(endm)
pixel_spectra <- sapply(known_fractions, function(f) M %*% f)  # 6 x 2

r <- raster::stack(lapply(1:6, function(b) {
  raster::raster(matrix(pixel_spectra[b, ], nrow = 1, ncol = 2))
}))
names(r) <- BAND_NAMES

fractions <- ForesToolboxRS::sma(img = r, endm = endm, verbose = FALSE)
ndfi <- ndfi_from_fractions(
  fractions[["gv"]], fractions[["npv"]], fractions[["soil"]], fractions[["shade"]]
)
ndfi_vals <- raster::values(ndfi)

stopifnot(all(ndfi_vals >= -1 & ndfi_vals <= 1))
stopifnot(all(abs(ndfi_vals - expected_ndfi) < 1e-6))

cat("OK: NDFI formula matches hand-computed values and stays within [-1, 1]:\n")
print(ndfi_vals)

# Guard check: sma() is unconstrained least-squares and CAN return
# out-of-[0,1] fractions on unresolvable pixels (hit for real in a smoke
# test against a near-pure-shade synthetic scene) - confirm the masking
# guard in ndfi_from_fractions() actually NAs those out rather than letting
# NDFI blow up past [-1, 1], while leaving a normal in-range pixel alone.
# Third pixel is the specific bug an earlier version of this guard missed:
# raw gv=0.05 and shade=0.98 each individually look like plausible fractions,
# but the DERIVED gvs = gv/(1-shade) = 0.05/0.02 = 2.5 blows past 1 - the
# guard has to check gvs itself, not the raw inputs, or this slips through.
guard_check <- ndfi_from_fractions(
  gv    = c(0.4, 0.01, 0.05),
  npv   = c(0.2, 0.0,  0.1),
  soil  = c(0.2, 0.0,  0.1),
  shade = c(0.2, 1.03, 0.98)
)
stopifnot(is.na(guard_check[2]))
stopifnot(is.na(guard_check[3]))
stopifnot(!is.na(guard_check[1]) && guard_check[1] >= -1 && guard_check[1] <= 1)
cat("OK: out-of-range fractions (direct and derived-gvs) masked to NA, normal pixel unaffected:\n")
print(guard_check)
