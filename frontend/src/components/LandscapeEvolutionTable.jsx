import { useState } from "react";
import EmptyState from "./EmptyState.jsx";
import { formatNumber, humanizeMetricName } from "../lib/format.js";

/** Same "—" convention formatNumber already uses for null - a real signed
 * number gets an explicit "+" (Intl doesn't add one for positive values). */
function formatSigned(value, digits = 2) {
  if (value == null) return "—";
  const formatted = formatNumber(value, digits);
  return value > 0 ? `+${formatted}` : formatted;
}

/** pct_change is a float, the string "new" (grew from a real 0 ha baseline -
 * mathematically infinite %, never sent as Infinity/NaN), or null (this
 * class wasn't part of the legend at one or both endpoint dates - see
 * backend EvolutionChange). Three distinct, deliberately different cases. */
function formatPctChange(pct) {
  if (pct === "new") return "New";
  if (pct == null) return "—";
  return `${formatSigned(pct, 1)}%`;
}

function changeClass(value) {
  if (value === "new" || (typeof value === "number" && value > 0)) return "evolution-positive";
  if (typeof value === "number" && value < 0) return "evolution-negative";
  return "";
}

/**
 * Phase 3 Wave G: land-class change table, reusing this app's existing
 * .data-table/.symbology-toggle styling (Wave E's tokens) - no new visual
 * language. Defaults to earliest-vs-latest; a 3+-eligible-date project can
 * switch to a consecutive-pairs view. Exactly 2 eligible dates never shows
 * the toggle at all - a single pair has nothing to switch between.
 *
 * `evolution` is GET /projects/{id}/evolution's response as-is - all the
 * eligibility (classified + dated layers only) and None-vs-zero/divide-by-
 * zero handling already happened server-side (see ProjectService.
 * compute_evolution); this component only renders it.
 */
export default function LandscapeEvolutionTable({ evolution }) {
  const [mode, setMode] = useState("first-last");

  if (!evolution || !evolution.applicable) {
    const dateCount = evolution?.dates?.length ?? 0;
    return (
      <EmptyState
        title="Not enough classified dates yet"
        detail={
          dateCount === 0
            ? "Landscape Evolution needs at least two classified (LULC-style) dated layers to compare - this project doesn't have any yet."
            : `Only one classified date available (${evolution.dates[0]}) - upload another classified dataset to see how the landscape has changed over time.`
        }
      />
    );
  }

  const { dates, classes } = evolution;
  const showToggle = dates.length >= 3;
  const consecutivePairs = dates.slice(0, -1).map((from, i) => [from, dates[i + 1]]);

  return (
    <div className="evolution-table-wrap">
      {showToggle ? (
        <div className="symbology-toggle evolution-mode-toggle" role="group">
          <button
            type="button"
            className={`symbology-toggle-btn${mode === "first-last" ? " symbology-toggle-btn-active" : ""}`}
            onClick={() => setMode("first-last")}
          >
            {dates[0]} → {dates[dates.length - 1]}
          </button>
          <button
            type="button"
            className={`symbology-toggle-btn${mode === "consecutive" ? " symbology-toggle-btn-active" : ""}`}
            onClick={() => setMode("consecutive")}
          >
            Consecutive dates
          </button>
        </div>
      ) : null}

      <table className="data-table evolution-table">
        <thead>
          <tr>
            <th>Land class</th>
            {dates.map((d) => (
              <th key={d} className="mono-cell">
                {d} (ha)
              </th>
            ))}
            {mode === "first-last" || !showToggle ? (
              <>
                <th>Net change</th>
                <th>% change</th>
              </>
            ) : (
              consecutivePairs.map(([from, to]) => (
                <th key={`${from}-${to}`} colSpan={2}>
                  {from} → {to}
                </th>
              ))
            )}
          </tr>
        </thead>
        <tbody>
          {classes.map((row) => (
            <tr key={row.metric_name}>
              <td>{humanizeMetricName(row.metric_name)}</td>
              {dates.map((d) => (
                <td key={d} className="mono-cell">
                  {formatNumber(row.area_by_date_ha[d])}
                </td>
              ))}
              {mode === "first-last" || !showToggle ? (
                <>
                  <td className={`mono-cell ${changeClass(row.first_vs_last.net_change_ha)}`}>
                    {formatSigned(row.first_vs_last.net_change_ha)}
                  </td>
                  <td className={`mono-cell ${changeClass(row.first_vs_last.pct_change)}`}>
                    {formatPctChange(row.first_vs_last.pct_change)}
                  </td>
                </>
              ) : (
                row.consecutive.map((change, i) => (
                  <td key={i} className="mono-cell evolution-consecutive-pair">
                    <span className={changeClass(change.net_change_ha)}>{formatSigned(change.net_change_ha)}</span>
                    {" · "}
                    <span className={changeClass(change.pct_change)}>{formatPctChange(change.pct_change)}</span>
                  </td>
                ))
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
