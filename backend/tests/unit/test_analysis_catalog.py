"""Pure-logic tests for the static analysis registry (Wave: GEE analysis
registry) - no DB, no GEE. CATALOG is a constant tuple, not app state, so
these just prove its own internal invariants."""
from __future__ import annotations

from app.domain.analysis_catalog import CATALOG, REAL_ANALYSIS_IDS, get_catalog_entry


def test_catalog_has_exactly_twenty_six_available_and_seven_in_development_entries():
    # 10 original + 3 Raw Imagery (Wave: raw-imagery browsing) + 1 vnv_ndfi
    # (Wave: VNV Pipeline NDFI go-live) + 12 available vnv_* band indices
    # (Wave: VNV band indices) - vnv_nddi is the 13th but is
    # "in-development" (confirmed formula degeneracy, see its own entry).
    available = [e for e in CATALOG if e["status"] == "available"]
    in_development = [e for e in CATALOG if e["status"] == "in-development"]
    assert len(available) == 26
    assert len(in_development) == 7
    assert len(CATALOG) == 33


def test_only_the_three_raw_imagery_entries_are_year_selectable():
    year_selectable_ids = {e["id"] for e in CATALOG if e.get("year_selectable")}
    assert year_selectable_ids == {"s2_browse", "s1_browse", "landsat_browse"}


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


# ---------------------------------------------- Wave: analysis config and methodology


def test_exactly_the_seven_expected_ids_declare_a_config():
    configured_ids = {e["id"] for e in CATALOG if "config" in e}
    assert configured_ids == {"io_lulc", "modis_lulc", "ndvi", "evi", "savi", "mndwi", "nbr"}


def test_every_config_declaring_entry_is_status_available_and_execution_matches():
    by_id = {e["id"]: e for e in CATALOG}
    assert by_id["io_lulc"]["execution"] == "sync"
    assert by_id["modis_lulc"]["execution"] == "sync"
    for analysis_id in ("ndvi", "evi", "savi", "mndwi", "nbr"):
        assert by_id[analysis_id]["status"] == "available"
        assert by_id[analysis_id]["execution"] == "async"


def test_land_cover_configs_have_a_static_year_max_not_live():
    by_id = {e["id"]: e for e in CATALOG}
    assert by_id["io_lulc"]["config"] == {
        "year_mode_default": "single", "year_min": 2017, "year_max": 2023,
    }
    assert by_id["modis_lulc"]["config"] == {
        "year_mode_default": "single", "year_min": 2001, "year_max": 2023,
    }


def test_all_five_indices_share_the_identical_config_spec():
    by_id = {e["id"]: e for e in CATALOG}
    index_ids = ("ndvi", "evi", "savi", "mndwi", "nbr")
    specs = [by_id[i]["config"] for i in index_ids]
    assert all(spec == specs[0] for spec in specs)
    assert specs[0]["year_max"] is None  # ask current_veg_index_years() live, not static
    assert specs[0]["supported_combos"] == [("sentinel2", "cloud_score_plus")]


# ---------------------------------------------- Wave: VNV Pipeline NDFI go-live
# --------------------------------------------------- Wave: VNV band indices

_VNV_BAND_INDEX_IDS = {
    "vnv_ndvi", "vnv_evi", "vnv_savi", "vnv_ndwi", "vnv_mndwi", "vnv_ndmi",
    "vnv_nbr", "vnv_bsi", "vnv_ndbi", "vnv_arvi", "vnv_gndvi", "vnv_nddi", "vnv_psri",
}
# vnv_nddi is the one band index NOT status: "available" - see its own
# catalog entry comment (confirmed formula degeneracy on vegetated land).
_AVAILABLE_VNV_BAND_INDEX_IDS = _VNV_BAND_INDEX_IDS - {"vnv_nddi"}


def test_only_vnv_pipeline_entries_declare_a_non_gee_compute_source():
    non_gee_ids = {e["id"] for e in CATALOG if e.get("compute_source") == "vnv_pipeline"}
    assert non_gee_ids == {"vnv_ndfi"} | _VNV_BAND_INDEX_IDS
    for entry in CATALOG:
        if entry["id"] not in non_gee_ids:
            assert "compute_source" not in entry, entry["id"]


def test_vnv_ndfi_is_available_async_and_uncategorized_as_config():
    entry = get_catalog_entry("vnv_ndfi")
    assert entry is not None
    assert entry["status"] == "available"
    assert entry["execution"] == "async"
    assert "config" not in entry


def test_twelve_band_indices_are_available_async_vnv_pipeline_no_config():
    for analysis_id in _AVAILABLE_VNV_BAND_INDEX_IDS:
        entry = get_catalog_entry(analysis_id)
        assert entry is not None, analysis_id
        assert entry["status"] == "available"
        assert entry["execution"] == "async"
        assert entry["compute_source"] == "vnv_pipeline"
        assert entry["category"] == "VM0047 Compute — Band Indices"
        assert "config" not in entry


def test_vnv_nddi_is_in_development_despite_being_a_vnv_pipeline_entry():
    # Confirmed, real-data formula degeneracy (see vnv_band_indices.py's
    # nddi() docstring) - implemented but deliberately not runnable yet.
    # compute_source is still set (unlike every other in-development entry)
    # so the frontend's "vnv" tab lists it honestly under VNV Pipeline
    # rather than defaulting to "gee" or vanishing.
    entry = get_catalog_entry("vnv_nddi")
    assert entry is not None
    assert entry["status"] == "in-development"
    assert entry["compute_source"] == "vnv_pipeline"
    assert entry["category"] == "VM0047 Compute — Band Indices"
    assert "execution" not in entry
    assert "config" not in entry


def test_band_index_ids_are_unique_from_the_gee_vegetation_indices():
    # vnv_ndvi/vnv_evi/vnv_savi/vnv_mndwi/vnv_nbr are distinct catalog ids
    # from GEE's own ndvi/evi/savi/mndwi/nbr - two different compute paths,
    # never sharing one analysis_id/cache row.
    gee_veg_ids = {"ndvi", "evi", "savi", "mndwi", "nbr"}
    assert gee_veg_ids.isdisjoint(_VNV_BAND_INDEX_IDS)


def test_no_other_catalog_entry_declares_a_config():
    no_config_ids = {
        "hansen_gfc", "dynamic_world", "esa_worldcover",
        "s2_browse", "s1_browse", "landsat_browse",
        "sar", "landtrendr", "canopy_density", "satellite_timelapse",
        "falsecolor_timelapse", "cultivated_area",
    }
    by_id = {e["id"]: e for e in CATALOG}
    for analysis_id in no_config_ids:
        assert "config" not in by_id[analysis_id], analysis_id
