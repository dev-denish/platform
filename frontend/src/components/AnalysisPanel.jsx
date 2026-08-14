import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  Bar,
  BarChart,
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
 * regions: the registry list on the left (GET /projects/{id}/analyses, all 19
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
 *
 * Wave: enriched index results adds `stats.summary` (a plain-language
 * synthesis, rendered as a lead callout above everything else - see
 * IndexSummaryCallout) and `stats.distribution` (per-year mean/std
 * dev/min/max plus a pixel-value histogram - see IndexDistribution). Both
 * are additive to the existing `stats.series` trend chart, not a
 * replacement for it - the raw per-year numbers stay exactly where they
 * were.
 *
 * Wave: compute-source toggle. A `computeSource` selector ("gee" | "vnv")
 * sits above the registry list and regroups it, entirely client-side - it
 * never changes what's fetched from `GET /projects/{id}/analyses` or what
 * `runAnalysis` sends (both compute sources' entries come back from that
 * one call, distinguished by the backend's own `compute_source` field).
 * "gee" shows the live catalog entries minus any `compute_source ===
 * "vnv_pipeline"` one, regrouped into three buckets by execution mode
 * instead of the backend's raw `category`. "vnv" shows the real, runnable
 * `vnv_ndfi` catalog entry (VNV Pipeline going live, see NdfiExperimentalNotice
 * below for its always-on experimental disclosure) plus two still-hardcoded,
 * still-never-runnable preview stubs - see VNV_PREVIEW_ENTRIES and the
 * Run-button gating in renderResults below.
 */

// Same polling pattern as UploadPage.jsx's job-status wait - job-kind
// agnostic on the backend, so reused verbatim here rather than re-derived.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

// Wave: analysis config and methodology. Wire values -> human labels for the
// rejection hint only - the option `<select>`s render these same ids as
// their own `<option>` text below, so a rejected combo's message uses the
// exact words the user just picked from, not the raw wire id.
const SOURCE_LABELS = { sentinel2: "Sentinel-2", landsat8: "Landsat 8", landsat9: "Landsat 9" };
const MASKING_LABELS = {
  cloud_score_plus: "Cloud Score+", qa_pixel: "QA_PIXEL", none: "No cloud masking",
};

/** Seeds `configParams` from a catalog entry's `config` block (backend-owned
 * defaults/domain, see app/domain/analysis_catalog.py) - called once per
 * selection change, same "reset on selected change" convention `browseYear`
 * already follows just below. `{}` for an entry with no `config` at all
 * (the 6 unconfigurable ids) - every reader below already guards on
 * `selected.config` being present before looking at this. */
function defaultConfigParams(config) {
  if (!config) return {};
  return {
    yearMode: config.year_mode_default ?? "single",
    year: config.year_max,
    yearStart: config.year_min,
    yearEnd: config.year_max,
    seasonStart: config.season_start_default,
    seasonEnd: config.season_end_default,
    imagerySource: config.imagery_sources?.[0],
    cloudMasking: config.cloud_masking_methods?.[0],
  };
}

/** The inverse of the backend's `_analysis_config_query_params` dependency
 * (app/api/v1/analyses.py) - only emits a key an entry's own `config`
 * actually declares (season/source/masking are absent for io_lulc/
 * modis_lulc), same "don't send what doesn't apply" rule the pre-existing
 * browse-year query building already follows. */
function buildConfigQuery(config, configParams) {
  if (!config) return "";
  const params = new URLSearchParams();
  if (configParams.yearMode === "range") {
    params.set("year_mode", "range");
    if (configParams.yearStart != null) params.set("year_start", configParams.yearStart);
    if (configParams.yearEnd != null) params.set("year_end", configParams.yearEnd);
  } else if (configParams.year != null) {
    params.set("year", configParams.year);
  }
  if (config.season_editable) {
    if (configParams.seasonStart) params.set("season_start", configParams.seasonStart);
    if (configParams.seasonEnd) params.set("season_end", configParams.seasonEnd);
  }
  if (config.imagery_sources) {
    if (configParams.imagerySource) params.set("imagery_source", configParams.imagerySource);
    if (configParams.cloudMasking) params.set("cloud_masking", configParams.cloudMasking);
  }
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** The button's in-flight label. `runStatus` is only set on the async
 * (job-polling) path, and only once the first poll response lands - a sync
 * analysis, or the moment between click and that first poll, has nothing to
 * report yet, so both default to the same "Computing…" as before. */
function runningLabel(runStatus) {
  return runStatus === "queued" ? "Queued…" : "Computing…";
}

/** Wave: compute-source toggle. Regroups a live, `status: "available"`
 * catalog entry for display only when `computeSource === "gee"` - the real
 * `category` field (and everything else on the entry) is untouched, so
 * config/methodology/run wiring downstream never has to know this
 * regrouping happened. Raw Imagery is checked first because its 3 entries
 * are also "sync", same as the 5 land-cover ones - execution mode alone
 * can't tell them apart. */
function geeGroupFor(entry) {
  if (entry.category === "Raw Imagery") return "Raw Imagery";
  return entry.execution === "async" ? "Compute — Sentinel-2 Composite" : "Instant Query";
}

/** Wave: VNV pipeline NDFI goes live. `vnv_ndfi` is now a real, runnable
 * catalog entry (`compute_source: "vnv_pipeline"`, `status: "available"`)
 * fetched from `GET /projects/{id}/analyses` just like every GEE entry - see
 * `visibleEntries` below, which merges it in ahead of these two. Stocking
 * Index and Control Plot Matching are NOT in the backend catalog and have no
 * implementation at all - this frontend-only preview list is now only
 * these two, never sent to any API call, rendering through the exact same
 * `.analysis-row-muted` / "isn't built yet" empty state every other
 * in-development catalog entry already gets. Ids are `vnv_`-prefixed so they
 * can never collide with a real catalog id. */
const VNV_PREVIEW_ENTRIES = [
  {
    id: "vnv_stocking_index",
    name: "Stocking Index",
    category: "VM0047 Compute — ForesToolboxRS",
    status: "in-development",
    description:
      "Estimates per-plot stem density and canopy stocking from ForesToolboxRS outputs, flagging under-stocked areas ahead of VM0047 field verification.",
  },
  {
    id: "vnv_control_plot_matching",
    name: "Control Plot Matching",
    category: "VM0047 Compute — ForesToolboxRS",
    status: "in-development",
    description:
      "Pairs project plots with statistically similar control-area plots outside the project boundary, supporting VM0047's additionality and leakage comparisons.",
  },
];

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

/** NDVI/EVI/SAVI/MNDWI/NBR: a one- or two-sentence plain-language synthesis
 * of the same numbers the rest of this column renders as figures and
 * charts ("2025: NDVI averages 0.62... risen from 0.44 to 0.62"). Additive,
 * not a replacement - it sits above the raw numbers (stat grid, trend
 * chart, histogram), it never substitutes for them, per the field-team
 * readability goal without hiding the underlying data a VVB would need to
 * trace. Reuses the callout container HansenStats already established for
 * a single lead metric; `.analysis-callout-text` only resets that
 * pattern's mono/bold numeric styling back to prose, since this callout's
 * content is a paragraph, not a number. */
function IndexSummaryCallout({ summary }) {
  return (
    <div className="analysis-callout">
      <span className="stat-label">Summary</span>
      <p className="analysis-callout-value analysis-callout-text">{summary}</p>
    </div>
  );
}

/** One year's pixel-value histogram for a vegetation index. Bin edges come
 * from the backend fixed every year (21 edges / 20 bins spanning the
 * index's valid range), so the x-axis is already physically bounded - nothing
 * to clamp here, unlike a freely auto-scaled axis. */
function IndexHistogramChart({ histogram }) {
  const { bin_edges: edges, counts } = histogram;
  const data = useMemo(
    () =>
      counts.map((count, i) => ({
        label: edges[i].toFixed(2),
        rangeLabel: `${edges[i].toFixed(2)} to ${edges[i + 1].toFixed(2)}`,
        count,
      })),
    [edges, counts]
  );
  // Thin out x-axis ticks so 20 bins don't collide into an unreadable
  // smear of labels - shows roughly 6-7 evenly spaced edges.
  const tickInterval = Math.max(0, Math.ceil(data.length / 7) - 1);
  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5ECE8" />
        <XAxis
          dataKey="label"
          interval={tickInterval}
          tick={{ fontSize: 10 }}
          axisLine={{ stroke: "#E5ECE8" }}
          tickLine={false}
        />
        <YAxis
          domain={[0, "dataMax"]}
          allowDecimals={false}
          tick={{ fontSize: 11 }}
          axisLine={{ stroke: "#E5ECE8" }}
          tickLine={false}
          width={44}
        />
        <Tooltip
          formatter={(value) => [`${formatNumber(value, 0)} px`, "Pixels"]}
          labelFormatter={(_, payload) => payload?.[0]?.payload?.rangeLabel ?? ""}
        />
        <Bar dataKey="count" fill="var(--accent)" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** NDVI/EVI/SAVI/MNDWI/NBR: per-year pixel-value distribution (mean/std
 * dev/min/max + the histogram above). Defaults to the most recent year
 * that HAS distribution data, deliberately independent of
 * IndexTrendChart's own timeline scrubber just above it - that scrubber
 * drives the single per-year value in the trend line, and wiring a second
 * chart to the same piece of state would only be worth the complexity once
 * there's an actual reason to inspect an older year's distribution; until
 * then "latest year" is the one reviewers will want by default and keeps
 * this section stateless.
 *
 * No unit suffix on the four stat values, matching IndexTrendChart's own
 * convention just above (also unitless index values, same file) - these
 * are dimensionless vegetation indices, not a physical quantity like ha or
 * tCO2e. */
function IndexDistribution({ distribution }) {
  const years = useMemo(() => Object.keys(distribution).sort(), [distribution]);
  const year = years[years.length - 1];
  const yearStats = distribution[year];
  if (!yearStats) return null;
  return (
    <>
      <div className="stat-grid">
        <div className="stat-card">
          <span className="stat-label">Mean ({year})</span>
          <span className="stat-value">{formatNumber(yearStats.mean, 3)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Variability ({year})</span>
          <span className="stat-value">{formatNumber(yearStats.std_dev, 3)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Min ({year})</span>
          <span className="stat-value">{formatNumber(yearStats.min, 3)}</span>
        </div>
        <div className="stat-card">
          <span className="stat-label">Max ({year})</span>
          <span className="stat-value">{formatNumber(yearStats.max, 3)}</span>
        </div>
      </div>
      {yearStats.histogram ? (
        <>
          <IndexHistogramChart histogram={yearStats.histogram} />
          <p className="analysis-note">Pixel-value distribution across the boundary, {year}.</p>
        </>
      ) : null}
      {/* Parked feature - comparing this boundary's distribution against a
       * reference (unmanaged / undisturbed) region. Shown so the intent is
       * visible to reviewers, but visually de-emphasized rather than
       * looking like a broken chart. */}
      <div className="analysis-placeholder-row">
        <span className="stat-label">Reference-region comparison</span>
        <span className="stat-value-muted">Not yet available</span>
      </div>
    </>
  );
}

/** Wave: analysis config and methodology. A real, per-analysis methodology
 * breakdown (`stats.methodology`, always a flat object of already-real,
 * already-user-facing values - see app/services/gee_analysis_service.py's
 * own methodology-builder functions) - closed by default (this is
 * supplementary detail, not the primary result), reusing the exact
 * `.layer-group-header`/`.collapsible-body` collapsible primitive
 * `AnalysisGroup` above and `LayersPanel`/`ProjectMembers` elsewhere already
 * use, and `.data-table` (HansenStats' own loss-by-year table) for the
 * label/value rows - no new component category for this. One generic
 * renderer for every analysis family, not one per shape, since the backend
 * contract guarantees every value here is already a plain string/number/
 * list a non-engineer can read. */
function AnalysisMethodology({ methodology, analysisId }) {
  const [open, toggle] = useCollapse(`collapse:analysis:methodology:${analysisId}`, false);
  const rows = Object.entries(methodology).map(([key, value]) => [
    key
      .split("_")
      .map((word) => word[0].toUpperCase() + word.slice(1))
      .join(" "),
    Array.isArray(value) ? value.join(", ") : String(value),
  ]);
  return (
    <div className="layer-group">
      <button type="button" className="layer-group-header" aria-expanded={open} onClick={toggle}>
        <span className={`layer-group-chevron${open ? " layer-group-chevron-open" : ""}`} aria-hidden="true">
          <ChevronDown size={16} strokeWidth={2} className="icon" />
        </span>
        Methodology
      </button>
      <div className="collapsible-body" data-open={open} inert={open ? undefined : ""}>
        <div className="collapsible-body-inner">
          <table className="data-table">
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <td>{label}</td>
                  <td className="mono-cell">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/** Wave: partial coverage. Always rendered when `coverage_pct` is present
 * (even at 100%) - matches this file's own "stats.note is always shown,
 * never hidden behind a tooltip" rule below. `partial` (< 90%) switches to
 * the amber warning treatment `.sample-data-banner` already established for
 * "don't skim past this" callouts - 90% is a deliberate buffer above 100,
 * not a hard boundary: edge-pixel rasterization alone can cost a
 * near-fully-covered AOI a couple of points, so the threshold has to sit
 * below that noise floor or every dataset would show a spurious warning. */
function CoverageBanner({ pct }) {
  if (pct == null) return null;
  const partial = pct < 90;
  return (
    <div className={`coverage-banner${partial ? " coverage-banner-warning" : ""}`}>
      {partial ? "Partial coverage" : "Coverage"}: {pct.toFixed(1)}% of the project boundary
      {partial ? " — some area has no valid pixels (cloud/scene-edge gaps or missing tiles)." : "."}
    </div>
  );
}

/** Wave: VNV pipeline NDFI goes live. Rendered every time a `vnv_ndfi`
 * result is shown - a persistent disclosure, not a one-time/dismissible
 * warning, same "always rendered, never hidden behind a tooltip" rule
 * `stats.note` already follows just below. Reuses `.sample-data-banner`
 * (the same amber convention the compute-source card's own dev note above
 * uses) rather than inventing new styling. The exact copy here is
 * load-bearing - it must match the compute-source card's own disclosure
 * verbatim. */
function NdfiExperimentalNotice() {
  return (
    <div className="sample-data-banner">
      Experimental — this method has not yet passed domain review and may be unreliable, especially on densely
      forested areas. Not for compliance reporting.
    </div>
  );
}

/** Wave: VNV pipeline NDFI goes live. `stats.masked_fraction` /
 * `valid_pixel_count` / `total_pixel_count` - the fraction of the boundary
 * NDFI's spectral unmixing could not classify (cloud/shadow/no-data),
 * surfaced with the same `.stat-grid` > `.stat-card` idiom `IndexDistribution`
 * above already uses, not buried as a raw fraction. Phase 2's own prototype
 * measured 97.66% masking on forest-heavy scenes, so a mostly-masked result
 * has to be obvious at a glance: `.stat-card-warning` switches the masked-
 * fraction card to the same amber warning treatment `CoverageBanner` already
 * uses for partial coverage once masking exceeds 50%. */
function NdfiQualityStats({ stats }) {
  const maskedPct = stats.masked_fraction * 100;
  const heavilyMasked = stats.masked_fraction > 0.5;
  return (
    <div className="stat-grid">
      <div className={`stat-card${heavilyMasked ? " stat-card-warning" : ""}`}>
        <span className="stat-label">Masked area (cloud/shadow/no-data)</span>
        <span className="stat-value">{maskedPct.toFixed(1)}%</span>
        {heavilyMasked ? (
          <span className="stat-warning-hint">Most of this boundary could not be classified - treat this result with caution.</span>
        ) : null}
      </div>
      <div className="stat-card">
        <span className="stat-label">Valid pixels used</span>
        <span className="stat-value">{formatNumber(stats.valid_pixel_count, 0)}</span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Total pixels in boundary</span>
        <span className="stat-value">{formatNumber(stats.total_pixel_count, 0)}</span>
      </div>
    </div>
  );
}

function AnalysisStats({ result }) {
  const { stats, legend } = result;
  return (
    <>
      {result.analysis_id === "vnv_ndfi" ? <NdfiExperimentalNotice /> : null}
      <CoverageBanner pct={stats.coverage_pct} />
      {stats.summary ? <IndexSummaryCallout summary={stats.summary} /> : null}
      {stats.canopy_cover_threshold_pct != null ? <HansenStats stats={stats} /> : null}
      {stats.class_area_ha ? <ClassBreakdown classAreaHa={stats.class_area_ha} legend={legend} /> : null}
      {stats.class_area_ha_by_year ? (
        <YearlyClassBreakdown byYear={stats.class_area_ha_by_year} legend={legend} />
      ) : null}
      {stats.series ? <IndexTrendChart series={stats.series} /> : null}
      {stats.distribution ? <IndexDistribution distribution={stats.distribution} /> : null}
      {stats.masked_fraction != null ? <NdfiQualityStats stats={stats} /> : null}
      {stats.note ? <p className="analysis-note">{stats.note}</p> : null}
      {stats.methodology ? (
        <AnalysisMethodology methodology={stats.methodology} analysisId={result.analysis_id} />
      ) : null}
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
  // Wave: compute-source toggle. "gee" (the live catalog, as before) or
  // "vnv" (the frontend-only preview list, VNV_PREVIEW_ENTRIES above).
  const [computeSource, setComputeSource] = useState("gee");
  // Wave: raw-imagery browsing. Only meaningful for the 3 year_selectable
  // catalog entries - "latest" (no `year` query param at all) matches every
  // other analysis's existing parameter-free refresh request exactly.
  const [browseYear, setBrowseYear] = useState("latest");
  // Wave: analysis config and methodology. Only meaningful for the 7
  // catalog entries with a real `config` block - seeded from that entry's
  // own defaults on every selection change, same reset convention
  // `browseYear` already follows just below. Clicking Run always POSTs
  // whatever's currently here - there's no separate "check if this variant
  // is already cached" probe, the backend's own Redis cache (Wave: GEE tile
  // caching) already makes a repeat request for the same resolved params
  // cheap without the frontend needing to guess first.
  const [configParams, setConfigParams] = useState({});

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

  // Wave: compute-source toggle. "gee" filters the live catalog down to
  // `status: "available"` entries that are NOT the VNV pipeline's own
  // catalog entry (`compute_source === "vnv_pipeline"` - excluded here so it
  // can't leak into a geeGroupFor bucket, since that grouping only looks at
  // category/execution) and regroups the rest (geeGroupFor) for display.
  // "vnv" shows the real vnv_ndfi entry as fetched from the backend (first,
  // real status/computed_at, selectable/runnable) followed by the two
  // still-hardcoded, still-never-runnable preview stubs. Everything
  // downstream (selected, groups, config panel, methodology, runAnalysis)
  // reads from this instead of raw `entries`.
  const visibleEntries = useMemo(() => {
    if (computeSource === "vnv") {
      const realNdfi = (entries ?? []).find((e) => e.compute_source === "vnv_pipeline");
      return realNdfi ? [realNdfi, ...VNV_PREVIEW_ENTRIES] : VNV_PREVIEW_ENTRIES;
    }
    return (entries ?? [])
      .filter((e) => e.status === "available" && e.compute_source !== "vnv_pipeline")
      .map((e) => ({ ...e, category: geeGroupFor(e) }));
  }, [entries, computeSource]);

  const selected = visibleEntries.find((e) => e.id === selectedId) ?? null;

  // Only ever fetched when the list already says there IS a cached result -
  // `computed_at` is authoritative, so a plain 404 (never computed) never
  // has to be told apart from a real error here.
  useEffect(() => {
    setResult(null);
    setResultError(null);
    setBrowseYear("latest");
    setConfigParams(defaultConfigParams(selected?.config));
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

  // Wave: raw-imagery browsing. Last 8 calendar years, newest first - a
  // single shared range across all 3 browse sensors (S2 since 2017, S1
  // since 2015, Landsat since 2013) rather than a per-sensor floor: picking
  // a year before a given sensor's own coverage start just yields "no scene
  // found" from that sensor's own filterDate, the same graceful empty
  // result any other real-but-uncovered request already gets, not a hard
  // error worth complicating this list over.
  const browseYearOptions = useMemo(() => {
    const currentYear = new Date().getFullYear();
    return Array.from({ length: 8 }, (_, i) => currentYear - i);
  }, []);

  // Wave: analysis config and methodology. `year_max` is always a real,
  // live number by the time it reaches here for the 5 indices too - the
  // backend overlays current_veg_index_years()'s real ceiling before
  // serializing (see GEEAnalysisService.list_catalog), so this never has to
  // guess or hardcode an eligibility cutoff itself.
  const configYearOptions = useMemo(() => {
    if (!selected?.config) return [];
    const { year_min, year_max } = selected.config;
    const opts = [];
    for (let y = year_max; y >= year_min; y--) opts.push(y);
    return opts;
  }, [selected]);

  // No network call - a pure client-side check against the same
  // supported_combos the backend will otherwise reject with a specific
  // reason, so an unsupported pick is caught before Run is even clickable,
  // not after a wasted request.
  const comboAllowed = useMemo(() => {
    if (!selected?.config?.supported_combos) return true;
    return selected.config.supported_combos.some(
      ([source, masking]) => source === configParams.imagerySource && masking === configParams.cloudMasking
    );
  }, [selected, configParams]);

  const groups = useMemo(() => {
    const out = [];
    for (const e of visibleEntries) {
      const group = out.find((g) => g.category === e.category);
      if (group) group.entries.push(e);
      else out.push({ category: e.category, entries: [e] });
    }
    return out;
  }, [visibleEntries]);

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
      // Wave: raw-imagery browsing. "latest" (the default) omits `year`
      // entirely - every non-year_selectable analysis's request is
      // unaffected, exactly as parameter-free as before. Mutually exclusive
      // with configQuery below - no catalog entry is ever both
      // year_selectable AND config-bearing.
      const yearParam = selected.year_selectable && browseYear !== "latest" ? `?year=${browseYear}` : "";
      const configQuery = selected.config ? buildConfigQuery(selected.config, configParams) : "";
      const data = await apiFetch(
        `/projects/${projectId}/analyses/${analysisId}/refresh${yearParam || configQuery}`,
        { method: "POST" }
      );
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
    // Wave: VNV pipeline NDFI goes live. Shown regardless of which VNV entry
    // (if any) is selected, above whichever empty state below ends up
    // firing, not instead of it - now honest that NDFI itself is live
    // (still experimental, never for compliance use) while the other two
    // ForesToolboxRS methods genuinely have no implementation yet.
    const vnvDevNote =
      computeSource === "vnv" ? (
        <div className="sample-data-banner">
          NDFI — Spectral Unmixing now runs on the VNV Pipeline. Experimental — this method has not yet passed domain
          review and may be unreliable, especially on densely forested areas. Not for compliance reporting. Stocking
          Index and Control Plot Matching are still in development and cannot be run yet.
        </div>
      ) : null;
    if (!selected) {
      return (
        <>
          {vnvDevNote}
          <EmptyState title="Select an analysis" detail="Pick one from the list to see its results here." />
        </>
      );
    }
    if (selected.status !== "available") {
      return (
        <>
          {vnvDevNote}
          <EmptyState
            title="This analysis isn't built yet"
            detail={`${selected.description} It's listed so you can see it's planned - nothing has been computed.`}
          />
        </>
      );
    }
    return (
      <>
        {vnvDevNote}
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
        {canRun && selected.year_selectable ? (
          <label className="analysis-year-select">
            <span>Year</span>
            <select
              className="map-toolbar-select"
              value={browseYear}
              onChange={(e) => setBrowseYear(e.target.value)}
              disabled={running}
            >
              <option value="latest">Latest</option>
              {browseYearOptions.map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {canRun && selected.config ? (
          <div className="analysis-config-panel">
            <label className="analysis-year-select">
              <span>Years</span>
              <select
                className="map-toolbar-select"
                value={configParams.yearMode ?? "single"}
                onChange={(e) => setConfigParams((p) => ({ ...p, yearMode: e.target.value }))}
                disabled={running}
              >
                <option value="single">Single year</option>
                <option value="range">Year range</option>
              </select>
            </label>
            {configParams.yearMode === "range" ? (
              <>
                <label className="analysis-year-select">
                  <span>From</span>
                  <select
                    className="map-toolbar-select"
                    value={configParams.yearStart ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, yearStart: Number(e.target.value) }))}
                    disabled={running}
                  >
                    {configYearOptions.map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </label>
                <label className="analysis-year-select">
                  <span>To</span>
                  <select
                    className="map-toolbar-select"
                    value={configParams.yearEnd ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, yearEnd: Number(e.target.value) }))}
                    disabled={running}
                  >
                    {configYearOptions.map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </label>
              </>
            ) : (
              <label className="analysis-year-select">
                <span>Year</span>
                <select
                  className="map-toolbar-select"
                  value={configParams.year ?? ""}
                  onChange={(e) => setConfigParams((p) => ({ ...p, year: Number(e.target.value) }))}
                  disabled={running}
                >
                  {configYearOptions.map((y) => (
                    <option key={y} value={y}>{y}</option>
                  ))}
                </select>
              </label>
            )}
            {selected.config.season_editable ? (
              <>
                <label className="analysis-year-select">
                  <span>Season start</span>
                  <input
                    type="text"
                    className="map-toolbar-select"
                    placeholder="MM-DD"
                    pattern="\d{2}-\d{2}"
                    value={configParams.seasonStart ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, seasonStart: e.target.value }))}
                    disabled={running}
                  />
                </label>
                <label className="analysis-year-select">
                  <span>Season end</span>
                  <input
                    type="text"
                    className="map-toolbar-select"
                    placeholder="MM-DD"
                    pattern="\d{2}-\d{2}"
                    value={configParams.seasonEnd ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, seasonEnd: e.target.value }))}
                    disabled={running}
                  />
                </label>
              </>
            ) : null}
            {selected.config.imagery_sources ? (
              <>
                <label className="analysis-year-select">
                  <span>Imagery source</span>
                  <select
                    className="map-toolbar-select"
                    value={configParams.imagerySource ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, imagerySource: e.target.value }))}
                    disabled={running}
                  >
                    {selected.config.imagery_sources.map((source) => (
                      <option key={source} value={source}>{SOURCE_LABELS[source] ?? source}</option>
                    ))}
                  </select>
                </label>
                <label className="analysis-year-select">
                  <span>Cloud masking</span>
                  <select
                    className="map-toolbar-select"
                    value={configParams.cloudMasking ?? ""}
                    onChange={(e) => setConfigParams((p) => ({ ...p, cloudMasking: e.target.value }))}
                    disabled={running}
                  >
                    {selected.config.cloud_masking_methods.map((masking) => (
                      <option key={masking} value={masking}>{MASKING_LABELS[masking] ?? masking}</option>
                    ))}
                  </select>
                </label>
              </>
            ) : null}
            {!comboAllowed ? (
              <p className="field-hint field-hint-error">
                This combination isn't supported yet — only{" "}
                {SOURCE_LABELS[selected.config.supported_combos[0][0]] ?? selected.config.supported_combos[0][0]}
                {" + "}
                {MASKING_LABELS[selected.config.supported_combos[0][1]] ?? selected.config.supported_combos[0][1]}
                {" is implemented."}
              </p>
            ) : null}
          </div>
        ) : null}
        {canRun ? (
          // Wave: VNV pipeline NDFI goes live. vnv_ndfi is real and runnable
          // even under computeSource === "vnv" - it behaves exactly like any
          // GEE async analysis below (same running/comboAllowed gating, same
          // labels, same runAnalysis call). The two still-unbuilt preview
          // stubs (vnv_stocking_index, vnv_control_plot_matching) stay
          // permanently disabled - this branch is unreachable for them today
          // since they never leave the "isn't built yet" empty state above,
          // but is kept explicit here as the documented, permanent gate.
          (() => {
            const vnvStubSelected = computeSource === "vnv" && selected.id !== "vnv_ndfi";
            return (
              <button
                type="button"
                className="primary-button"
                disabled={vnvStubSelected ? true : running || (selected.config && !comboAllowed)}
                onClick={vnvStubSelected ? undefined : runAnalysis}
              >
                {vnvStubSelected
                  ? "Not yet available"
                  : running
                    ? runningLabel(runStatus)
                    : result
                      ? "Refresh"
                      : "Run analysis"}
              </button>
            );
          })()
        ) : null}
      </>
    );
  }

  return (
    <>
      <ErrorBanner message={listError} />
      <div className="analysis-layout">
        <aside className="analysis-column">
          {/* Wave: compute-source toggle. Cards, not a <select> - selecting
              either resets `selectedId` (the existing "selected became
              null" effects already clear result/configParams/etc, no need
              to duplicate that here). */}
          <div className="compute-source-section">
            <div className="compute-source-title">Compute source</div>
            <div className="compute-source-cards">
              <button
                type="button"
                className={`compute-source-card${
                  computeSource === "gee" ? " compute-source-card-active" : ""
                }`}
                aria-pressed={computeSource === "gee"}
                onClick={() => {
                  setComputeSource("gee");
                  setSelectedId(null);
                }}
              >
                <span className="compute-source-card-top">
                  <span className="compute-source-card-name">Google Earth Engine</span>
                  <span className="role-badge">LIVE</span>
                </span>
                <span className="compute-source-card-meta">ee-kdenish4 · noncommercial account</span>
              </button>
              <button
                type="button"
                className={`compute-source-card${
                  computeSource === "vnv" ? " compute-source-card-active" : ""
                }`}
                aria-pressed={computeSource === "vnv"}
                onClick={() => {
                  setComputeSource("vnv");
                  setSelectedId(null);
                }}
              >
                <span className="compute-source-card-top">
                  <span className="compute-source-card-name">VNV Pipeline</span>
                  <span className="role-badge role-badge-warning">EXPERIMENTAL</span>
                </span>
                <span className="compute-source-card-meta">CDSE + Landsat C2 · ForesToolboxRS</span>
                <span className="compute-source-card-desc">
                  Self-hosted pipeline sourced directly from Copernicus &amp; USGS, built for VM0047 production use.
                </span>
                {/* Wave: VNV pipeline NDFI goes live. Always rendered - not
                    gated on computeSource === "vnv" - since this card is
                    visible whether or not it's the selected one. */}
                <span className="compute-source-card-warning">
                  Experimental — this method has not yet passed domain review and may be unreliable, especially on
                  densely forested areas. Not for compliance reporting.
                </span>
              </button>
            </div>
          </div>
          <div className="analysis-column-title">Analyses</div>
          <div className="analysis-column-body">
            <p className="compute-source-sublabel">
              {computeSource === "gee"
                ? "Showing analyses available via Google Earth Engine."
                : "Showing analyses available via VNV Pipeline."}
            </p>
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
          // Identify support (GET .../analyses/{id}/point): only ever the ONE
          // selected, real (catalog "available") analysis - selection here is
          // single, not multi-select, so there is no "which of several GEE
          // layers" ambiguity to resolve yet. Passed regardless of whether it
          // has been run - an async index that hasn't gets a clear "run this
          // first" popup row (renderGeePixelRow) instead of no row at all.
          //
          // Wave: raw-imagery browsing. `year` comes from the CURRENTLY
          // RENDERED result's own `stats.scene_date` (not the `browseYear`
          // UI picker, which can be ahead of what's actually been refreshed)
          // - so a click always samples the same year the tile visually
          // shows, never silently "latest". `undefined` for every other
          // analysis, which the backend already treats as "no year param".
          activeAnalysis={
            selected && selected.status === "available"
              ? {
                  id: selected.id,
                  name: selected.name,
                  year: result?.stats?.scene_date ? Number(result.stats.scene_date.slice(0, 4)) : undefined,
                }
              : null
          }
        />

        <aside className="analysis-column">
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
