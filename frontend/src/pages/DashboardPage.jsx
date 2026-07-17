import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { apiFetch } from "../config.js";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import { StatusBadge } from "../components/StatusBadge.jsx";
import Legend from "../components/Legend.jsx";
import LandCoverPie from "../components/LandCoverPie.jsx";
import PortfolioMap from "../components/PortfolioMap.jsx";
import { formatDate, formatNumber, humanizeMetricName } from "../lib/format.js";
import { classColor, DATASET_TYPE_COLORS } from "../lib/colors.js";

// Cap on how many projects we fetch layer geometry for, to build the portfolio
// map. Each project needs its own /layers call (the API has no bulk endpoint for
// this), so this bounds worst-case parallel requests on a large portfolio. A
// missing/failed project's layers are simply skipped (see Promise.allSettled
// below) rather than failing the whole dashboard.
const MAP_PROJECT_CAP = 30;

// Recharts takes color as a JS prop, not CSS, so this can't just reference
// var(--text-faint) — keep this hex in sync with --text-faint in index.css.
const AXIS_TICK_COLOR = "#63756c";

// ---------------------------------------------------------------------------
// There is no backend model yet for carbon removal, verified credits, plots
// monitored, data-quality %, or verification status - so the two charts below
// are NOT live: they are fixed placeholder arrays, rendered only to preview the
// dashboard's target layout, and every panel that uses them carries a visible
// "Sample data" caption. Replace with a real fetch once a carbon-accounting
// endpoint exists; do not remove the caption before then.
// ---------------------------------------------------------------------------
const SAMPLE_CARBON_TREND = [
  { month: "Feb", tco2e: 1180 },
  { month: "Mar", tco2e: 1340 },
  { month: "Apr", tco2e: 1290 },
  { month: "May", tco2e: 1510 },
  { month: "Jun", tco2e: 1690 },
  { month: "Jul", tco2e: 1820 },
];

const SAMPLE_VERIFICATION_STATUS = [
  { name: "Verified", value: 4, color: "#0B6B46" },
  { name: "Pending", value: 5, color: "#F5A623" },
  { name: "Rejected", value: 1, color: "#EF5350" },
];

export default function DashboardPage() {
  const [summary, setSummary] = useState(null);
  const [recent, setRecent] = useState(null);
  const [mapLayers, setMapLayers] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [summaryRes, recentRes, allProjectsRes] = await Promise.all([
        apiFetch("/summary"),
        apiFetch("/projects?limit=5&offset=0"),
        apiFetch(`/projects?limit=${MAP_PROJECT_CAP}&offset=0`),
      ]);
      setSummary(summaryRes);
      setRecent(recentRes);

      const layerResults = await Promise.allSettled(
        allProjectsRes.items.map((p) =>
          apiFetch(`/projects/${p.project_id}/layers`).then((r) =>
            r.layers.map((l) => ({ ...l, projectName: p.name }))
          )
        )
      );
      setMapLayers(
        layerResults.filter((r) => r.status === "fulfilled").flatMap((r) => r.value)
      );
    } catch (err) {
      setError(err.message ?? "Could not load the portfolio summary.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="full-screen-center">
        <Spinner label="Aggregating portfolio metrics…" />
      </div>
    );
  }

  const totals = summary ? Object.entries(summary.portfolio) : [];
  const classBreakdown = totals.filter(([metric]) => metric !== "total_area");
  const totalArea = summary?.portfolio?.total_area ?? null;
  const classSum = classBreakdown.reduce((sum, [, value]) => sum + value, 0);

  const pieData = classBreakdown.map(([metric, value]) => ({
    name: humanizeMetricName(metric),
    value,
  }));

  const classLegendItems = pieData.map((d) => ({ label: d.name, color: classColor(d.name) }));

  const presentTypes = [...new Set(mapLayers.map((l) => l.type))];
  const typeLegendItems = presentTypes.map((t) => ({
    label: t,
    color: DATASET_TYPE_COLORS[t] ?? "#0B6B46",
  }));

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Overview</p>
          <h1 className="page-title">Dashboard</h1>
        </div>
      </header>

      <ErrorBanner message={error} onRetry={load} />

      <section className="stat-grid stat-grid-lead">
        <div className="stat-card stat-card-lead">
          <span className="stat-sweep" aria-hidden="true" />
          <KpiIcon d="M4 6 H20 M4 12 H20 M4 18 H14" />
          <span className="stat-label">Projects tracked</span>
          <span className="stat-value">{summary?.project_count ?? 0}</span>
        </div>
        <div className="stat-card stat-card-lead">
          <span className="stat-sweep" aria-hidden="true" />
          <KpiIcon d="M4 8 L12 4 L20 8 L20 16 L12 20 L4 16 Z" />
          <span className="stat-label">Total measured area</span>
          <span className="stat-value">
            {totalArea != null ? formatNumber(totalArea) : "0"} <span className="stat-unit">ha</span>
          </span>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Land cover composition</h2>
        </div>

        {classBreakdown.length === 0 ? (
          <EmptyState
            title="No land-cover data yet"
            detail="Class-level breakdown appears once a dataset with a class legend is ingested."
          />
        ) : (
          <div className="composition-grid">
            <div className="composition-chart">
              <LandCoverPie data={pieData} />
              <Legend items={classLegendItems} />
            </div>
            <table className="data-table composition-table">
              <thead>
                <tr>
                  <th>Class</th>
                  <th>Area</th>
                  <th>Share</th>
                </tr>
              </thead>
              <tbody>
                {classBreakdown
                  .slice()
                  .sort(([, a], [, b]) => b - a)
                  .map(([metric, value]) => {
                    const label = humanizeMetricName(metric);
                    const share = classSum > 0 ? (value / classSum) * 100 : 0;
                    return (
                      <tr key={metric}>
                        <td>
                          <span className="legend-item">
                            <span
                              className="legend-swatch"
                              style={{ background: classColor(label) }}
                              aria-hidden="true"
                            />
                            {label}
                          </span>
                        </td>
                        <td className="mono-cell">{formatNumber(value)} ha</td>
                        <td className="mono-cell">{formatNumber(share, 1)}%</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Project coverage</h2>
        </div>
        {mapLayers.length === 0 ? (
          <EmptyState
            title="No spatial layers yet"
            detail="Ingested raster extents will appear here across your whole portfolio."
          />
        ) : (
          <>
            <PortfolioMap layers={mapLayers} />
            <Legend items={typeLegendItems} title="Dataset type" />
          </>
        )}
      </section>

      <div className="dashboard-grid">
        <section className="panel panel-sample">
          <div className="panel-header">
            <h2 className="panel-title">Carbon removal trend</h2>
          </div>
          <SampleDataBanner />
          <CarbonTrendChart data={SAMPLE_CARBON_TREND} />
        </section>

        <section className="panel panel-sample">
          <div className="panel-header">
            <h2 className="panel-title">Verification status</h2>
          </div>
          <SampleDataBanner />
          <VerificationDonut data={SAMPLE_VERIFICATION_STATUS} />
        </section>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Recently updated projects</h2>
          <Link to="/projects" className="link-button">
            View all →
          </Link>
        </div>

        {recent && recent.items.length === 0 ? (
          <EmptyState
            title="No projects yet"
            detail="Once a dataset is ingested, its project will appear here."
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Region</th>
                <th>Status</th>
                <th>Latest accuracy</th>
                <th>Last processed</th>
              </tr>
            </thead>
            <tbody>
              {recent?.items.map((p) => (
                <tr key={p.project_id}>
                  <td>
                    <Link to={`/projects/${p.project_id}`} className="table-link">
                      {p.name}
                    </Link>
                  </td>
                  <td className="mono-cell">{p.region ?? "—"}</td>
                  <td>
                    <StatusBadge status={p.status} />
                  </td>
                  <td className="mono-cell">
                    {p.latest_accuracy != null ? `${formatNumber(p.latest_accuracy)}%` : "—"}
                  </td>
                  <td className="mono-cell">{formatDate(p.latest_processed)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

/** Loud, structurally-distinct warning banner for the two placeholder carbon
 * panels — these render fabricated tCO2e-adjacent numbers ahead of a real
 * carbon-accounting endpoint, and must never be mistaken for verified figures. */
function SampleDataBanner() {
  return (
    <div className="sample-data-banner" role="note">
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
        <path
          d="M12 4 L21 19 H3 Z M12 10 V14 M12 17 H12.01"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      Sample data — pending carbon accounting module. Not verified figures.
    </div>
  );
}

function KpiIcon({ d }) {
  return (
    <span className="kpi-card-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="18" height="18">
        <path d={d} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  );
}

/** Sample-only: monthly cumulative tCO2e removal, placeholder until a real
 * carbon-accounting endpoint exists (see SAMPLE_CARBON_TREND above). */
function CarbonTrendChart({ data }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <defs>
          <linearGradient id="carbonTrendFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#0B6B46" stopOpacity={0.28} />
            <stop offset="100%" stopColor="#0B6B46" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#E5ECE8" />
        <XAxis dataKey="month" tick={{ fontSize: 12, fill: AXIS_TICK_COLOR }} axisLine={{ stroke: "#E5ECE8" }} tickLine={false} />
        <YAxis
          domain={[0, "auto"]}
          tick={{ fontSize: 12, fill: AXIS_TICK_COLOR }}
          axisLine={{ stroke: "#E5ECE8" }}
          tickLine={false}
          width={48}
        />
        <Tooltip
          formatter={(v) => [`${formatNumber(v, 0)} tCO₂e`, "Removal"]}
          contentStyle={{ background: "#ffffff", border: "1px solid #e5ece8", borderRadius: 8, fontSize: 12 }}
        />
        <Area type="monotone" dataKey="tco2e" stroke="#0B6B46" strokeWidth={2} fill="url(#carbonTrendFill)" />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Sample-only: verified/pending/rejected dataset counts, placeholder until a
 * real verification-workflow endpoint exists (see SAMPLE_VERIFICATION_STATUS
 * above). */
function VerificationDonut({ data }) {
  return (
    <div className="composition-chart">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={52} outerRadius={86} paddingAngle={1.5} stroke="none">
            {data.map((d) => (
              <Cell key={d.name} fill={d.color} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value, name) => [`${value} dataset(s)`, name]}
            contentStyle={{ background: "#ffffff", border: "1px solid #e5ece8", borderRadius: 8, fontSize: 12 }}
          />
        </PieChart>
      </ResponsiveContainer>
      <Legend items={data.map((d) => ({ label: d.name, color: d.color }))} />
    </div>
  );
}
