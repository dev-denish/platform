import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../config.js";
import ErrorBanner from "./ErrorBanner.jsx";

const INITIAL = { domain: "", service_kind: "wms", path: "", layer_name: "", is_reference: false };

/**
 * Wave: multi-format layers (Part B). Adding a WMS/WFS layer offers ONLY a
 * dropdown of Administrator-approved domains (from GET /wms-domains, open to
 * any UPLOAD_ROLES user) - never a free-text URL field. This is a UX
 * convenience only, not the security boundary: the real enforcement is
 * server-side, on every proxied fetch (app/services/external_fetch.py) - a
 * direct API call naming a non-approved domain is rejected there regardless
 * of what this form does.
 */
export default function AddExternalLayerDialog({ open, projectId, projectName, region, onCreated, onCancel }) {
  const ref = useRef(null);
  const [domains, setDomains] = useState(null);
  const [domainsError, setDomainsError] = useState(null);
  const [form, setForm] = useState(INITIAL);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setForm(INITIAL);
      setError(null);
      dialog.showModal();
      loadDomains();
    }
    if (!open && dialog.open) dialog.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function loadDomains() {
    setDomainsError(null);
    try {
      const list = await apiFetch("/wms-domains");
      setDomains(list);
      if (list.length > 0) setForm((f) => ({ ...f, domain: list[0].domain }));
    } catch (err) {
      setDomainsError(err.message ?? "Could not load the approved domain list.");
    }
  }

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function valid() {
    return form.domain && form.layer_name.trim().length > 0;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch(`/projects/${projectId}/external-layers`, {
        method: "POST",
        body: JSON.stringify({
          project_name: projectName,
          region: region || "Unspecified",
          domain: form.domain,
          service_kind: form.service_kind,
          path: form.path,
          layer_name: form.layer_name.trim(),
          is_reference: form.is_reference,
        }),
      });
      await onCreated();
    } catch (err) {
      setError(err.message ?? "Could not add this layer.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.();
      }}
    >
      <h2 className="confirm-dialog-title">Add a WMS/WFS layer</h2>
      <p className="confirm-dialog-detail">
        Only domains an Administrator has approved are offered here - to approve a new
        domain, see Admin → WMS/WFS domains.
      </p>

      <ErrorBanner message={domainsError} onRetry={loadDomains} />

      {domains && domains.length === 0 ? (
        <p className="field-hint">No domains are approved yet. Ask an Administrator to add one first.</p>
      ) : (
        <div className="form-grid">
          <label className="field">
            <span className="field-label">Domain</span>
            <select
              className="field-input"
              value={form.domain}
              onChange={(e) => update("domain", e.target.value)}
              disabled={submitting || !domains}
            >
              {(domains ?? []).map((d) => (
                <option key={d.domain_id} value={d.domain}>
                  {d.domain}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Service type</span>
            <select
              className="field-input"
              value={form.service_kind}
              onChange={(e) => update("service_kind", e.target.value)}
              disabled={submitting}
            >
              <option value="wms">WMS</option>
              <option value="wfs">WFS</option>
            </select>
          </label>
          <label className="field">
            <span className="field-label">Service path</span>
            <input
              className="field-input"
              value={form.path}
              onChange={(e) => update("path", e.target.value)}
              placeholder="/geoserver/wms"
              disabled={submitting}
            />
            <span className="field-hint">The path portion of the service URL on that domain.</span>
          </label>
          <label className="field">
            <span className="field-label">Layer name</span>
            <input
              className="field-input"
              value={form.layer_name}
              onChange={(e) => update("layer_name", e.target.value)}
              placeholder="e.g. ne:boundaries"
              disabled={submitting}
            />
          </label>
          <label className="field field-wide checkbox-field">
            <input
              type="checkbox"
              checked={form.is_reference}
              onChange={(e) => update("is_reference", e.target.checked)}
              disabled={submitting}
            />
            <span>Add as a shared reference layer (visible on every project, not just {projectName})</span>
          </label>
        </div>
      )}

      <ErrorBanner message={error} />
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel} disabled={submitting}>
          Cancel
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={submitting || !valid() || !domains || domains.length === 0}
          onClick={handleSubmit}
        >
          {submitting ? "Adding…" : "Add layer"}
        </button>
      </div>
    </dialog>
  );
}
