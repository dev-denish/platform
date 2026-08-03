import { useEffect, useRef, useState } from "react";
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
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [flash, setFlash] = useState(location.state?.flash ?? null);
  const [confirmTarget, setConfirmTarget] = useState(null); // project being considered for deletion
  const [deletingId, setDeletingId] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkConfirmOpen, setBulkConfirmOpen] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);
  const canDelete = user && canDeleteProject(user.role);
  const searchMounted = useRef(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => {
    load(offset, debouncedSearch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset]);

  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    // A new search term always starts back at page 1 - don't leave someone
    // stranded on, say, page 7 of a 2-result search. If offset is already 0
    // the effect above won't fire on its own, so load directly here.
    if (offset === 0) {
      load(0, debouncedSearch);
    } else {
      setOffset(0);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  useEffect(() => {
    if (!location.state?.flash) return;
    // Show it once, then drop it from history state so a refresh/back-nav
    // doesn't keep re-showing a stale confirmation.
    navigate(location.pathname, { replace: true, state: {} });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load(currentOffset, currentSearch) {
    setLoading(true);
    setError(null);
    setSelected(new Set());
    try {
      const params = new URLSearchParams({ limit: LIMIT, offset: currentOffset });
      if (currentSearch) params.set("search", currentSearch);
      const res = await apiFetch(`/projects?${params}`);
      setPage(res);
    } catch (err) {
      setError(err.message ?? "Could not load projects.");
    } finally {
      setLoading(false);
    }
  }

  function toggleSelected(projectId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  }

  function toggleSelectAll() {
    const visibleIds = page?.items.map((p) => p.project_id) ?? [];
    const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
    setSelected(allSelected ? new Set() : new Set(visibleIds));
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

  async function handleBulkDelete() {
    const ids = [...selected];
    setBulkConfirmOpen(false);
    setBulkDeleting(true);
    setError(null);
    try {
      const { results } = await apiFetch("/projects/bulk-delete", {
        method: "POST",
        body: JSON.stringify({ ids }),
      });
      const succeededIds = new Set(results.filter((r) => r.success).map((r) => r.id));
      const failed = results.filter((r) => !r.success);
      setPage((p) => ({
        ...p,
        items: p.items.filter((i) => !succeededIds.has(i.project_id)),
        total: p.total - succeededIds.size,
      }));
      setSelected(new Set());
      if (succeededIds.size > 0) {
        setFlash(`${succeededIds.size} project${succeededIds.size === 1 ? "" : "s"} deleted.`);
      }
      if (failed.length > 0) {
        setError(
          `${failed.length} project${failed.length === 1 ? "" : "s"} could not be deleted: ` +
            failed.map((r) => `"${r.name}" (${r.error})`).join("; ")
        );
      }
    } catch (err) {
      setError(err.message ?? "Could not delete these projects.");
    } finally {
      setBulkDeleting(false);
    }
  }

  const selectedNames = page ? page.items.filter((p) => selected.has(p.project_id)).map((p) => p.name) : [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Registry</p>
          <h1 className="page-title">Projects</h1>
        </div>
      </header>

      <SuccessBanner message={flash} />
      <ErrorBanner message={error} onRetry={() => load(offset, debouncedSearch)} />

      <input
        type="search"
        className="field-input projects-search"
        placeholder="Search projects by name…"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        aria-label="Search projects by name"
      />

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

      <ConfirmDialog
        open={bulkConfirmOpen}
        title={`Delete ${selectedNames.length} project${selectedNames.length === 1 ? "" : "s"}?`}
        detail="These will be removed from every list and dashboard. Their datasets are kept and can be recovered by an administrator directly in the database if ever needed."
        confirmLabel="Delete projects"
        danger
        onConfirm={handleBulkDelete}
        onCancel={() => setBulkConfirmOpen(false)}
      >
        <ul className="bulk-delete-list">
          {selectedNames.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      </ConfirmDialog>

      {canDelete && selected.size > 0 ? (
        <div className="bulk-action-bar">
          <span className="bulk-action-bar-count">
            {selected.size} selected
          </span>
          <button
            type="button"
            className="danger-button"
            disabled={bulkDeleting}
            onClick={() => setBulkConfirmOpen(true)}
          >
            {bulkDeleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      ) : null}

      {loading ? (
        <div className="full-screen-center">
          <Spinner label="Loading projects…" />
        </div>
      ) : page && page.items.length === 0 ? (
        debouncedSearch ? (
          <EmptyState
            title="No projects match"
            detail={`No project names match "${debouncedSearch}".`}
          />
        ) : (
          <EmptyState title="No projects yet" detail="Ingest a dataset to create the first project." />
        )
      ) : (
        <section className="panel">
          <table className="data-table">
            <thead>
              <tr>
                {canDelete ? (
                  <th className="select-cell">
                    <input
                      type="checkbox"
                      aria-label="Select all projects on this page"
                      checked={
                        page.items.length > 0 && page.items.every((p) => selected.has(p.project_id))
                      }
                      onChange={toggleSelectAll}
                    />
                  </th>
                ) : null}
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
                  {canDelete ? (
                    <td className="select-cell">
                      <input
                        type="checkbox"
                        aria-label={`Select "${p.name}"`}
                        checked={selected.has(p.project_id)}
                        onChange={() => toggleSelected(p.project_id)}
                      />
                    </td>
                  ) : null}
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
