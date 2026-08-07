import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import { useCollapse } from "../lib/useCollapse.js";
import { canUpload } from "../lib/roles.js";
import { formatDate, formatNumber } from "../lib/format.js";
import EmptyState from "./EmptyState.jsx";
import ErrorBanner from "./ErrorBanner.jsx";
import ProjectMap from "./ProjectMap.jsx";

/**
 * The Maps tab's "Analysis" view (Wave: GEE analysis registry) - three
 * regions: the registry list on the left (GET /projects/{id}/analyses, all 13
 * catalog entries, grouped by category), the existing project map in the
 * middle with the selected analysis's GEE tiles laid over it, and that
 * analysis's cached stats/legend on the right.
 *
 * Honesty rules this UI exists to keep (see app/domain/analysis_catalog.py):
 * an "in-development" entry is LISTED but visibly de-emphasized and renders
 * an empty state - never a chart, never a number. An "available" entry that
 * has never been computed for this project renders an empty state plus a
 * Compute button, not a zero-filled chart. `stats.note` (dataset caveats) is
 * always rendered when present, never hidden behind a tooltip only.
 *
 * Stat shapes differ per analysis and are rendered by three small branches
 * (Hansen's totals, a single class breakdown, a per-year class breakdown) -
 * deliberately not a generic renderer for five real shapes.
 *
 * Wave: vegetation indices adds a 6th shape (`stats.series`, a per-year
 * value) plus the timeline scrubber - see IndexTrendChart below - and an
 * async job-queue path (POST refresh can return either the result directly
 * or a {job_id} to poll, per the catalog entry's execution mode; see
 * runAnalysis).
 */

// Same polling pattern as UploadPage.jsx's job-status wait - job-kind
// agnostic on the backend, so reused verbatim here rather than re-derived.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

/** Rows are keyed on the analysis id; a whole result object is only ever
 * fetched for the one currently selected. */
function AnalysisGroup({ category, entries, selectedId, onSelect }) {
  const [open, toggle] = useCollapse(`collapse:analysis:group:${category}`, true);
  return (
    <div className="layer-group">
      <button type="button" className="layer-group-header" aria-expanded={open} onClick={toggle}>
        <span className={`layer-group-chevron${open ? " layer-group-chevron-open" : ""}`} aria-hidden="true">
          <ChevronDown size={16} strokeWidth={2} className="icon" />
        </span>
        {category}
      </button>
      <div className="collapsible-body" data-open={open} inert={open ? undefined : ""}>
        <div className="collapsible-body-inner">
          {entries.map((e) => {
            const inDev = e.status !== "available";
            return (
              <button
                type="button"
                key={e.id}
                className={`analysis-row${e.id === selectedId ? " analysis-row-active" : ""}${
                  inDev ? " analysis-row-muted" : ""
                }`}
                aria-pressed={e.id === selectedId}
                onClick={() => onSelect(e.id)}
              >
                <span className="analysis-row-top">
                  <span className="analysis-row-name">{e.name}</span>
                  <span className="role-badge">{inDev ? "In development" : "Available"}</span>
                </span>
                {!inDev ? (
                  <span className="analysis-row-meta">
                    {e.computed_at ? `Computed ${formatDate(e.computed_at)}` : "Not computed yet"}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** One labelled bar + hectare value. `max` is the largest value in the same
 * list, so bars are comparable within one breakdown (never across analyses -
 * the classification schemes differ, see the service's docstring). */
function StatBar({ label, value, max, color }) {
  return (
    <li className="analysis-bar-row">
      <span className="analysis-bar-label">
        {color ? <span className="legend-swatch" style={{ background: color }} aria-hidden="true" /> : null}
        {label}
      </span>
      <span className="mono-cell">{formatNumber(value)} ha</span>
      <span className="analysis-bar-track">
        <span
          className="analysis-bar-fill"
          style={{ width: max > 0 ? `${(value / max) * 100}%` : 0, background: color ?? "var(--accent)" }}
        />
      </span>
    </li>
  );
}

function ClassBreakdown({ classAreaHa, legend }) {
  const colorByName = useMemo(
    () => Object.fromEntries((legend ?? []).map((l) => [l.name, l.color])),
    [legend]
  );
  const rows = Object.entries(classAreaHa).sort((a, b) => b[1] - a[1]);
  const max = rows.length > 0 ? rows[0][1] : 0;
  return (
    <ul className="analysis-bars">
      {rows.map(([name, value]) => (
        <StatBar key={name} label={name} value={value} max={max} color={colorByName[name]} />
      ))}
    </ul>
  );
}

/** io_lulc / modis_lulc: several years of class breakdowns. Latest year by
 * default, the rest via this plain <select> - no timeline scrubber, nothing
 * here needs one yet. */
function YearlyClassBreakdown({ byYear, legend }) {
  const years = Object.keys(byYear).sort();
  const [year, setYear] = useState(years[years.length - 1]);
  const active = byYear[year] ? year : years[years.length - 1];
  return (
    <>
      <label className="analysis-year-select">
        <span>Year</span>
        <select className="map-toolbar-select" value={active} onChange={(e) => setYear(e.target.value)}>
          {[...years].reverse().map((y) => (
            <option key={y} value={y}>
              {y}
            </option>
          ))}
        </select>
      </label>
      <ClassBreakdown classAreaHa={byYear[active]} legend={legend} />
    </>
  );
}

function HansenStats({ stats }) {
  const lossByYear = stats.loss_area_ha_by_year ?? {};
  const years = Object.keys(lossByYear).sort();
  const totalLoss = Object.values(lossByYear).reduce((a, b) => a + b, 0);
  return (
    <>
      {/* The canopy-cover threshold is what makes "forest area" mean anything
       * at all (it's the platform's own forest-definition setting - see
       * ForestDefinitionPage) - shown next to the number, never as a bare
       * figure a reviewer has to go look up. */}
      <div className="analysis-callout">
        <span className="stat-label">
          Baseline forest area (at ≥{formatNumber(stats.canopy_cover_threshold_pct, 0)}% canopy cover)
        </span>
        <span className="analysis-callout-value">{formatNumber(stats.baseline_forest_area_ha)} ha</span>
      </div>
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-label">Tree-cover gain (2000-2012)</span>
          <span className="stat-value">
            {formatNumber(stats.gain_area_ha_2000_2012)} <span className="stat-unit">ha</span>
          </span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Total loss (within baseline forest)</span>
          <span className="stat-value">
            {formatNumber(totalLoss)} <span className="stat-unit">ha</span>
          </span>
        </div>
      </div>
      {years.length === 0 ? (
        <p className="analysis-note">No tree-cover loss detected inside this boundary.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Year</th>
              <th>Loss (ha)</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => (
              <tr key={y}>
                <td className="mono-cell">{y}</td>
                <td className="mono-cell">{formatNumber(lossByYear[y])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}

/** NDVI/EVI/SAVI/MNDWI/NBR: one value per year, 2017-present. The timeline
 * scrubber (a plain range input - no dedicated scrubber component existed
 * anywhere in this app before) drives which year's point is highlighted
 * here, using the already-fetched series - no extra backend call per drag.
 * The map overlay is always the LATEST year regardless of scrubber position
 * (the backend only generates one tile, to stay inside the timing budget
 * this whole batch was measured against - see gee_analysis_service.py) -
 * the caption below says so rather than implying otherwise. */
function IndexTrendChart({ series }) {
  const years = useMemo(() => Object.keys(series).sort(), [series]);
  const [selectedYear, setSelectedYear] = useState(years[years.length - 1]);
  const data = useMemo(
    () => years.map((y) => ({ year: y, value: series[y] })),
    [years, series]
  );
  const activeValue = series[selectedYear];

  return (
    <>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#E5ECE8" />
          <XAxis dataKey="year" tick={{ fontSize: 11 }} axisLine={{ stroke: "#E5ECE8" }} tickLine={false} />
          <YAxis
            domain={[-1, 1]}
            tick={{ fontSize: 11 }}
            axisLine={{ stroke: "#E5ECE8" }}
            tickLine={false}
            width={32}
          />
          <Tooltip formatter={(v) => (v == null ? "no data" : v.toFixed(4))} />
          <Line
            type="monotone"
            dataKey="value"
            stroke="var(--accent)"
            strokeWidth={2}
            dot={{ r: 3 }}
            connectNulls
          />
          {activeValue != null ? (
            <ReferenceDot x={selectedYear} y={activeValue} r={6} fill="var(--accent)" stroke="#fff" strokeWidth={2} />
          ) : null}
        </LineChart>
      </ResponsiveContainer>
      <label className="analysis-year-select">
        <span>Year</span>
        <input
          type="range"
          min={0}
          max={years.length - 1}
          step={1}
          value={years.indexOf(selectedYear)}
          onChange={(e) => setSelectedYear(years[Number(e.target.value)])}
          aria-label="Select year"
        />
        <span className="mono-cell">
          {selectedYear}: {activeValue != null ? activeValue.toFixed(3) : "no data"}
        </span>
      </label>
      <p className="analysis-note">Map shows the latest year ({years[years.length - 1]}).</p>
    </>
  );
}

function AnalysisStats({ result }) {
  const { stats, legend } = result;
  return (
    <>
      {stats.canopy_cover_threshold_pct != null ? <HansenStats stats={stats} /> : null}
      {stats.class_area_ha ? <ClassBreakdown classAreaHa={stats.class_area_ha} legend={legend} /> : null}
      {stats.class_area_ha_by_year ? (
        <YearlyClassBreakdown byYear={stats.class_area_ha_by_year} legend={legend} />
      ) : null}
      {stats.series ? <IndexTrendChart series={stats.series} /> : null}
      {stats.note ? <p className="analysis-note">{stats.note}</p> : null}
    </>
  );
}

export default function AnalysisPanel({ projectId, layers, onRefreshLayers, onLegendChanged }) {
  const { user } = useAuth();
  const [entries, setEntries] = useState(null);
  const [listError, setListError] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [result, setResult] = useState(null);
  const [resultError, setResultError] = useState(null);
  const [running, setRunning] = useState(false);
  const [runStatus, setRunStatus] = useState(null); // async-job status while polling, else null

  // Race guard for the async (job-polling) path below: if the user switches
  // to a different analysis while one is still polling (can take a minute+
  // for a fresh multi-year composite), that stale poll's eventual result
  // must not overwrite whatever's now selected.
  const selectedIdRef = useRef(selectedId);
  useEffect(() => {
    selectedIdRef.current = selectedId;
  }, [selectedId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch(`/projects/${projectId}/analyses`);
        if (!cancelled) setEntries(data.analyses);
      } catch (err) {
        if (!cancelled) setListError(err.message ?? "Could not load the analysis list.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const selected = entries?.find((e) => e.id === selectedId) ?? null;

  // Only ever fetched when the list already says there IS a cached result -
  // `computed_at` is authoritative, so a plain 404 (never computed) never
  // has to be told apart from a real error here.
  useEffect(() => {
    setResult(null);
    setResultError(null);
    if (!selected || selected.status !== "available" || !selected.computed_at) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiFetch(`/projects/${projectId}/analyses/${selected.id}`);
        if (!cancelled) setResult(data);
      } catch (err) {
        if (!cancelled) setResultError(err.message ?? "Could not load this result.");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, selected]);

  const groups = useMemo(() => {
    const out = [];
    for (const e of entries ?? []) {
      const group = out.find((g) => g.category === e.category);
      if (group) group.entries.push(e);
      else out.push({ category: e.category, entries: [e] });
    }
    return out;
  }, [entries]);

  /** Real GEE compute. Two shapes come back from POST refresh, depending on
   * the catalog entry's execution mode (app/domain/analysis_catalog.py):
   * "sync" analyses (Hansen ~7s, Dynamic World ~13s, etc.) return the result
   * directly, same as before. "async" ones (NDVI et al. - a fresh multi-year
   * composite measured 5-101s end-to-end, not "a few seconds") return
   * {job_id, status_url} instead; this polls GET /jobs/{id} until terminal
   * (same pattern as UploadPage.jsx's dataset-upload wait), then re-fetches
   * the now-cached real result. The 422s this can return ("no Boundary layer
   * yet") are end-user-readable and surfaced verbatim either way. */
  async function runAnalysis() {
    const analysisId = selected.id;
    setRunning(true);
    setResultError(null);
    setRunStatus(null);
    try {
      const data = await apiFetch(`/projects/${projectId}/analyses/${analysisId}/refresh`, { method: "POST" });
      let final = data;
      if (data.job_id) {
        const deadline = Date.now() + POLL_TIMEOUT_MS;
        let job = data;
        while (!TERMINAL_STATUSES.includes(job.status)) {
          if (selectedIdRef.current !== analysisId) return; // switched away mid-poll
          if (Date.now() > deadline) {
            throw new Error("This is taking longer than expected. It may still finish - check back shortly.");
          }
          setRunStatus(job.status ?? "queued");
          await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
          job = await apiFetch(`/jobs/${data.job_id}`);
        }
        if (job.status !== "succeeded") {
          throw new Error(job.error?.message ?? "This analysis failed to compute.");
        }
        if (selectedIdRef.current !== analysisId) return;
        final = await apiFetch(`/projects/${projectId}/analyses/${analysisId}`);
      }
      if (selectedIdRef.current !== analysisId) return;
      setResult(final);
      setEntries((prev) =>
        prev.map((e) => (e.id === analysisId ? { ...e, computed_at: final.computed_at } : e))
      );
    } catch (err) {
      if (selectedIdRef.current === analysisId) setResultError(err.message ?? "Could not run this analysis.");
    } finally {
      if (selectedIdRef.current === analysisId) {
        setRunning(false);
        setRunStatus(null);
      }
    }
  }

  const canRun = user && canUpload(user.role);

  function renderResults() {
    if (!selected) {
      return <EmptyState title="Select an analysis" detail="Pick one from the list to see its results here." />;
    }
    if (selected.status !== "available") {
      return (
        <EmptyState
          title="This analysis isn't built yet"
          detail={`${selected.description} It's listed so you can see it's planned - nothing has been computed.`}
        />
      );
    }
    return (
      <>
        <p className="analysis-description">{selected.description}</p>
        {resultError ? <p className="field-hint field-hint-error">{resultError}</p> : null}
        {result ? (
          <AnalysisStats result={result} />
        ) : selected.computed_at && !resultError ? (
          <p className="analysis-note">Loading result…</p>
        ) : (
          <EmptyState
            title="Not computed yet"
            detail={
              canRun
                ? "Run it against this project's boundary - takes a few seconds."
                : "Ask a GIS Associate or an administrator to run it."
            }
          />
        )}
        {canRun ? (
          <button type="button" className="primary-button" disabled={running} onClick={runAnalysis}>
            {running ? "Computing…" : result ? "Refresh" : "Run analysis"}
          </button>
        ) : null}
      </>
    );
  }

  return (
    <>
      <ErrorBanner message={listError} />
      <div className="analysis-layout">
        <aside className="analysis-column">
          <div className="analysis-column-toolbar-spacer" aria-hidden="true" />
          <div className="analysis-column-title">Analyses</div>
          <div className="analysis-column-body">
            {groups.map((g) => (
              <AnalysisGroup
                key={g.category}
                category={g.category}
                entries={g.entries}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            ))}
          </div>
        </aside>

        <ProjectMap
          layers={layers}
          onRefreshLayers={onRefreshLayers}
          onLegendChanged={onLegendChanged}
          projectId={projectId}
          overlayTileUrl={result?.tile_url_template ?? null}
        />

        <aside className="analysis-column">
          <div className="analysis-column-toolbar-spacer" aria-hidden="true" />
          <div className="analysis-column-title">
            {selected ? selected.name : "Results"}
            {result ? <span className="analysis-column-meta">Computed {formatDate(result.computed_at)}</span> : null}
          </div>
          <div className="analysis-column-body analysis-results-body">{renderResults()}</div>
        </aside>
      </div>
    </>
  );
}
