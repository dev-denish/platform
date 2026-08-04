// Run: node src/lib/draw.check.mjs   (no test runner in this frontend)
import assert from "node:assert/strict";
import { toFeatureCollection } from "./draw.js";

const p = [
  { lat: 12.9, lng: 77.6 },
  { lat: 13.0, lng: 77.7 },
  { lat: 13.1, lng: 77.5 },
];
const geom = (mode, pts) => toFeatureCollection(mode, pts, "x").features[0].geometry;

// [lng, lat], not [lat, lng].
assert.deepEqual(geom("point", p).coordinates, [77.6, 12.9]);
assert.deepEqual(geom("line", p.slice(0, 2)).coordinates, [[77.6, 12.9], [77.7, 13.0]]);
// Ring closes back on the first vertex.
const ring = geom("polygon", p).coordinates[0];
assert.equal(ring.length, 4);
assert.deepEqual(ring[0], ring[3]);
assert.equal(toFeatureCollection("point", p, "x").features[0].properties.name, "x");
console.log("draw.js ok");
