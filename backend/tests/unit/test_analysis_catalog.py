"""Pure-logic tests for the static analysis registry (Wave: GEE analysis
registry) - no DB, no GEE. CATALOG is a constant tuple, not app state, so
these just prove its own internal invariants."""
from __future__ import annotations

from app.domain.analysis_catalog import CATALOG, REAL_ANALYSIS_IDS, get_catalog_entry


def test_catalog_has_exactly_ten_available_and_six_in_development_entries():
    available = [e for e in CATALOG if e["status"] == "available"]
    in_development = [e for e in CATALOG if e["status"] == "in-development"]
    assert len(available) == 10
    assert len(in_development) == 6
    assert len(CATALOG) == 16


def test_every_catalog_id_is_unique():
    ids = [e["id"] for e in CATALOG]
    assert len(ids) == len(set(ids))


def test_get_catalog_entry_returns_none_for_unknown_id():
    assert get_catalog_entry("not-a-real-analysis") is None


def test_get_catalog_entry_returns_the_matching_entry():
    entry = get_catalog_entry("hansen_gfc")
    assert entry is not None
    assert entry["status"] == "available"
    assert entry["name"] == "Global Forest Change (Hansen)"


def test_real_analysis_ids_matches_the_available_entries_exactly():
    available_ids = {e["id"] for e in CATALOG if e["status"] == "available"}
    assert available_ids == REAL_ANALYSIS_IDS


def test_every_available_entry_declares_a_valid_execution_mode():
    # GEEAnalysisService.refresh()/enqueue_refresh() each assert on this field
    # (see their own docstrings) - a missing or bogus value would only be
    # caught the first time someone actually ran that analysis.
    for entry in CATALOG:
        if entry["status"] != "available":
            continue
        assert entry.get("execution") in ("sync", "async"), entry["id"]


def test_the_five_vegetation_indices_are_available_and_async():
    veg_indices = {"ndvi", "evi", "savi", "mndwi", "nbr"}
    by_id = {e["id"]: e for e in CATALOG}
    for analysis_id in veg_indices:
        entry = by_id[analysis_id]
        assert entry["status"] == "available"
        assert entry["execution"] == "async"
        assert entry["category"] == "Vegetation Indices"
