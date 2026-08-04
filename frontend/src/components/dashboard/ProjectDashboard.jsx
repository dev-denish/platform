import { useState } from "react";
import { Download } from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import EmptyState from "../EmptyState.jsx";
import LandCoverPie from "../LandCoverPie.jsx";
import Legend from "../Legend.jsx";
import ActivityFeed from "./ActivityFeed.jsx";
import { formatDate, formatNumber, humanizeMetricName } from "../../lib/format.js";
import { classColor } from "../../lib/colors.js";
import { datedLayerGroups } from "../../lib/timeline.js";
import { exportDashboardXlsx } from "../../lib/exportDashboard.js";

const AXIS_TICK_COLOR = "#63756c";

function datasetLabel(layer) {
  return layer.date_processed ? `${layer.type} · ${layer.date_processed}` : layer.type;
}

/**
 * Per-project Dashboard tab (redesign Part B). Everything here reads data
 * ProjectDetailPage already fetched (layers/kpis/evolution/members) - no new
 * endpoints except the activity feed, which owns its own fetch (see
 * ActivityFeed.jsx). The critical fix vs. the old portfolio-wide Dashboard:
 * the composition pie is scoped to ONE manually-selected layer's own KPI
 * slice (kpis.layers[id]), never combined across layers.
 */
export default function ProjectDashboard({ projectId, projectName, layers, kpis, evolution, members }) {
  const classifiedLayers = [...layers]
    .filter((l) => l.layer_kind === "raster" && !!l.class_legend)
    .sort((a, b) => (b.date_processed ?? "").localeCompare(a.date_processed ?? ""));

  const [selectedLayerId, setSelectedLayerId] = useState(classifiedLayers[0]?.layer_id ?? "");

  const metricEntries = Object.entries(kpis?.layers?.[selectedLayerId] ?? {}).filter(
    ([name]) => name !== "total_area"
  );
  const classSum = metricEntries.reduce((sum, [, kpi]) => sum + kpi.value, 0);
  const pieData = metricEntries.map(([name, kpi]) => ({ name: humanizeMetricName(name), value: kpi.value }));
  const classLegendItems = pieData.map((d) => ({ label: d.name, color: classColor(d.name) }));

  const forestRows = (evolution?.classes ?? []).filter((row) =>
    humanizeMetricName(row.metric_name).toLowerCase().includes("forest")
  );
  const forestChartData = (evolution?.dates ?? []).map((d) => {
    const point = { date: d };
    forestRows.forEach((row) => {
      point[row.metric_name] = row.area_by_date_ha[d];
    });
    return point;
  });

  const needsReingestionCount = layers.filter((l) => l.needs_reingestion).length;
  const verifiedCount = layers.filter((l) => l.accuracy_score != null).length;

  const monitoringDates = datedLayerGroups(layers.filter((l) => !l.is_adhoc)).map((g) => g.date);

  const checklist = [
    { label: "Has a boundary/reference layer", ok: layers.some((l) => l.is_reference) },
    { label: "Has at least one classified layer", ok: classifiedLayers.length > 0 },
    { label: "Has 2+ dated layers (evolution possible)", ok: !!evolution?.applicable },
    { label: "Has more than one project member", ok: (members?.length ?? 0) > 1 },
    { label: "Has at least one verified layer", ok: verifiedCount > 0 },
  ];

  function handleExport() {
    const compositionRows = metricEntries.map(([name, kpi]) => {
      const share = classSum > 0 ? (kpi.value / classSum) * 100 : 0;
      return {
        Class: humanizeMetricName(name),
        "Area (ha)": Number(kpi.value.toFixed(3)),
        "Share (%)": Number(share.toFixed(1)),
      };
    });
    const qualityRows = layers.map((l) => ({
      Layer: l.is_adhoc ? l.source ?? "Untitled layer" : l.type,
      Date: l.date_processed ?? "—",
      Status: l.needs_reingestion ? "Needs re-ingestion" : l.accuracy_score != null ? "Verified" : "—",
      Accuracy: l.accuracy_score ?? "—",
    }));
    exportDashboardXlsx({ projectName, compositionRows, qualityRows });
  }

  return (
    <div className="dashboard-tab">
      <div className="dashboard-toolbar">
        <button type="button" className="ghost-button" onClick={handleExport}>
          <Download size={14} strokeWidth={2} className="icon" aria-hidden="true" /> Export to Excel
        </button>
      </div>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Land cover composition</h2>
          {classifiedLayers.length > 0 ? (
            <label className="dataset-select-row">
              Dataset
              <select
                className="field-input"
                value={selectedLayerId}
                onChange={(e) => setSelectedLayerId(e.target.value)}
              >
                {classifiedLayers.map((l) => (
                  <option key={l.layer_id} value={l.layer_id}>
                    {datasetLabel(l)}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
        </div>

        {classifiedLayers.length === 0 ? (
          <EmptyState
            title="No classified dataset yet"
            detail="Land cover composition appears once a raster with a class legend is ingested."
          />
        ) : metricEntries.length === 0 ? (
          <EmptyState title="No land-cover data yet" detail="This dataset has no class-level metrics." />
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
                {metricEntries
                  .slice()
                  .sort(([, a], [, b]) => b.value - a.value)
                  .map(([name, kpi]) => {
                    const label = humanizeMetricName(name);
                    const share = classSum > 0 ? (kpi.value / classSum) * 100 : 0;
                    return (
                      <tr key={name}>
                        <td>
                          <span className="legend-item">
                            <span className="legend-swatch" style={{ background: classColor(label) }} aria-hidden="true" />
                            {label}
                          </span>
                        </td>
                        <td className="mono-cell">{formatNumber(kpi.value)} ha</td>
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
        <h2 className="panel-title">Forest cover trend</h2>
        {forestRows.length === 0 ? (
          <EmptyState
            title="No forest-class data yet"
            detail="Appears once this project has a forest-family class tracked across 2+ classified dates."
          />
        ) : (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={forestChartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5ECE8" />
              <XAxis dataKey="date" tick={{ fontSize: 12, fill: AXIS_TICK_COLOR }} axisLine={{ stroke: "#E5ECE8" }} tickLine={false} />
              <YAxis
                domain={[0, "auto"]}
                tick={{ fontSize: 12, fill: AXIS_TICK_COLOR }}
                axisLine={{ stroke: "#E5ECE8" }}
                tickLine={false}
                width={56}
              />
              <Tooltip formatter={(v, name) => [`${formatNumber(v)} ha`, humanizeMetricName(name)]} />
              {forestRows.map((row) => (
                <Line
                  key={row.metric_name}
                  type="monotone"
                  dataKey={row.metric_name}
                  name={row.metric_name}
                  stroke={classColor(humanizeMetricName(row.metric_name))}
                  strokeWidth={2}
                  dot={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </section>

      <div className="dashboard-grid">
        <section className="panel">
          <h2 className="panel-title">Data quality</h2>
          <p className="data-quality-line">{needsReingestionCount} layers need re-ingestion</p>
          <p className="data-quality-line">{verifiedCount} layers verified</p>
        </section>

        <section className="panel">
          <h2 className="panel-title">Monitoring periods</h2>
          {monitoringDates.length === 0 ? (
            <EmptyState title="No dated layers yet" />
          ) : (
            <ul className="monitoring-list">
              {monitoringDates.map((d) => (
                <li key={d}>{formatDate(d)}</li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="dashboard-grid">
        <section className="panel">
          <h2 className="panel-title">Project completeness</h2>
          <ul className="checklist-list">
            {checklist.map((item) => (
              <li key={item.label} className={item.ok ? "checklist-yes" : "checklist-no"}>
                {item.ok ? "✓" : "✗"} {item.label}
              </li>
            ))}
          </ul>
        </section>

        <section className="panel">
          <h2 className="panel-title">Recent activity</h2>
          <ActivityFeed projectId={projectId} />
        </section>
      </div>
    </div>
  );
}
