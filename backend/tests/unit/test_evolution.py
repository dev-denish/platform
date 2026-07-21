"""Unit tests for Landscape Evolution's pure computation (Phase 3 Wave G).

`compute_evolution` takes exactly the row shapes LayerRepository.
list_for_project / KpiRepository.for_project already return, so it's
tested directly here (no live database needed) - the real project this
wave was built against (RA-GHG, 3 real classified dates) has every class
present at every date with no zeros, so it can't exercise the None-vs-zero
or divide-by-zero paths at all; these fixtures construct exactly those
gaps on purpose.
"""
from __future__ import annotations

import uuid

from app.services.project_service import compute_evolution


def _layer(date, legend, layer_id=None):
    return {
        "layer_id": layer_id or uuid.uuid4(),
        "date_processed": date,
        "class_legend": legend,
    }


def _kpi(layer_id, metric_name, value):
    return {"layer_id": layer_id, "metric_name": metric_name, "value": value}


LEGEND_AB = {"1": {"label": "Forest"}, "2": {"label": "Water"}}


def test_two_dates_basic_change():
    forest, water = uuid.uuid4(), None
    l1 = _layer("2020-01-01", LEGEND_AB)
    l2 = _layer("2021-01-01", LEGEND_AB)
    kpis = [
        _kpi(l1["layer_id"], "class_area_forest", 100.0),
        _kpi(l1["layer_id"], "class_area_water", 20.0),
        _kpi(l2["layer_id"], "class_area_forest", 80.0),
        _kpi(l2["layer_id"], "class_area_water", 20.0),
    ]
    result = compute_evolution(uuid.uuid4(), [l1, l2], kpis)

    assert result.applicable is True
    assert result.dates == ["2020-01-01", "2021-01-01"]
    forest_row = next(c for c in result.classes if c.metric_name == "class_area_forest")
    assert forest_row.area_by_date_ha == {"2020-01-01": 100.0, "2021-01-01": 80.0}
    assert forest_row.first_vs_last.net_change_ha == -20.0
    assert forest_row.first_vs_last.pct_change == -20.0
    # Exactly 2 eligible dates -> consecutive has exactly 1 pair, identical
    # to first_vs_last (the frontend decides not to show a redundant toggle
    # for this case, not the API).
    assert len(forest_row.consecutive) == 1
    assert forest_row.consecutive[0].net_change_ha == forest_row.first_vs_last.net_change_ha


def test_three_dates_consecutive_pairs_differ_from_first_vs_last():
    l1 = _layer("2015-01-01", LEGEND_AB)
    l2 = _layer("2020-01-01", LEGEND_AB)
    l3 = _layer("2025-01-01", LEGEND_AB)
    kpis = [
        _kpi(l1["layer_id"], "class_area_forest", 100.0),
        _kpi(l2["layer_id"], "class_area_forest", 60.0),
        _kpi(l3["layer_id"], "class_area_forest", 90.0),
    ]
    result = compute_evolution(uuid.uuid4(), [l1, l2, l3], kpis)

    assert result.dates == ["2015-01-01", "2020-01-01", "2025-01-01"]
    row = next(c for c in result.classes if c.metric_name == "class_area_forest")
    assert row.first_vs_last.net_change_ha == -10.0  # 100 -> 90 overall
    assert len(row.consecutive) == 2
    assert row.consecutive[0].net_change_ha == -40.0  # 100 -> 60
    assert row.consecutive[1].net_change_ha == 30.0  # 60 -> 90


def test_class_missing_from_one_dates_legend_is_null_not_zero():
    """The core None-vs-zero distinction: a class that simply isn't part of
    a date's legend must read null there, not a fabricated 0 - and any
    change touching that date must also be null (not computable), not a
    fake number treating the gap as zero area."""
    legend_2015 = {"1": {"label": "Forest"}}
    legend_2020 = {"1": {"label": "Forest"}, "2": {"label": "Water"}}  # Water is new
    l1 = _layer("2015-01-01", legend_2015)
    l2 = _layer("2020-01-01", legend_2020)
    kpis = [
        _kpi(l1["layer_id"], "class_area_forest", 100.0),
        _kpi(l2["layer_id"], "class_area_forest", 90.0),
        _kpi(l2["layer_id"], "class_area_water", 15.0),
    ]
    result = compute_evolution(uuid.uuid4(), [l1, l2], kpis)

    water_row = next(c for c in result.classes if c.metric_name == "class_area_water")
    assert water_row.area_by_date_ha == {"2015-01-01": None, "2020-01-01": 15.0}
    assert water_row.first_vs_last.net_change_ha is None
    assert water_row.first_vs_last.pct_change is None


def test_class_defined_but_zero_measured_pixels_is_real_zero_not_null():
    """compute_stats never writes a KPI row for a legend-defined class with
    zero matching pixels - the legend itself (not KPI presence) is what
    proves this class WAS defined at this date, so it must read as a real
    0.0, not null."""
    legend = {"1": {"label": "Forest"}, "2": {"label": "Water"}}
    l1 = _layer("2020-01-01", legend)
    l2 = _layer("2021-01-01", legend)
    kpis = [
        _kpi(l1["layer_id"], "class_area_forest", 100.0),
        # no class_area_water KPI row for l1 at all - zero pixels matched
        _kpi(l2["layer_id"], "class_area_forest", 90.0),
        _kpi(l2["layer_id"], "class_area_water", 10.0),
    ]
    result = compute_evolution(uuid.uuid4(), [l1, l2], kpis)

    water_row = next(c for c in result.classes if c.metric_name == "class_area_water")
    assert water_row.area_by_date_ha["2020-01-01"] == 0.0
    assert water_row.area_by_date_ha["2020-01-01"] is not None


def test_zero_to_positive_pct_change_is_new_not_infinity():
    legend = {"1": {"label": "Water"}}
    l1 = _layer("2020-01-01", legend)
    l2 = _layer("2021-01-01", legend)
    kpis = [_kpi(l2["layer_id"], "class_area_water", 25.0)]  # l1 has none -> real 0.0
    result = compute_evolution(uuid.uuid4(), [l1, l2], kpis)

    row = result.classes[0]
    assert row.area_by_date_ha == {"2020-01-01": 0.0, "2021-01-01": 25.0}
    assert row.first_vs_last.pct_change == "new"
    assert row.first_vs_last.net_change_ha == 25.0


def test_zero_to_zero_pct_change_is_real_zero_not_new():
    legend = {"1": {"label": "Water"}}
    l1 = _layer("2020-01-01", legend)
    l2 = _layer("2021-01-01", legend)
    result = compute_evolution(uuid.uuid4(), [l1, l2], [])  # no KPI rows at all -> both 0.0

    row = result.classes[0]
    assert row.area_by_date_ha == {"2020-01-01": 0.0, "2021-01-01": 0.0}
    assert row.first_vs_last.pct_change == 0.0
    assert row.first_vs_last.net_change_ha == 0.0


def test_positive_to_zero_is_ordinary_negative_hundred_not_a_special_case():
    """Shrinking TO zero divides BY the (non-zero) starting value - an
    entirely ordinary division, not the divide-by-zero case at all."""
    legend = {"1": {"label": "Forest"}}
    l1 = _layer("2020-01-01", legend)
    l2 = _layer("2021-01-01", legend)
    kpis = [_kpi(l1["layer_id"], "class_area_forest", 50.0)]  # l2 has none -> real 0.0
    result = compute_evolution(uuid.uuid4(), [l1, l2], kpis)

    row = result.classes[0]
    assert row.first_vs_last.pct_change == -100.0
    assert row.first_vs_last.net_change_ha == -50.0


def test_one_eligible_date_is_not_applicable():
    l1 = _layer("2020-01-01", LEGEND_AB)
    result = compute_evolution(uuid.uuid4(), [l1], [_kpi(l1["layer_id"], "class_area_forest", 100.0)])

    assert result.applicable is False
    assert result.dates == ["2020-01-01"]
    assert result.classes == []


def test_zero_eligible_dates_is_not_applicable_not_an_error():
    raw_layer = {"layer_id": uuid.uuid4(), "date_processed": "2020-01-01", "class_legend": None}
    result = compute_evolution(uuid.uuid4(), [raw_layer], [])

    assert result.applicable is False
    assert result.dates == []
    assert result.classes == []


def test_raw_imagery_dates_excluded_from_eligibility():
    """A project mixing classified LULC with raw/unclassified imagery across
    dates only compares the classified ones - the raw dates must not appear
    in `dates` at all, even though real layers exist for them."""
    raw1 = {"layer_id": uuid.uuid4(), "date_processed": "2015-06-01", "class_legend": None}
    raw2 = {"layer_id": uuid.uuid4(), "date_processed": "2015-06-15", "class_legend": {}}  # empty dict, no real classes either
    l1 = _layer("2020-01-01", LEGEND_AB)
    l2 = _layer("2021-01-01", LEGEND_AB)
    kpis = [
        _kpi(l1["layer_id"], "class_area_forest", 100.0),
        _kpi(l2["layer_id"], "class_area_forest", 90.0),
    ]
    result = compute_evolution(uuid.uuid4(), [raw1, raw2, l1, l2], kpis)

    assert result.applicable is True
    assert result.dates == ["2020-01-01", "2021-01-01"]
    assert "2015-06-01" not in result.dates
    assert "2015-06-15" not in result.dates


def test_duplicate_layers_sharing_a_date_pick_one_deterministically():
    """Real data can have 2+ layers share a date - must not crash or double
    the date in the list, and must pick a stable, single representative."""
    dup_a = _layer("2020-01-01", LEGEND_AB)
    dup_b = _layer("2020-01-01", LEGEND_AB)
    l2 = _layer("2021-01-01", LEGEND_AB)
    kpis = [
        _kpi(dup_a["layer_id"], "class_area_forest", 100.0),
        _kpi(dup_b["layer_id"], "class_area_forest", 999.0),  # must NOT win/blend
        _kpi(l2["layer_id"], "class_area_forest", 90.0),
    ]
    result = compute_evolution(uuid.uuid4(), [dup_a, dup_b, l2], kpis)

    assert result.dates == ["2020-01-01", "2021-01-01"]  # not duplicated
    row = result.classes[0]
    assert row.area_by_date_ha["2020-01-01"] == 100.0  # the first one (dup_a) won, deterministically
