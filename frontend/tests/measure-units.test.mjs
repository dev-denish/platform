/**
 * Unit-conversion + precision check for src/lib/measure.js. Pure functions, no
 * DOM, no server - run directly: `node tests/measure-units.test.mjs`.
 * Deliberately NOT under tests/e2e (Playwright's testDir), which spins up a
 * whole docker stack this doesn't need.
 */
import assert from "node:assert";
import { formatMeasurement, MEASURE_UNITS, DEFAULT_UNIT } from "../src/lib/measure.js";

// Distance: base value is meters
assert.equal(formatMeasurement("distance", 1250.44, "m"), "1,250.4 m");
assert.equal(formatMeasurement("distance", 1250.44, "km"), "1.25 km");
assert.equal(formatMeasurement("distance", 1000, "ft"), "3,281 ft");
assert.equal(formatMeasurement("distance", 1609.344, "mi"), "1 mi");

// Area: base value is hectares
assert.equal(formatMeasurement("area", 0.3, "ha"), "0.3 ha");
assert.equal(formatMeasurement("area", 0.3, "ac"), "0.7413 ac");
assert.equal(formatMeasurement("area", 0.3, "km2"), "0.003 km²");
assert.equal(formatMeasurement("area", 1234.5, "mi2"), "4.766 mi²");

// The reason decimalsFor exists: a small plot in square miles must not read "0.00"
assert.equal(formatMeasurement("area", 0.3, "mi2"), "0.001158 mi²");

// Edge cases
assert.equal(formatMeasurement("area", null, "ha"), null); // not enough points yet
assert.equal(formatMeasurement("inspect", 5, "m"), null); // not a measure mode
assert.equal(formatMeasurement("area", 12, "bogus-stored-unit"), "12 ha"); // falls back to default
assert.equal(formatMeasurement("distance", 0, "m"), "0 m");

// Defaults must be today's behaviour, and every unit needs a dropdown label
for (const mode of ["distance", "area"]) {
  assert.ok(MEASURE_UNITS[mode][DEFAULT_UNIT[mode]]);
  assert.equal(MEASURE_UNITS[mode][DEFAULT_UNIT[mode]].factor, 1);
  for (const u of Object.values(MEASURE_UNITS[mode])) assert.ok(u.label && u.symbol);
}

console.log("measure-units: ok");
