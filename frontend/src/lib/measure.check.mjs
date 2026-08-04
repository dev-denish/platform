// Run: node src/lib/measure.check.mjs   (no test runner in this frontend)
import assert from "node:assert/strict";
import { parseLatLon } from "./measure.js";

assert.deepEqual(parseLatLon("12.345, 67.89"), { lat: 12.345, lon: 67.89 });
assert.deepEqual(parseLatLon("  -12.5   77  "), { lat: -12.5, lon: 77 });
assert.deepEqual(parseLatLon("12.9716,77.5946"), { lat: 12.9716, lon: 77.5946 });
// Wrong count, non-numbers, out of range -> no jump.
assert.equal(parseLatLon("12.345"), null);
assert.equal(parseLatLon("12, 34, 56"), null);
assert.equal(parseLatLon(""), null);
assert.equal(parseLatLon("abc, 12"), null);
assert.equal(parseLatLon("12, abc"), null);
assert.equal(parseLatLon("91, 10"), null);
assert.equal(parseLatLon("-91, 10"), null);
assert.equal(parseLatLon("10, 181"), null);
// Boundaries are valid, and 0,0 must not be mistaken for "empty".
assert.deepEqual(parseLatLon("90, 180"), { lat: 90, lon: 180 });
assert.deepEqual(parseLatLon("0, 0"), { lat: 0, lon: 0 });
console.log("measure.js parseLatLon ok");
