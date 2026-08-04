import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import { useCollapse } from "../lib/useCollapse.js";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { StatusBadge } from "../components/StatusBadge.jsx";
import ProjectMap from "../components/ProjectMap.jsx";
import ProjectMembers from "../components/ProjectMembers.jsx";
import LandscapeEvolutionTable from "../components/LandscapeEvolutionTable.jsx";
import AddExternalLayerDialog from "../components/AddExternalLayerDialog.jsx";
import { canDeleteProject, canUpload } from "../lib/roles.js";
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
function LayerMetricsSection({ layer, metrics, projectId }) {
  const [expanded, toggleExpanded] = useCollapse(
    `collapse:project:${projectId}:layer-metrics:${layer.layer_id}`,
    true
  );
  const entries = metrics ? Object.entries(metrics) : [];

  return (
    <div className="layer-metrics-section">
      <button
        type="button"
        className="layer-metrics-header"
        aria-expanded={expanded}
        onClick={toggleExpanded}
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

// Wave: multi-format layers. A vector/external layer has no raster preview
// image (no COG, no fixed pixel size) - this label fills both gaps in the
// layer-card grid instead of showing a broken image or "null m/px".
function layerKindLabel(layerKind) {
  switch (layerKind) {
    case "vector":
      return "Vector layer";
    case "external_wms":
      return "WMS layer";
    case "external_wfs":
      return "WFS layer";
    default:
      return "Raster layer";
  }
}

export default function ProjectDetailPage() {
  const { projectId } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [detail, setDetail] = useState(null);
  const [kpis, setKpis] = useState(null);
  const [layers, setLayers] = useState(null);
  const [evolution, setEvolution] = useState(null);
  const [members, setMembers] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [addLayerOpen, setAddLayerOpen] = useState(false);

  const [keyMetricsOpen, toggleKeyMetrics] = useCollapse(`collapse:project:${projectId}:key-metrics`, true);
  const [evolutionOpen, toggleEvolution] = useCollapse(`collapse:project:${projectId}:evolution`, true);
  const [datasetsOpen, toggleDatasets] = useCollapse(`collapse:project:${projectId}:datasets`, true);

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
  // expires (see ProjectMap.jsx's tile-error handling). Wrapped in
  // useCallback so ProjectMap (memoized by a parallel wave) gets a stable
  // function identity across this page's now-more-frequent re-renders
  // (collapse toggles).
  const reloadLayers = useCallback(async () => {
    try {
      setLayers(await apiFetch(`/projects/${projectId}/layers`));
      return true;
    } catch {
      return false;
    }
  }, [projectId]);

  // Re-fetches only the members list - used by ProjectMembers after an
  // add/remove/role-change, same reload-just-this-section pattern as
  // reloadLayers above.
  async function reloadMembers() {
    setMembers(await apiFetch(`/projects/${projectId}/members`));
  }

  // Wave: editable class legend. A legend edit changes this layer's Total
  // Area and per-class KPI numbers, not just its tile colors (reloadLayers
  // alone only mints a fresh tile token) - so Key Metrics and Landscape
  // Evolution need a fresh read too, same "reload just what changed"
  // pattern as reloadLayers/reloadMembers above.
  const reloadLayersAndMetrics = useCallback(async () => {
    const [, kpisRes, evolutionRes] = await Promise.all([
      reloadLayers(),
      apiFetch(`/projects/${projectId}/kpis`),
      apiFetch(`/projects/${projectId}/evolution`),
    ]);
    setKpis(kpisRes);
    setEvolution(evolutionRes);
  }, [projectId, reloadLayers]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [detailRes, kpisRes, layersRes, evolutionRes, membersRes] = await Promise.all([
        apiFetch(`/projects/${projectId}`),
        apiFetch(`/projects/${projectId}/kpis`),
        apiFetch(`/projects/${projectId}/layers`),
        apiFetch(`/projects/${projectId}/evolution`),
        apiFetch(`/projects/${projectId}/members`),
      ]);
      setDetail(detailRes);
      setKpis(kpisRes);
      setLayers(layersRes);
      setEvolution(evolutionRes);
      setMembers(membersRes);
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
  // date-grouping stays meaningfully shared, not duplicated. Wave 3 (Added
  // Layers): an ad-hoc layer never has real metrics (see
  // KpiRepository.for_project's exclusion) - excluded here rather than
  // showing every one of them an empty "No metrics yet" card.
  const orderedLayers = layers
    ? (() => {
        const official = layers.layers.filter((l) => !l.is_adhoc);
        const dated = datedLayerGroups(official).map((g) => g.layer);
        const datedIds = new Set(dated.map((l) => l.layer_id));
        return [...dated, ...official.filter((l) => !datedIds.has(l.layer_id))];
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

      <ProjectMembers
        projectId={projectId}
        members={members?.members}
        currentUser={user}
        onChanged={reloadMembers}
      />

      <section className="panel">
        <button className="collapsible-header" aria-expanded={keyMetricsOpen} onClick={toggleKeyMetrics}>
          <span>Key metrics</span>
          <span className={`collapsible-chevron${keyMetricsOpen ? " collapsible-chevron-open" : ""}`} aria-hidden="true">▾</span>
        </button>
        <div className="collapsible-body" data-open={keyMetricsOpen} inert={keyMetricsOpen ? undefined : ""}>
          <div className="collapsible-body-inner">
            {orderedLayers.length === 0 ? (
              <EmptyState title="No metrics yet" detail="Metrics appear once a dataset has been ingested." />
            ) : (
              <div className="layer-metrics-list">
                {orderedLayers.map((l) => (
                  <LayerMetricsSection key={l.layer_id} layer={l} metrics={kpis?.layers?.[l.layer_id]} projectId={projectId} />
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Spatial layers</h2>
          {user && canUpload(user.role) ? (
            <button type="button" className="ghost-button" onClick={() => setAddLayerOpen(true)}>
              + Add WMS/WFS layer
            </button>
          ) : null}
        </div>

        <AddExternalLayerDialog
          open={addLayerOpen}
          projectId={projectId}
          projectName={detail.name}
          region={detail.region}
          onCreated={async () => {
            setAddLayerOpen(false);
            await reloadLayers();
          }}
          onCancel={() => setAddLayerOpen(false)}
        />

        {!layers || layers.layers.length === 0 ? (
          <EmptyState title="No layers yet" detail="Ingested rasters will appear here with their extent and preview." />
        ) : (
          <>
            <ProjectMap
              layers={layers.layers}
              onRefreshLayers={reloadLayers}
              onLegendChanged={reloadLayersAndMetrics}
              projectId={projectId}
            />
            <div className="layer-grid">
              {layers.layers.map((l) => (
                <div className="layer-card" key={l.layer_id}>
                  <div className="layer-preview">
                    {l.preview_url ? (
                      <img src={l.preview_url} alt={`${l.type} preview`} loading="lazy" />
                    ) : (
                      <div className="layer-preview-placeholder">{layerKindLabel(l.layer_kind)}</div>
                    )}
                  </div>
                  <div className="layer-meta">
                    <span className="layer-type">
                      {l.is_adhoc ? l.source ?? "Untitled layer" : l.type}
                      {l.is_adhoc ? <span className="layer-adhoc-badge"> Added</span> : null}
                    </span>
                    <span className="mono-cell">{l.crs}</span>
                    <span className="mono-cell">
                      {l.pixel_size_m != null ? `${l.pixel_size_m} m/px` : layerKindLabel(l.layer_kind)}
                    </span>
                    <span className="mono-cell">{l.date_processed ?? "undated"}</span>
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="panel">
        <button className="collapsible-header" aria-expanded={evolutionOpen} onClick={toggleEvolution}>
          <span>Landscape evolution</span>
          <span className={`collapsible-chevron${evolutionOpen ? " collapsible-chevron-open" : ""}`} aria-hidden="true">▾</span>
        </button>
        <div className="collapsible-body" data-open={evolutionOpen} inert={evolutionOpen ? undefined : ""}>
          <div className="collapsible-body-inner">
            <LandscapeEvolutionTable evolution={evolution} />
          </div>
        </div>
      </section>

      <section className="panel">
        <button className="collapsible-header" aria-expanded={datasetsOpen} onClick={toggleDatasets}>
          <span>Datasets</span>
          <span className={`collapsible-chevron${datasetsOpen ? " collapsible-chevron-open" : ""}`} aria-hidden="true">▾</span>
        </button>
        <div className="collapsible-body" data-open={datasetsOpen} inert={datasetsOpen ? undefined : ""}>
          <div className="collapsible-body-inner">
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
          </div>
        </div>
      </section>
    </div>
  );
}
