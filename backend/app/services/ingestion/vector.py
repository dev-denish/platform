"""
Vector format parsing (Wave: multi-format layers, Part A).

Unlike raster.py, none of this needs GDAL/rasterio - a vector feature is just
a geometry + a properties dict, and PostGIS itself does the actual geometry
construction/validation (ST_GeomFromGeoJSON, ST_MakePoint) once a feature
reaches VectorFeatureRepository.insert_many. Every parser below therefore
returns the same simple shape: a list of (geometry_as_geojson_dict,
properties_dict) tuples, in EPSG:4326 (WGS84) - never a different CRS,
because:

  - GeoJSON is WGS84 by definition (RFC 7946 mandates it).
  - KML is WGS84 by definition (OGC KML spec, section 5.3).
  - CSV lat/lon columns are assumed WGS84 (the only sane default for a
    "type a latitude and longitude" input - there is no other CRS
    metadata a plain CSV could carry).
  - Shapefile is the one format that can genuinely be in something else -
    see `_assert_shapefile_is_wgs84` below for why that's handled by
    rejecting non-WGS84 bundles outright rather than reprojecting them.

Every parser raises UnprocessableError/ValidationError (never a bare
exception) for anything it can't confidently resolve into real geometry - the
Wave's explicit instruction for CSV ("reject clearly if no valid lat/lon can
be resolved") is applied here to every format, not just CSV.
"""
from __future__ import annotations

import csv
import json
import math
import zipfile
from typing import Any

import shapefile  # pyshp
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from app.core.errors import UnprocessableError, ValidationError

Feature = tuple[dict[str, Any], dict[str, Any]]  # (geometry_geojson, properties)

_KML_NS = "{http://www.opengis.net/kml/2.2}"
_VALID_GEOJSON_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
}


def _validate_geometry(geom: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(geom, dict) or geom.get("type") not in _VALID_GEOJSON_TYPES:
        raise UnprocessableError(f"Not a valid GeoJSON geometry: {geom!r}")
    return geom


def _iter_coord_pairs(geom: dict[str, Any]) -> Any:
    if geom["type"] == "GeometryCollection":
        for g in geom["geometries"]:
            yield from _iter_coord_pairs(g)
        return

    def walk(node: Any) -> Any:
        # A well-formed coordinate tree bottoms out in a [lon, lat(, alt)] list/tuple
        # of numbers. A malformed leaf (e.g. a string where a number belongs, from a
        # hand-edited or corrupt file) is not a list/tuple at all by the time we get
        # here - `node[0]` on a string returns another string of length 1, which
        # recursed into itself forever (RecursionError) instead of failing cleanly.
        if not isinstance(node, list | tuple) or not node:
            raise UnprocessableError(f"Malformed coordinate value: {node!r}")
        if isinstance(node[0], int | float):
            yield node
        else:
            for child in node:
                yield from walk(child)

    yield from walk(geom["coordinates"])


def compute_bounds(features: list[Feature]) -> tuple[float, float, float, float]:
    """(minx, miny, maxx, maxy) across every feature's geometry - the extent
    LayerRepository.insert_non_raster needs, computed in plain Python since
    nothing here has a PostGIS cursor available yet (this runs before the row
    exists)."""
    minx = miny = math.inf
    maxx = maxy = -math.inf
    for geom, _props in features:
        for lon, lat in _iter_coord_pairs(geom):
            minx, maxx = min(minx, lon), max(maxx, lon)
            miny, maxy = min(miny, lat), max(maxy, lat)
    if math.isinf(minx):
        raise UnprocessableError("No coordinates found in the uploaded file.")
    return (minx, miny, maxx, maxy)


# ---------------------------------------------------------------- GeoJSON


def parse_geojson(path: str) -> list[Feature]:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise UnprocessableError(f"Invalid GeoJSON: {e}") from e

    gtype = data.get("type") if isinstance(data, dict) else None
    if gtype == "FeatureCollection":
        raw_features = data.get("features", [])
    elif gtype == "Feature":
        raw_features = [data]
    elif gtype in _VALID_GEOJSON_TYPES:
        raw_features = [{"type": "Feature", "geometry": data, "properties": {}}]
    else:
        raise UnprocessableError("GeoJSON must be a Feature, FeatureCollection, or geometry.")

    features: list[Feature] = []
    for f in raw_features:
        geom = _validate_geometry(f.get("geometry") or {})
        props = f.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        features.append((geom, props))

    if not features:
        raise UnprocessableError("GeoJSON contained no features.")
    return features


# ---------------------------------------------------------------- KML


def _kml_coords_to_list(text: str) -> list[list[float]]:
    """KML coordinate tuples are 'lon,lat[,alt]' separated by whitespace -
    altitude (if present) is dropped, since every geometry column here is
    2D (GEOMETRY(Geometry, 4326), not GEOMETRYZ)."""
    coords = []
    for tuple_str in text.split():
        parts = tuple_str.split(",")
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError as e:
            raise UnprocessableError(f"Invalid KML coordinate: {tuple_str!r}") from e
        coords.append([lon, lat])
    return coords


def _kml_geometry(el: ET.Element) -> dict[str, Any] | None:
    tag = el.tag.removeprefix(_KML_NS)
    if tag == "Point":
        coord_el = el.find(f"{_KML_NS}coordinates")
        coords = _kml_coords_to_list(coord_el.text or "") if coord_el is not None else []
        if not coords:
            return None
        return {"type": "Point", "coordinates": coords[0]}
    if tag == "LineString":
        coord_el = el.find(f"{_KML_NS}coordinates")
        coords = _kml_coords_to_list(coord_el.text or "") if coord_el is not None else []
        if len(coords) < 2:
            return None
        return {"type": "LineString", "coordinates": coords}
    if tag in ("Polygon",):
        rings = []
        outer = el.find(f"{_KML_NS}outerBoundaryIs/{_KML_NS}LinearRing/{_KML_NS}coordinates")
        if outer is None or not (outer.text or "").strip():
            return None
        rings.append(_kml_coords_to_list(outer.text or ""))
        for inner in el.findall(
            f"{_KML_NS}innerBoundaryIs/{_KML_NS}LinearRing/{_KML_NS}coordinates"
        ):
            ring = _kml_coords_to_list(inner.text or "")
            if ring:
                rings.append(ring)
        return {"type": "Polygon", "coordinates": rings}
    if tag in ("MultiGeometry",):
        parts = [g for child in el for g in [_kml_geometry(child)] if g is not None]
        if not parts:
            return None
        return {"type": "GeometryCollection", "geometries": parts}
    return None


def parse_kml(path: str) -> list[Feature]:
    # defusedxml (not stdlib ElementTree) - blocks external entity resolution
    # (XXE) AND internal entity expansion (billion-laughs/quadratic blowup),
    # so a ~1 KB hostile KML can't OOM the worker despite the upload size cap
    # having nothing to do with post-expansion memory use.
    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise UnprocessableError(f"Invalid KML: {e}") from e
    except DefusedXmlException as e:
        raise UnprocessableError(f"KML rejected (unsafe XML construct): {e}") from e

    features: list[Feature] = []
    for placemark in tree.getroot().iter(f"{_KML_NS}Placemark"):
        geom_el = None
        for child in placemark:
            if child.tag.removeprefix(_KML_NS) in (
                "Point", "LineString", "Polygon", "MultiGeometry",
            ):
                geom_el = child
                break
        if geom_el is None:
            continue
        geom = _kml_geometry(geom_el)
        if geom is None:
            continue
        name_el = placemark.find(f"{_KML_NS}name")
        desc_el = placemark.find(f"{_KML_NS}description")
        props: dict[str, Any] = {}
        if name_el is not None and name_el.text:
            props["name"] = name_el.text
        if desc_el is not None and desc_el.text:
            props["description"] = desc_el.text
        for data_el in placemark.findall(f"{_KML_NS}ExtendedData/{_KML_NS}Data"):
            key = data_el.get("name")
            value_el = data_el.find(f"{_KML_NS}value")
            if key and value_el is not None:
                props[key] = value_el.text
        features.append((_validate_geometry(geom), props))

    if not features:
        raise UnprocessableError("KML contained no usable Point/LineString/Polygon placemarks.")
    return features


# ---------------------------------------------------------------- CSV (points)


def csv_header(path: str) -> list[str]:
    """Read just the header row, for the frontend's lat/lon column picker to
    validate against server-side (never trust the client's own CSV parse)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            return next(reader)
        except StopIteration:
            return []


def parse_csv_points(path: str, lat_column: str, lon_column: str) -> list[Feature]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValidationError("CSV file is empty.")
        if lat_column not in reader.fieldnames or lon_column not in reader.fieldnames:
            raise ValidationError(
                f"Columns '{lat_column}'/'{lon_column}' not found in CSV header: "
                f"{reader.fieldnames}."
            )
        features: list[Feature] = []
        for i, row in enumerate(reader, start=2):  # row 1 is the header
            lat_raw, lon_raw = row.get(lat_column), row.get(lon_column)
            try:
                lat, lon = float(lat_raw), float(lon_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError) as e:
                raise UnprocessableError(
                    f"Row {i}: '{lat_column}'/'{lon_column}' is not a valid "
                    f"latitude/longitude ({lat_raw!r}, {lon_raw!r})."
                ) from e
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise UnprocessableError(
                    f"Row {i}: latitude/longitude out of range ({lat}, {lon})."
                )
            props = {k: v for k, v in row.items() if k not in (lat_column, lon_column)}
            features.append(({"type": "Point", "coordinates": [lon, lat]}, props))

    if not features:
        raise UnprocessableError("CSV file contained no data rows.")
    return features


# ---------------------------------------------------------------- Shapefile (zipped)

_WGS84_MARKERS = ("GCS_WGS_1984", "WGS_1984", "WGS84", '"EPSG","4326"')


def _assert_shapefile_is_wgs84(prj_wkt: str | None) -> None:
    """A shapefile's .prj is arbitrary projected/geographic WKT - correctly
    reprojecting arbitrary WKT to EPSG:4326 needs a real CRS-transform
    library (pyproj/GDAL's OSR), which this backend does not otherwise
    depend on (see module docstring - rasterio's GDAL is not exposed as a
    general CRS-transform API here).

    ponytail: rather than add that dependency for what is, for this
    platform's use case (uploaded plot/AOI boundaries), overwhelmingly
    already WGS84, reject anything that doesn't look like WGS84 with a
    clear error instead of silently mis-locating every geometry. Upgrade
    path if a real non-WGS84 shapefile shows up in practice: add pyproj and
    call Transformer.from_crs(prj_wkt, "EPSG:4326") here instead of raising.
    """
    if prj_wkt is None:
        # No .prj at all is the common convention for "assume WGS84" in
        # hand-authored shapefiles - accepted, not rejected.
        return
    if not any(marker in prj_wkt for marker in _WGS84_MARKERS):
        raise UnprocessableError(
            "Shapefile is not in WGS84 (EPSG:4326). Re-project it before uploading; "
            "automatic CRS transformation is not supported for shapefiles."
        )


def _shape_to_geojson(shape: Any) -> dict[str, Any] | None:
    geo = shape.__geo_interface__
    if geo is None or geo.get("type") is None:
        return None
    return geo


def parse_shapefile_zip(path: str) -> list[Feature]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            prj_name = next((n for n in names if n.lower().endswith(".prj")), None)
            prj_wkt = zf.read(prj_name).decode("utf-8", errors="ignore") if prj_name else None
    except zipfile.BadZipFile as e:
        raise UnprocessableError(f"Not a valid .zip shapefile bundle: {e}") from e

    _assert_shapefile_is_wgs84(prj_wkt)

    try:
        reader = shapefile.Reader(path)
    except shapefile.ShapefileException as e:
        raise UnprocessableError(f"Invalid shapefile bundle: {e}") from e

    features: list[Feature] = []
    with reader:
        field_names = [f[0] for f in reader.fields[1:]]  # skip DeletionFlag
        for sr in reader.iterShapeRecords():
            geom = _shape_to_geojson(sr.shape)
            if geom is None or geom.get("type") == "Null":
                continue
            props = dict(zip(field_names, sr.record, strict=False))
            features.append((_validate_geometry(geom), props))

    if not features:
        raise UnprocessableError("Shapefile contained no usable geometries.")
    return features


def demo() -> None:
    """ponytail: smallest runnable check per parser - real files, no fixtures."""
    geojson_bytes = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                    "properties": {"name": "a"},
                }
            ],
        }
    ).encode()
    kml_bytes = b"""<?xml version="1.0"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document>
<Placemark><name>x</name><Point><coordinates>1.0,2.0,0</coordinates></Point></Placemark>
</Document></kml>"""
    csv_bytes = b"lat,lon,name\n2.0,1.0,a\n"

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".geojson") as f:
        f.write(geojson_bytes)
        f.flush()
        feats = parse_geojson(f.name)
        assert len(feats) == 1 and feats[0][0]["type"] == "Point"

    with tempfile.NamedTemporaryFile(suffix=".kml") as f:
        f.write(kml_bytes)
        f.flush()
        feats = parse_kml(f.name)
        assert len(feats) == 1 and feats[0][0]["coordinates"] == [1.0, 2.0]

    with tempfile.NamedTemporaryFile(suffix=".csv") as f:
        f.write(csv_bytes)
        f.flush()
        assert csv_header(f.name) == ["lat", "lon", "name"]
        feats = parse_csv_points(f.name, "lat", "lon")
        assert len(feats) == 1 and feats[0][0]["coordinates"] == [1.0, 2.0]
        try:
            parse_csv_points(f.name, "nope", "lon")
        except ValidationError:
            pass
        else:
            raise AssertionError("missing column should have raised")

    print("vector.demo: all checks passed")


if __name__ == "__main__":
    demo()
