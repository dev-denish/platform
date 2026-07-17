import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiFetch } from "../config.js";
import { useAuth } from "../context/AuthContext.jsx";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import SuccessBanner from "../components/SuccessBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Pagination from "../components/Pagination.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { StatusBadge } from "../components/StatusBadge.jsx";
import { canDeleteProject } from "../lib/roles.js";
import { formatDate, formatNumber } from "../lib/format.js";

const LIMIT = 20;

export default function ProjectsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [page, setPage] = useState(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [flash, setFlash] = useState(location.state?.flash ?? null);
  const [confirmTarget, setConfirmTarget] = useState(null); // project being considered for deletion
  const [deletingId, setDeletingId] = useState(null);
  const canDelete = user && canDeleteProject(user.role);

  useEffect(() => {
    load(offset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset]);

  useEffect(() => {
    if (!location.state?.flash) return;
    // Show it once, then drop it from history state so a refresh/back-nav
    // doesn't keep re-showing a stale confirmation.
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load(currentOffset) {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/projects?limit=${LIMIT}&offset=${currentOffset}`);
      setPage(res);
    } catch (err) {
      setError(err.message ?? "Could not load projects.");
    } finally {
      setLoading(false);
    }
  }

  async function handleDelete() {
    const target = confirmTarget;
    setConfirmTarget(null);
    setDeletingId(target.project_id);
    setError(null);
    try {
      await apiFetch(`/projects/${target.project_id}`, { method: "DELETE" });
      setPage((p) => ({
        ...p,
        items: p.items.filter((i) => i.project_id !== target.project_id),
        total: p.total - 1,
      }));
      setFlash(`"${target.name}" was deleted.`);
    } catch (err) {
      setError(err.message ?? "Could not delete this project.");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Registry</p>
          <h1 className="page-title">Projects</h1>
        </div>
      </header>

      <SuccessBanner message={flash} />
      <ErrorBanner message={error} onRetry={() => load(offset)} />

      <ConfirmDialog
        open={confirmTarget != null}
        title="Delete this project?"
        detail={
          confirmTarget
            ? `"${confirmTarget.name}" will be removed from every list and dashboard. Its datasets are kept and can be recovered by an administrator directly in the database if ever needed.`
            : ""
        }
        confirmLabel="Delete project"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmTarget(null)}
      />

      {loading ? (
        <div className="full-screen-center">
          <Spinner label="Loading projects…" />
        </div>
      ) : page && page.items.length === 0 ? (
        <EmptyState title="No projects yet" detail="Ingest a dataset to create the first project." />
      ) : (
        <section className="panel">
          <table className="data-table">
            <thead>
              <tr>
                <th>Project</th>
                <th>Region</th>
                <th>Status</th>
                <th>Latest accuracy</th>
                <th>Last processed</th>
                {canDelete ? <th /> : null}
              </tr>
            </thead>
            <tbody>
              {page?.items.map((p) => (
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
                  {canDelete ? (
                    <td className="table-actions-cell">
                      <button
                        type="button"
                        className="link-button table-danger-link"
                        disabled={deletingId === p.project_id}
                        onClick={() => setConfirmTarget(p)}
                      >
                        {deletingId === p.project_id ? "Deleting…" : "Delete"}
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {page ? (
            <Pagination total={page.total} limit={page.limit} offset={page.offset} onChange={setOffset} />
          ) : null}
        </section>
      )}
    </div>
  );
}
