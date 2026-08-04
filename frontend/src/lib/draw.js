/**
 * Turns the draw tool's collected Leaflet vertices into the GeoJSON that both
 * "Save to project" (the adhoc-layer upload pipeline) and "Download shapefile"
 * (shp-write) consume. Leaflet LatLng is {lat, lng}; GeoJSON coordinates are
 * [lng, lat] - the one thing in here that's easy to get backwards, hence
 * draw.check.mjs.
 */
export function toFeatureCollection(mode, points, name) {
  const coords = points.map((p) => [p.lng, p.lat]);
  const geometry =
    mode === "point"
      ? { type: "Point", coordinates: coords[0] }
      : mode === "line"
        ? { type: "LineString", coordinates: coords }
        : { type: "Polygon", coordinates: [[...coords, coords[0]]] }; // rings must close
  return {
    type: "FeatureCollection",
    features: [{ type: "Feature", geometry, properties: { name } }],
  };
}
