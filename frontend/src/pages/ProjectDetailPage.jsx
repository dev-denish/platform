import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { StatusBadge } from "../components/StatusBadge.jsx";
import ProjectMap from "../components/ProjectMap.jsx";
import LandscapeEvolutionTable from "../components/LandscapeEvolutionTable.jsx";
import { canDeleteProject } from "../lib/roles.js";
import { formatDate, formatNumber, humanizeMetricName } from "../lib/format.js";
import { datedLayerGroups } from "../lib/timeline.js";

/**
 * One collapsible Key Metrics section per real layer (Phase 3 Wave G) -
 * `metrics` is that layer's own {metric_name: KpiValue} slice of
 * GET /projects/{id}/kpis's now layer_id-keyed response (see
 * ProjectService.get_kpis), not a project-wide flattened dict. Scales to
 * however many layers a project has - N layers is just N of these stacked,
 * each with its own small stat-grid, never a hardcoded layout.
 */
function LayerMetricsSection({ layer, metrics }) {
  const [expanded, setExpanded] = useState(true);
  const entries = metrics ? Object.entries(metrics) : [];

  return (
    <div className="layer-metrics-section">
      <button
        type="button"
        className="layer-metrics-header"
        onClick={() => setExpanded((e) => !e)}
      >
        <span className="layer-metrics-title">
          {layer.type}
          {layer.date_processed ? ` · ${layer.date_processed}` : ""}
        </span>
        <span className={`layer-metrics-chevron${expanded ? " layer-metrics-chevron-open" : ""}`} aria-hidden="true">
          ▾
        </span>
      </button>
      {expanded ? (
        entries.length === 0 ? (
          <EmptyState
            title="No metrics yet"
            detail="Metrics appear once this layer's dataset has been ingested."
          />
        ) : (
          <div className="stat-grid">
            {entries.map(([name, kpi]) => (
              <div className="stat-card" key={name}>
                <span className="stat-label">{humanizeMetricName(name)}</span>
                <span className="stat-value">
                  {formatNumber(kpi.value)} <span className="stat-unit">{kpi.unit}</span>
                </span>
              </div>
            ))}
          </div>
        )
      ) : null}
    </div>
  );
}

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [kpis, setKpis] = useState(null);
  const [layers, setLayers] = useState(null);
  const [evolution, setEvolution] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function handleDelete() {
    setConfirmOpen(false);
    setDeleting(true);
    setError(null);
    try {
      await apiFetch(`/projects/${projectId}`, { method: "DELETE" });
      navigate("/projects", {
        replace: true,
        state: { flash: `"${detail.name}" was deleted.` },
      });
    } catch (err) {
      setError(err.message ?? "Could not delete this project.");
      setDeleting(false);
    }
  }

  // Re-fetches only the layers endpoint (not the whole page) - used by
  // ProjectMap to pick up a fresh signed tile token after the current one
  // expires (see ProjectMap.jsx's tile-error handling).
  async function reloadLayers() {
    try {
      setLayers(await apiFetch(`/projects/${projectId}/layers`));
      return true;
    } catch {
      return false;
    }
  }

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [detailRes, kpisRes, layersRes, evolutionRes] = await Promise.all([
        apiFetch(`/projects/${projectId}`),
        apiFetch(`/projects/${projectId}/kpis`),
        apiFetch(`/projects/${projectId}/layers`),
        apiFetch(`/projects/${projectId}/evolution`),
      ]);
      setDetail(detailRes);
      setKpis(kpisRes);
      setLayers(layersRes);
      setEvolution(evolutionRes);
    } catch (err) {
      setError(err.message ?? "Could not load this project.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="full-screen-center">
        <Spinner label="Loading project…" />
      </div>
    );
  }

  if (error && !detail) {
    return (
      <div className="page">
        <ErrorBanner message={error} onRetry={load} />
      </div>
    );
  }

  // Same chronological (dated-first, then undated) ordering LayersPanel uses
  // on the map, so the two lists read consistently - lib/timeline.js's
  // date-grouping stays meaningfully shared, not duplicated.
  const orderedLayers = layers
    ? (() => {
        const dated = datedLayerGroups(layers.layers).map((g) => g.layer);
        const datedIds = new Set(dated.map((l) => l.layer_id));
        return [...dated, ...layers.layers.filter((l) => !datedIds.has(l.layer_id))];
      })()
    : [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">
            <Link to="/projects" className="link-button">
              Projects
            </Link>{" "}
            / {detail.name}
          </p>
          <h1 className="page-title">{detail.name}</h1>
          <div className="page-meta-row">
            <StatusBadge status={detail.status} />
            <span className="mono-cell">{detail.region ?? "Region unspecified"}</span>
            <span className="mono-cell">Started {formatDate(detail.start_date)}</span>
          </div>
        </div>
        {user && canDeleteProject(user.role) ? (
          <button
            type="button"
            className="danger-button"
            disabled={deleting}
            onClick={() => setConfirmOpen(true)}
          >
            {deleting ? "Deleting…" : "Delete project"}
          </button>
        ) : null}
      </header>

      <ConfirmDialog
        open={confirmOpen}
        title="Delete this project?"
        detail={`"${detail.name}" will be removed from every list and dashboard. Its datasets are kept and can be recovered by an administrator directly in the database if ever needed.`}
        confirmLabel="Delete project"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmOpen(false)}
      />

      <ErrorBanner message={error} onRetry={load} />

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Key metrics</h2>
        </div>
        {orderedLayers.length === 0 ? (
          <EmptyState title="No metrics yet" detail="Metrics appear once a dataset has been ingested." />
        ) : (
          <div className="layer-metrics-list">
            {orderedLayers.map((l) => (
              <LayerMetricsSection key={l.layer_id} layer={l} metrics={kpis?.layers?.[l.layer_id]} />
            ))}
          </div>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Spatial layers</h2>
        </div>
        {!layers || layers.layers.length === 0 ? (
          <EmptyState title="No layers yet" detail="Ingested rasters will appear here with their extent and preview." />
        ) : (
          <>
            <ProjectMap layers={layers.layers} onRefreshLayers={reloadLayers} />
            <div className="layer-grid">
              {layers.layers.map((l) => (
                <div className="layer-card" key={l.layer_id}>
                  <div className="layer-preview">
                    <img src={l.preview_url} alt={`${l.type} preview`} loading="lazy" />
                  </div>
                  <div className="layer-meta">
                    <span className="layer-type">{l.type}</span>
                    <span className="mono-cell">{l.crs}</span>
                    <span className="mono-cell">{l.pixel_size_m} m/px</span>
                    <span className="mono-cell">{l.date_processed ?? "undated"}</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Landscape evolution</h2>
        </div>
        <LandscapeEvolutionTable evolution={evolution} />
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Datasets</h2>
        </div>
        {detail.datasets.length === 0 ? (
          <EmptyState title="No datasets yet" />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Source</th>
                <th>Accuracy</th>
                <th>Processed</th>
                <th>Loaded</th>
              </tr>
            </thead>
            <tbody>
              {detail.datasets.map((d) => (
                <tr key={d.dataset_id}>
                  <td>{d.type}</td>
                  <td className="mono-cell">{d.source ?? "—"}</td>
                  <td className="mono-cell">{d.accuracy_score != null ? `${formatNumber(d.accuracy_score)}%` : "—"}</td>
                  <td className="mono-cell">{formatDate(d.date_processed)}</td>
                  <td className="mono-cell">{formatDate(d.loaded_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
