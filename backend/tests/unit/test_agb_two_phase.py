"""Unit tests for the two-phase (GEDI-calibrated) AGB estimator. Proves the variance
formula's census limit matches the (1-rho^2) theoretical shrink factor -- this is the
exact bug class (a wrong finite-population-correction limit) that would silently
under- or over-state Up,t for VM0047 reporting if it regressed."""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.sampling.agb_two_phase import optimal_allocation, two_phase_regression_estimate


@pytest.fixture
def synthetic_population():
    """5000 "pixels": x = GEDI-gap-filled proxy, y = correlated ground AGB (rho ~ 0.83)."""
    rng = np.random.default_rng(42)
    n_pop = 5000
    x = rng.normal(80, 20, n_pop)
    y = 0.9 * x + rng.normal(0, 8, n_pop)
    return rng, x, y


def _phase2_sample(rng, x, y, n2):
    idx = rng.choice(len(x), size=n2, replace=False)
    return [
        {"plot_id": f"p{i}", "x_proxy": float(x[i]), "y_measured": float(y[i])} for i in idx
    ]


def test_census_variance_matches_one_minus_rho_squared_shrink(synthetic_population):
    rng, x, y = synthetic_population
    phase1_mean = float(x.mean())
    phase2 = _phase2_sample(rng, x, y, n2=20)

    result = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=None)
    naive_srs_var = float(np.var([p["y_measured"] for p in phase2], ddof=1)) / len(phase2)

    assert result.variance < naive_srs_var
    assert math.isclose(result.variance / naive_srs_var, 1 - result.rho**2, rel_tol=0.05)


def test_calibrated_mean_tracks_population_truth(synthetic_population):
    rng, x, y = synthetic_population
    phase1_mean = float(x.mean())
    phase2 = _phase2_sample(rng, x, y, n2=20)

    result = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=None)
    assert abs(result.y_reg_mean - float(y.mean())) < 10.0
    assert result.df == len(phase2) - 2
    assert result.up_t_pct > 0


def test_finite_phase1_n_gives_weaker_reduction_than_census(synthetic_population):
    rng, x, y = synthetic_population
    phase1_mean = float(x.mean())
    phase2 = _phase2_sample(rng, x, y, n2=25)

    census = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=None)
    finite = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=100)
    same_as_n2 = two_phase_regression_estimate(phase2, phase1_mean, phase1_n=25)

    assert census.variance < finite.variance
    # phase1_n == n2 -> fpc = 0 -> no benefit at all, reduces to plain-SRS variance
    naive_srs_var = float(np.var([p["y_measured"] for p in phase2], ddof=1)) / len(phase2)
    assert same_as_n2.variance == pytest.approx(naive_srs_var)


def test_rejects_too_few_plots_and_constant_proxy():
    with pytest.raises(ValueError, match="at least 3"):
        two_phase_regression_estimate([{"plot_id": "a", "x_proxy": 5, "y_measured": 1}], 5.0)

    constant_x = [
        {"plot_id": "a", "x_proxy": 5, "y_measured": 1},
        {"plot_id": "b", "x_proxy": 5, "y_measured": 2},
        {"plot_id": "c", "x_proxy": 5, "y_measured": 3},
    ]
    with pytest.raises(ValueError, match="constant"):
        two_phase_regression_estimate(constant_x, phase1_mean=5.0)


def test_optimal_allocation_favours_phase1_when_it_is_cheap():
    n1, n2 = optimal_allocation(
        rho_prior=0.83, sy_prior=14.6, y_mean_prior=72.0,
        target_up_t_pct=8.0, cost_ratio_c1_c2=0.02,
    )
    assert n2 > 0
    assert n1 > n2  # phase-1 is ~50x cheaper -> allocation should lean heavily on it


def test_optimal_allocation_zero_correlation_reduces_to_plain_srs_size():
    _, n2_no_gain = optimal_allocation(
        rho_prior=0.0, sy_prior=14.6, y_mean_prior=72.0,
        target_up_t_pct=8.0, cost_ratio_c1_c2=0.02,
    )
    target_se = 8.0 / 100.0 * 72.0 / 1.645
    expected = math.ceil(14.6**2 / target_se**2)
    assert n2_no_gain == expected
