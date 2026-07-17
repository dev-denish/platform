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
import { canDeleteProject } from "../lib/roles.js";
import { formatDate, formatNumber, humanizeMetricName } from "../lib/format.js";

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [kpis, setKpis] = useState(null);
  const [layers, setLayers] = useState(null);
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

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [detailRes, kpisRes, layersRes] = await Promise.all([
        apiFetch(`/projects/${projectId}`),
        apiFetch(`/projects/${projectId}/kpis`),
        apiFetch(`/projects/${projectId}/layers`),
      ]);
      setDetail(detailRes);
      setKpis(kpisRes);
      setLayers(layersRes);
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

  const kpiEntries = kpis ? Object.entries(kpis.kpis) : [];

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
        {kpiEntries.length === 0 ? (
          <EmptyState title="No metrics yet" detail="Metrics appear once a dataset has been ingested." />
        ) : (
          <div className="stat-grid">
            {kpiEntries.map(([name, kpi]) => (
              <div className="stat-card" key={name}>
                <span className="stat-label">{humanizeMetricName(name)}</span>
                <span className="stat-value">
                  {formatNumber(kpi.value)} <span className="stat-unit">{kpi.unit}</span>
                </span>
              </div>
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
            <ProjectMap layers={layers.layers} />
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
