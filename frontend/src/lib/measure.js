import { area as turfArea } from "@turf/area";

/**
 * Real-world distance/area from a list of Leaflet LatLng vertices - the two
 * measure tools' actual math, kept out of ProjectMap.jsx.
 *
 * Distance: Leaflet's own `LatLng.distanceTo` (already installed, no new
 * dependency) - a real haversine great-circle calculation in meters, not
 * naive degree math.
 *
 * Area: Leaflet has no equivalent area utility, so this reaches for
 * `@turf/area` - a real geodesic polygon-area formula (Chamberlain &
 * Duquette) operating directly on WGS84 lon/lat, not a planar shoelace over
 * raw degrees (that was literally the Phase-1 bug this platform exists to
 * avoid). One small scoped package, not the full turf bundle.
 */
export function lineDistanceMeters(latlngs) {
  let total = 0;
  for (let i = 1; i < latlngs.length; i++) {
    total += latlngs[i - 1].distanceTo(latlngs[i]);
  }
  return total;
}

export function polygonAreaHectares(latlngs) {
  if (latlngs.length < 3) return 0;
  const ring = latlngs.map((p) => [p.lng, p.lat]);
  ring.push(ring[0]); // close the ring
  const squareMeters = turfArea({ type: "Polygon", coordinates: [ring] });
  return squareMeters / 10000;
}
