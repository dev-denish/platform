import { useEffect, useState } from "react";
import { apiFetch } from "../config.js";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import EmptyState from "../components/EmptyState.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { formatDate } from "../lib/format.js";

/**
 * Wave: multi-format layers (Part B). Administrator-only management of the
 * WMS/WFS domain allow-list - the entire security boundary of this wave's
 * "no arbitrary external URLs" rule rests on this list being small,
 * deliberate, and admin-controlled (see app/services/external_fetch.py for
 * where it's actually enforced, on every fetch, not just here). Same
 * visibility/layout convention as UsersPage.jsx (the other Administrator-
 * only screen): a create form up top, a table below.
 */
export default function WmsDomainsPage() {
  const [domains, setDomains] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [newDomain, setNewDomain] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState(null);

  const [removeTarget, setRemoveTarget] = useState(null);
  const [removingId, setRemovingId] = useState(null);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setDomains(await apiFetch("/wms-domains"));
    } catch (err) {
      setError(err.message ?? "Could not load the allow-list.");
    } finally {
      setLoading(false);
    }
  }

  async function handleAdd(e) {
    e.preventDefault();
    if (!newDomain.trim()) return;
    setAdding(true);
    setAddError(null);
    try {
      await apiFetch("/wms-domains", {
        method: "POST",
        body: JSON.stringify({ domain: newDomain.trim() }),
      });
      setNewDomain("");
      await load();
    } catch (err) {
      setAddError(err.message ?? "Could not add this domain.");
    } finally {
      setAdding(false);
    }
  }

  async function handleRemove() {
    const target = removeTarget;
    setRemoveTarget(null);
    setRemovingId(target.domain_id);
    setError(null);
    try {
      await apiFetch(`/wms-domains/${target.domain_id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err.message ?? "Could not remove this domain.");
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Administration</p>
          <h1 className="page-title">WMS/WFS domains</h1>
        </div>
      </header>

      <p className="field-hint">
        A GIS Associate or Analyst adding a WMS/WFS layer can only pick from domains
        approved here - never a free-text URL. Removing a domain takes effect
        immediately: every layer already using it stops rendering on the very next
        request, not just for new layers.
      </p>

      <ErrorBanner message={error} onRetry={load} />

      <ConfirmDialog
        open={removeTarget != null}
        title="Remove this domain?"
        detail={
          removeTarget
            ? `"${removeTarget.domain}" will no longer be fetchable. Any existing WMS/WFS layer from this domain will stop rendering immediately.`
            : ""
        }
        confirmLabel="Remove"
        danger
        onConfirm={handleRemove}
        onCancel={() => setRemoveTarget(null)}
      />

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Approve a domain</h2>
        </div>
        <form className="form-grid" onSubmit={handleAdd}>
          <label className="field field-wide">
            <span className="field-label">Domain</span>
            <input
              className="field-input"
              value={newDomain}
              onChange={(e) => setNewDomain(e.target.value)}
              placeholder="e.g. mapserver.example.com"
              disabled={adding}
            />
            <span className="field-hint">
              A bare hostname only - no scheme, path, or port.
            </span>
          </label>
          <ErrorBanner message={addError} />
          <div className="form-actions">
            <button type="submit" className="primary-button" disabled={adding || !newDomain.trim()}>
              {adding ? "Adding…" : "Add domain"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title">Approved domains</h2>
        </div>
        {loading ? (
          <div className="full-screen-center">
            <Spinner label="Loading domains…" />
          </div>
        ) : !domains || domains.length === 0 ? (
          <EmptyState title="No domains approved yet" />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Domain</th>
                <th>Added</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {domains.map((d) => (
                <tr key={d.domain_id}>
                  <td className="mono-cell">{d.domain}</td>
                  <td className="mono-cell">{formatDate(d.created_at)}</td>
                  <td className="table-actions-cell">
                    <button
                      type="button"
                      className="link-button table-danger-link"
                      disabled={removingId === d.domain_id}
                      onClick={() => setRemoveTarget(d)}
                    >
                      {removingId === d.domain_id ? "Removing…" : "Remove"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
