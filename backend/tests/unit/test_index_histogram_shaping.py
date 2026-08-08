"""Pure-logic tests for the two pieces of gee_analysis_service.py that turn a
GEE combined-reducer pass into the `distribution` field on a vegetation-index
result (Wave: enriched vegetation-index results):

  * `_shape_index_histogram(rows)` - genuinely pure Python, no `ee` import
    touched at all. Turns ee.Reducer.fixedHistogram()'s raw
    `[[lower_edge, count], ...]` rows into `{bin_edges, counts}`. All of this
    file's geospatial-correctness checks (bin-edge count, bin-width
    arithmetic, no pixel silently dropped or double-counted, explicit int/
    float coercion) live in the tests for this function.

  * `_index_stats_reducer()` - builds an `ee.Reducer` combinator tree. This
    DOES touch `ee`, and a bare call (verified directly - see
    `test_index_stats_reducer_raises_without_any_session_at_all` below)
    raises "Earth Engine client library not initialized" even though it
    never leaves the process: `ee.Reducer.mean()` etc. resolve against the
    algorithm signature list the FIRST time any `ee.Reducer`/`ee.Image`/...
    method is called, and that resolution goes through `ee.Initialize()`
    normally. `ee`'s own test suite (`ee/apitestcase.py`, shipped in this
    venv) works around exactly this by installing a LOCAL, file-based
    algorithm list (`ee/tests/algorithms.json`) and stubbing out the network
    resource installer before calling `ee.Initialize()` - no credentials, no
    HTTP call, offline-safe. `_local_ee_session()` below does the same thing
    so `_index_stats_reducer()` can be constructed and its shape asserted on
    without a live GEE session, matching how the rest of this file's sibling
    (`test_gee_point_query.py`) keeps `ee` fully out of the leaf-most layer
    it can."""
from __future__ import annotations

import pytest

from app.services.gee_analysis_service import (
    _VEG_INDEX_HISTOGRAM_BINS,
    _VEG_INDEX_HISTOGRAM_RANGE,
    _index_stats_reducer,
    _shape_index_histogram,
)

# --------------------------------------------------- _shape_index_histogram


def test_shape_index_histogram_none_rows_returns_empty_arrays():
    assert _shape_index_histogram(None) == {"bin_edges": [], "counts": []}


def test_shape_index_histogram_empty_rows_returns_empty_arrays():
    assert _shape_index_histogram([]) == {"bin_edges": [], "counts": []}


def _canned_rows() -> list[list[float]]:
    """20 rows spanning -1.0 to 0.9 in 0.1 steps (the real
    ee.Reducer.fixedHistogram(-1, 1, 20) shape), engineered so exactly 60% of
    the 900 total pixels fall in the two bins covering [0.4, 0.6): all 18
    other bins get count=20 (18*20=360=40%), the 0.4 and 0.5 bins get
    count=270 each (2*270=540=60%). Every number here is picked by hand, not
    derived from the function under test, so the assertions below are a real
    check, not a tautology."""
    edges = [round(-1.0 + 0.1 * i, 10) for i in range(20)]
    counts = [20] * 20
    counts[14] = 270  # bin lower edge 0.4
    counts[15] = 270  # bin lower edge 0.5
    assert edges[14] == 0.4 and edges[15] == 0.5  # sanity on the hand-picked indices
    return [[e, c] for e, c in zip(edges, counts, strict=True)]


def test_shape_index_histogram_bin_edges_has_counts_plus_one_entries():
    rows = _canned_rows()
    out = _shape_index_histogram(rows)
    assert len(out["counts"]) == 20
    assert len(out["bin_edges"]) == 21  # every bucket's lower edge + one final upper edge


def test_shape_index_histogram_last_edge_is_computed_from_bin_width_not_echoed():
    # The bug this guards: naively using the last ROW's lower edge (0.9) as
    # the final bin_edges entry instead of lower_edge + bin_width (0.9 + 0.1
    # = 1.0) would silently collapse the last bucket to zero width.
    rows = _canned_rows()
    out = _shape_index_histogram(rows)
    assert out["bin_edges"][-1] == 1.0
    assert out["bin_edges"][-1] != rows[-1][0]  # must NOT just equal the last row's lower edge
    assert out["bin_edges"] == [round(-1.0 + 0.1 * i, 10) for i in range(21)]


def test_shape_index_histogram_no_pixels_dropped_or_double_counted():
    rows = _canned_rows()
    total_in = sum(r[1] for r in rows)
    assert total_in == 900
    out = _shape_index_histogram(rows)
    assert sum(out["counts"]) == total_in  # exact match, not "close enough"


def test_shape_index_histogram_known_60_percent_bulk_lands_in_the_expected_two_bins():
    rows = _canned_rows()
    out = _shape_index_histogram(rows)
    # bins at index 14 (edge 0.4) and 15 (edge 0.5) - i.e. the [0.4, 0.6) span.
    bulk = out["counts"][14] + out["counts"][15]
    assert bulk == 540
    assert bulk / sum(out["counts"]) == pytest.approx(0.6)


def test_shape_index_histogram_counts_are_int_and_bin_edges_are_float():
    # GEE's raw reduceRegion().getInfo() output can hand back JSON numbers
    # that round-trip as float even for integer pixel counts - the
    # implementation explicitly coerces both, verify it holds.
    out = _shape_index_histogram([[-1.0, 20.0], [-0.9, 5.0]])
    assert all(isinstance(c, int) for c in out["counts"])
    assert all(isinstance(e, float) for e in out["bin_edges"])
    assert out["counts"] == [20, 5]


def test_shape_index_histogram_single_row_gives_zero_width_final_bin():
    # len(rows) == 1 -> no second row to derive a bin width from; the
    # function's own documented fallback is bin_width=0.0, so both edges of
    # the one bucket coincide rather than raising an IndexError.
    out = _shape_index_histogram([[0.5, 7]])
    assert out == {"bin_edges": [0.5, 0.5], "counts": [7]}


# ------------------------------------------------------------- _index_stats_reducer


@pytest.fixture
def local_ee_session():
    """Initializes `ee` against the LOCAL algorithm list the earthengine-api
    package ships for its own tests (ee/tests/algorithms.json via
    ee.apitestcase.GetAlgorithms) instead of a live GEE backend - no
    credentials, no network call, safe to run anywhere this venv exists.
    Constructing an `ee.Reducer` combinator tree (as `_index_stats_reducer()`
    does) needs SOME initialized session because `ee.Reducer.mean()` et al.
    resolve against the algorithm signature list on first use; it does NOT
    need a real one, since nothing here calls `.getInfo()` (no compute
    round trip happens - only the expression graph is built and inspected)."""
    import ee
    from ee import apitestcase

    ee.data._install_cloud_api_resource = lambda: None
    ee.data.getAlgorithms = apitestcase.GetAlgorithms
    ee.Initialize(None, "", project="my-project")
    yield
    ee.Reset()


def test_index_stats_reducer_raises_without_any_session_at_all():
    # Documents WHY `local_ee_session` exists: a completely bare call (no
    # ee.Initialize() anywhere) is not "safe" - it raises. This is the
    # behavior qa-backend-tester was asked to check.
    import ee

    ee.Reset()
    with pytest.raises(ee.EEException, match="not initialized"):
        _index_stats_reducer()


def _func_name(obj) -> str:
    return obj.func.getSignature()["name"]


def test_index_stats_reducer_is_one_combine_tree_of_mean_stddev_minmax_histogram(
    local_ee_session,
):
    """Walks the actual `ee.Reducer` expression graph `_index_stats_reducer()`
    builds and asserts its exact shape - mean.combine(stdDev).combine(minMax)
    .combine(fixedHistogram), sharedInputs=True at every level, and the
    histogram's min/max/steps matching this module's own
    _VEG_INDEX_HISTOGRAM_RANGE/_BINS constants exactly (not a hardcoded
    duplicate of -1/1/20) - so a change to the module constants can't
    silently drift out of sync with what the reducer actually asks GEE for."""
    reducer = _index_stats_reducer()

    assert _func_name(reducer) == "Reducer.combine"
    assert reducer.args["sharedInputs"] is True

    histogram = reducer.args["reducer2"]
    assert _func_name(histogram) == "Reducer.fixedHistogram"
    assert histogram.args["min"]._number == _VEG_INDEX_HISTOGRAM_RANGE[0]
    assert histogram.args["max"]._number == _VEG_INDEX_HISTOGRAM_RANGE[1]
    assert histogram.args["steps"]._number == _VEG_INDEX_HISTOGRAM_BINS

    mean_stddev_minmax = reducer.args["reducer1"]
    assert _func_name(mean_stddev_minmax) == "Reducer.combine"
    assert mean_stddev_minmax.args["sharedInputs"] is True
    assert _func_name(mean_stddev_minmax.args["reducer2"]) == "Reducer.minMax"

    mean_stddev = mean_stddev_minmax.args["reducer1"]
    assert _func_name(mean_stddev) == "Reducer.combine"
    assert mean_stddev.args["sharedInputs"] is True
    assert _func_name(mean_stddev.args["reducer2"]) == "Reducer.stdDev"
    assert _func_name(mean_stddev.args["reducer1"]) == "Reducer.mean"


def test_index_stats_reducer_histogram_range_matches_module_constants(local_ee_session):
    # Sanity on the constants themselves, independent of the reducer walk
    # above - if these ever change, the histogram-range assertions above are
    # still checking the RIGHT numbers, not stale hardcoded ones.
    assert _VEG_INDEX_HISTOGRAM_RANGE == (-1.0, 1.0)
    assert _VEG_INDEX_HISTOGRAM_BINS == 20
