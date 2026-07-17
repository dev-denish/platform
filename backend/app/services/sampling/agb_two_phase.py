"""
Two-phase (double) sampling estimator for AGB, calibrated against a GEDI-gap-filled
remote-sensing proxy, feeding VM0047's Up,t parameter (Section 9.2, Eq. 27-31).

Why this exists: VM0047 requires field-plot-measured AGB, with Up,t = the 90% CI
half-width of the plot mean, expressed as % of the mean. GEDI L4A biomass is NOT a
substitute for field plots -- it is legitimate ONLY as an auxiliary variable that
shrinks Up,t (or the field-plot count needed for the same Up,t) via classical double
sampling for regression: Cochran, "Sampling Techniques" (3rd ed., 1977), Sec. 12.7.
This module implements exactly that one estimator -- nothing more generic.

  Phase 1 (cheap, large/n1->census): the GEDI-gap-filled AGB raster gives an RS-proxy
    value x at every pixel in the AOI. See ../../../scripts/gee_phase1_agb_proxy.py
    for how that raster and phase1_mean/phase1_n are produced.
  Phase 2 (expensive, small n2): a subsample of field plots gets BOTH the RS-proxy x
    (sampled from the same raster at the plot location) AND ground-measured AGB y.
    y = a + b*x is fit on those n2 pairs and used to calibrate the phase-1 mean.

This cuts the variance of the AGB mean below plain field-plot-only variance by a
factor of roughly (1 - rho^2), for the SAME field-plot count -- or lets a target Up,t
be hit with fewer plots. `variance` below is Cochran eq. 12.30; `optimal_allocation`
is the cost-optimal (n1, n2) from eq. 12.34-12.36 of the same section.

NOT pre-approved for VVB reporting as-is: VM0047 itself does not spell out
double-sampling formulas. This is state-of-the-art forest-inventory practice applied
to satisfy VM0047's error-propagation intent, and needs `carbon-mrv-vm0047` sign-off
before an Up,t produced here goes into a monitoring report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class TwoPhaseResult:
    n1: float  # phase-1 sample size backing phase1_mean; math.inf if a true census
    n2: int  # phase-2 field-plot count
    phase1_mean: float  # x-bar', RS-proxy mean over the AOI/stratum, Mg/ha
    y_reg_mean: float  # calibrated two-phase AGB mean, Mg/ha -- the reported estimate
    variance: float  # V(y_reg), (Mg/ha)^2
    se: float  # sqrt(variance), Mg/ha
    rho: float  # phase-2 correlation between RS proxy and ground AGB
    slope: float
    intercept: float
    df: int  # n2 - 2, degrees of freedom for the Student's-t critical value
    up_t_pct: float  # VM0047 Up,t: t(alpha/2, df) * se / y_reg_mean * 100


def two_phase_regression_estimate(
    phase2_plots: list[dict],
    phase1_mean: float,
    phase1_n: float | None = None,
    alpha: float = 0.10,
) -> TwoPhaseResult:
    """
    phase2_plots: list of {"plot_id": str, "x_proxy": float, "y_measured": float}.
        x_proxy is the GEDI-gap-filled AGB (Mg/ha) sampled from the phase-1 raster AT
        the plot's location (same units/pixel grid as phase1_mean). y_measured is the
        ground-measured plot AGB (Mg/ha) from the field-plot tracker.
    phase1_mean: x-bar', the RS-proxy mean over the whole AOI/stratum (Mg/ha).
    phase1_n: independent phase-1 unit count backing phase1_mean. None = census (the
        GEDI-gap-filled raster covers the AOI wall-to-wall, so there is no phase-1
        sampling-error term). If phase-1 was itself a subsample (e.g. one point per
        systematic grid cell), pass that count -- and see the caveat about spatial
        autocorrelation inflating the *effective* n1 in the design writeup.
    alpha: VM0047 uses a 90% CI -> alpha=0.10 (two-sided, alpha/2 per tail).

    Returns a TwoPhaseResult; `up_t_pct` is what flows into VM0047 Eq. 27's UNCt term
    as the Up,t input for this pool/stratum/period.
    """
    n2 = len(phase2_plots)
    if n2 < 3:
        raise ValueError("need at least 3 phase-2 plots to fit a 2-parameter regression")

    x = np.array([p["x_proxy"] for p in phase2_plots], dtype=float)
    y = np.array([p["y_measured"] for p in phase2_plots], dtype=float)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise ValueError("phase2_plots contains non-finite x_proxy/y_measured values")

    xbar, ybar = float(x.mean()), float(y.mean())
    sxx = float(np.sum((x - xbar) ** 2) / (n2 - 1))
    syy = float(np.sum((y - ybar) ** 2) / (n2 - 1))
    sxy = float(np.sum((x - xbar) * (y - ybar)) / (n2 - 1))
    if sxx == 0.0:
        raise ValueError("phase-2 RS-proxy values are constant -- can't fit a regression on them")

    b = sxy / sxx
    a = ybar - b * xbar
    rho = sxy / math.sqrt(sxx * syy) if syy > 0 else 0.0

    y_reg = ybar + b * (phase1_mean - xbar)
    if y_reg <= 0:
        raise ValueError(f"calibrated mean is non-positive ({y_reg:.4g} Mg/ha) -- check inputs")

    # fpc -> (1/n2 - 1/n1); phase1_n=None means a census (n1 -> inf, so 1/n1 -> 0),
    # NOT that the whole correction term vanishes.
    fpc = 1.0 / n2 if phase1_n is None else (1.0 / n2 - 1.0 / phase1_n)
    variance = syy / n2 - (rho**2) * syy * fpc
    if variance <= 0:
        raise ValueError(
            f"non-positive variance ({variance:.4g}) from n2={n2} plots -- rho/Sy^2 "
            "estimate is too noisy at this sample size; add plots before reporting"
        )
    se = math.sqrt(variance)

    df = n2 - 2
    t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df))
    up_t_pct = t_crit * se / y_reg * 100.0

    return TwoPhaseResult(
        n1=phase1_n if phase1_n is not None else math.inf,
        n2=n2,
        phase1_mean=phase1_mean,
        y_reg_mean=y_reg,
        variance=variance,
        se=se,
        rho=rho,
        slope=b,
        intercept=a,
        df=df,
        up_t_pct=up_t_pct,
    )


def optimal_allocation(
    rho_prior: float,
    sy_prior: float,
    y_mean_prior: float,
    target_up_t_pct: float,
    cost_ratio_c1_c2: float,
    z: float = 1.645,
) -> tuple[float, int]:
    """
    Cost-optimal double-sampling allocation (Cochran 1977, Sec. 12.7): the minimum-
    cost (n1, n2) that hits `target_up_t_pct`, given a PRIOR estimate of rho and Sy
    (from a previous monitoring period, a pilot plot set, or published GEDI-vs-plot
    AGB correlations for a similar forest type). This is a season-planning tool, not
    the final estimator -- run `two_phase_regression_estimate` on the plots actually
    collected to get the number that gets reported.

    cost_ratio_c1_c2: cost of one phase-1 unit (RS-proxy extraction -- near zero,
        the raster is already computed) divided by cost of one phase-2 unit (a field
        plot: travel + crew-days + DBH/height measurement). 0 means phase-1 is free
        -> use the whole wall-to-wall raster as phase1_mean (n1 -> inf), don't
        subsample it.

    ponytail: uses the z=1.645 normal quantile (90% two-sided) instead of the exact
    Student's-t, because df=n2-2 isn't known until n2 is solved for -- standard
    forest-inventory planning shortcut (t -> z as df grows). Treat the returned n2 as
    a lower bound for the field-plot budget, and always recompute the FINAL Up,t with
    `two_phase_regression_estimate`'s exact t on the n2 actually achieved.

    Returns (n1, n2). n1 is math.inf when cost_ratio_c1_c2 == 0 (free phase-1).
    """
    if not (0.0 <= rho_prior < 1.0):
        raise ValueError("rho_prior must be in [0, 1)")
    if cost_ratio_c1_c2 < 0:
        raise ValueError("cost_ratio_c1_c2 must be >= 0")

    target_se = target_up_t_pct / 100.0 * y_mean_prior / z
    v0 = target_se**2
    gain = sy_prior**2 * (1.0 - rho_prior**2)  # residual variance left after calibration
    corr_part = sy_prior**2 * rho_prior**2  # variance explained by the RS proxy

    if cost_ratio_c1_c2 == 0:
        return math.inf, math.ceil(gain / v0)

    root = math.sqrt(gain) + math.sqrt(corr_part * cost_ratio_c1_c2)
    n2 = math.sqrt(gain) * root / v0
    n1 = math.sqrt(corr_part / cost_ratio_c1_c2) * root / v0
    return n1, math.ceil(n2)


def _demo() -> None:
    """Synthetic sanity check: two-phase variance must beat plain-SRS variance by
    ~(1-rho^2), and the estimator must recover a known population mean. Run directly:
    `python -m app.services.sampling.agb_two_phase`."""
    rng = np.random.default_rng(42)
    n_pop = 5000
    true_x = rng.normal(80, 20, n_pop)  # GEDI-gap-filled AGB proxy, Mg/ha
    true_y = 0.9 * true_x + rng.normal(0, 8, n_pop)  # ground AGB, correlated w/ proxy
    pop_mean_y = float(true_y.mean())
    phase1_mean = float(true_x.mean())  # wall-to-wall census -> phase1_n=None

    idx = rng.choice(n_pop, size=20, replace=False)  # 20 field plots = phase 2
    phase2 = [
        {"plot_id": f"p{i}", "x_proxy": float(true_x[i]), "y_measured": float(true_y[i])}
        for i in idx
    ]

    result = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=None)
    naive_srs_var = float(np.var([p["y_measured"] for p in phase2], ddof=1)) / len(phase2)

    assert abs(result.y_reg_mean - pop_mean_y) < 10, "calibrated mean should track truth"
    assert result.variance < naive_srs_var, "two-phase variance must beat plain SRS"
    assert math.isclose(result.variance / naive_srs_var, 1 - result.rho**2, rel_tol=0.05), (
        "variance shrink should match the (1-rho^2) theoretical factor"
    )
    assert result.up_t_pct > 0 and result.df == len(phase2) - 2

    n1, n2 = optimal_allocation(
        rho_prior=result.rho, sy_prior=math.sqrt(naive_srs_var * len(phase2)),
        y_mean_prior=pop_mean_y, target_up_t_pct=10.0, cost_ratio_c1_c2=0.02,
    )
    assert n2 > 0 and n1 > n2, "phase-1 (cheap) should always be allocated more units than phase-2"

    print(f"y_reg_mean={result.y_reg_mean:.2f} Mg/ha  Up,t={result.up_t_pct:.2f}%  "
          f"rho={result.rho:.3f}  planned n1={n1:.0f} n2={n2}")


if __name__ == "__main__":
    _demo()
