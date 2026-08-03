"""Unit tests for app.services.ingestion.vector - edge cases beyond
vector.demo()'s own smallest-runnable-check (one happy path per format). No
DB needed: PostGIS itself only constructs geometry once a feature reaches
VectorFeatureRepository.insert_many (see that module's docstring), so
everything a parser does purely in Python is testable here.
"""
from __future__ import annotations

import json
import zipfile

import pytest
import shapefile

from app.core.errors import UnprocessableError, ValidationError
from app.services.ingestion import vector as V

# --------------------------------------------------------------- compute_bounds


def test_compute_bounds_spans_multiple_features():
    features = [
        ({"type": "Point", "coordinates": [10.0, 20.0]}, {}),
        ({"type": "Point", "coordinates": [-5.0, 2.0]}, {}),
    ]
    assert V.compute_bounds(features) == (-5.0, 2.0, 10.0, 20.0)


def test_compute_bounds_rejects_empty_feature_list():
    with pytest.raises(UnprocessableError):
        V.compute_bounds([])


def test_compute_bounds_handles_geometry_collection():
    geom = {
        "type": "GeometryCollection",
        "geometries": [
            {"type": "Point", "coordinates": [1.0, 1.0]},
            {"type": "Point", "coordinates": [3.0, 3.0]},
        ],
    }
    assert V.compute_bounds([(geom, {})]) == (1.0, 1.0, 3.0, 3.0)


def test_compute_bounds_raises_cleanly_on_a_non_numeric_coordinate_leaf():
    """Regression test for the RecursionError fix (a string coordinate value
    used to recurse into itself indefinitely instead of raising)."""
    features = [({"type": "Point", "coordinates": ["oops", 1.0]}, {})]
    with pytest.raises(UnprocessableError, match="Malformed coordinate"):
        V.compute_bounds(features)


def test_compute_bounds_raises_cleanly_on_an_empty_coordinate_list():
    features = [({"type": "LineString", "coordinates": []}, {})]
    with pytest.raises(UnprocessableError):
        V.compute_bounds(features)


# --------------------------------------------------------------- GeoJSON


def test_parse_geojson_accepts_bare_feature_and_bare_geometry(tmp_path):
    feature_path = tmp_path / "feature.geojson"
    feature_path.write_text(json.dumps({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
        "properties": {"a": 1},
    }))
    feats = V.parse_geojson(str(feature_path))
    assert feats == [({"type": "Point", "coordinates": [1.0, 2.0]}, {"a": 1})]

    geom_path = tmp_path / "geom.geojson"
    geom_path.write_text(json.dumps({"type": "Point", "coordinates": [5.0, 6.0]}))
    feats = V.parse_geojson(str(geom_path))
    assert feats == [({"type": "Point", "coordinates": [5.0, 6.0]}, {})]


def test_parse_geojson_rejects_invalid_json(tmp_path):
    p = tmp_path / "bad.geojson"
    p.write_text("{not valid json")
    with pytest.raises(UnprocessableError):
        V.parse_geojson(str(p))


def test_parse_geojson_rejects_unsupported_geometry_type(tmp_path):
    p = tmp_path / "bad_type.geojson"
    p.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": {"type": "Circle", "coordinates": []}, "properties": {}}],
    }))
    with pytest.raises(UnprocessableError):
        V.parse_geojson(str(p))


def test_parse_geojson_rejects_empty_feature_collection(tmp_path):
    p = tmp_path / "empty.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    with pytest.raises(UnprocessableError):
        V.parse_geojson(str(p))


def test_parse_geojson_rejects_a_top_level_array(tmp_path):
    p = tmp_path / "array.geojson"
    p.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(UnprocessableError):
        V.parse_geojson(str(p))


# --------------------------------------------------------------- KML


def test_parse_kml_rejects_a_document_with_no_placemarks(tmp_path):
    p = tmp_path / "empty.kml"
    p.write_text('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
                 '<Document></Document></kml>')
    with pytest.raises(UnprocessableError):
        V.parse_kml(str(p))


def test_parse_kml_rejects_malformed_xml(tmp_path):
    p = tmp_path / "bad.kml"
    p.write_text("<kml><Document>")
    with pytest.raises(UnprocessableError):
        V.parse_kml(str(p))


def test_parse_kml_blocks_xxe_entity_expansion(tmp_path):
    """defusedxml, not stdlib ElementTree - see vector.py's module docstring.
    A DOCTYPE with an external entity must be rejected outright, not
    resolved."""
    p = tmp_path / "xxe.kml"
    p.write_text(
        '<?xml version="1.0"?>\n'
        '<!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>&xxe;</name></Placemark>"
        "</Document></kml>"
    )
    with pytest.raises(UnprocessableError, match="unsafe XML"):
        V.parse_kml(str(p))


def test_parse_kml_skips_placemarks_with_no_geometry(tmp_path):
    p = tmp_path / "mixed.kml"
    p.write_text(
        '<?xml version="1.0"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        "<Placemark><name>no geometry here</name></Placemark>"
        "<Placemark><Point><coordinates>1.0,2.0,0</coordinates></Point></Placemark>"
        "</Document></kml>"
    )
    feats = V.parse_kml(str(p))
    assert len(feats) == 1
    assert feats[0][0]["coordinates"] == [1.0, 2.0]


# --------------------------------------------------------------- CSV


def test_parse_csv_points_rejects_an_empty_file(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    with pytest.raises(ValidationError):
        V.parse_csv_points(str(p), "lat", "lon")


def test_parse_csv_points_rejects_a_header_only_file(tmp_path):
    p = tmp_path / "header_only.csv"
    p.write_text("lat,lon\n")
    with pytest.raises(UnprocessableError):
        V.parse_csv_points(str(p), "lat", "lon")


def test_parse_csv_points_rejects_non_numeric_lat(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("lat,lon\nnot-a-number,76.0\n")
    with pytest.raises(UnprocessableError, match="Row 2"):
        V.parse_csv_points(str(p), "lat", "lon")


def test_parse_csv_points_rejects_out_of_range_latitude(tmp_path):
    p = tmp_path / "range.csv"
    p.write_text("lat,lon\n95.0,76.0\n")
    with pytest.raises(UnprocessableError, match="out of range"):
        V.parse_csv_points(str(p), "lat", "lon")


def test_csv_header_returns_the_raw_header_row(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text("a,b,c\n1,2,3\n")
    assert V.csv_header(str(p)) == ["a", "b", "c"]


def test_csv_header_on_an_empty_file_returns_empty_list(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("")
    assert V.csv_header(str(p)) == []


# --------------------------------------------------------------- Shapefile


def _write_zip_shapefile(tmp_path, name: str, *, prj: str | None) -> str:
    base = str(tmp_path / name)
    w = shapefile.Writer(base, shapeType=shapefile.POINT)
    w.field("label", "C")
    w.point(1.0, 2.0)
    w.record("pt")
    w.close()
    if prj is not None:
        with open(base + ".prj", "w") as f:
            f.write(prj)
    zpath = str(tmp_path / f"{name}.zip")
    exts = [".shp", ".shx", ".dbf"] + ([".prj"] if prj is not None else [])
    with zipfile.ZipFile(zpath, "w") as zf:
        for ext in exts:
            zf.write(base + ext, arcname=name + ext)
    return zpath


def test_parse_shapefile_zip_accepts_a_bundle_with_no_prj_file(tmp_path):
    """No .prj at all is the common 'assume WGS84' convention - accepted, not
    rejected (see _assert_shapefile_is_wgs84's docstring)."""
    zpath = _write_zip_shapefile(tmp_path, "noprj", prj=None)
    feats = V.parse_shapefile_zip(zpath)
    assert len(feats) == 1
    assert feats[0][0]["type"] == "Point"


def test_parse_shapefile_zip_rejects_a_bundle_not_in_wgs84(tmp_path):
    non_wgs84_prj = (
        'GEOGCS["GCS_North_American_1983",'
        'DATUM["D_North_American_1983",SPHEROID["GRS_1980",6378137,298.257222101]],'
        'PRIMEM["Greenwich",0],UNIT["Degree",0.017453292519943295]]'
    )
    zpath = _write_zip_shapefile(tmp_path, "nad83", prj=non_wgs84_prj)
    with pytest.raises(UnprocessableError, match="WGS84"):
        V.parse_shapefile_zip(zpath)


def test_parse_shapefile_zip_rejects_a_corrupt_zip(tmp_path):
    p = tmp_path / "corrupt.zip"
    p.write_bytes(b"not a zip file")
    with pytest.raises(UnprocessableError):
        V.parse_shapefile_zip(str(p))
