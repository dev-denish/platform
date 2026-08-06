import { useEffect, useState } from "react";
import { apiFetch } from "../config.js";
import Spinner from "../components/Spinner.jsx";
import ErrorBanner from "../components/ErrorBanner.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { formatDate } from "../lib/format.js";

// India's confirmed DNA values (migration 0016's seed row) - shown here so
// the page can flag a customized threshold at a glance without a second
// request.
const DEFAULTS = { canopy_cover_pct: 15, min_height_m: 2, min_area_ha: 0.05 };

const FIELDS = [
  { key: "canopy_cover_pct", label: "Canopy cover", unit: "%", step: "0.1" },
  { key: "min_height_m", label: "Minimum height", unit: "m", step: "0.1" },
  { key: "min_area_ha", label: "Minimum area", unit: "ha", step: "0.0001" },
];

export default function ForestDefinitionPage() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/forest-definition");
      setData(res);
      setForm({
        canopy_cover_pct: res.canopy_cover_pct,
        min_height_m: res.min_height_m,
        min_area_ha: res.min_area_ha,
      });
    } catch (err) {
      setError(err.message ?? "Could not load the forest definition.");
    } finally {
      setLoading(false);
    }
  }

  function updateField(key, value) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave() {
    setConfirmOpen(false);
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch("/forest-definition", {
        method: "PUT",
        body: JSON.stringify({
          canopy_cover_pct: Number(form.canopy_cover_pct),
          min_height_m: Number(form.min_height_m),
          min_area_ha: Number(form.min_area_ha),
        }),
      });
      setData(res);
      setForm({
        canopy_cover_pct: res.canopy_cover_pct,
        min_height_m: res.min_height_m,
        min_area_ha: res.min_area_ha,
      });
    } catch (err) {
      setError(err.message ?? "Could not save the forest definition.");
    } finally {
      setSaving(false);
    }
  }

  const isCustom = data && FIELDS.some((f) => Number(data[f.key]) !== DEFAULTS[f.key]);

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Compliance</p>
          <h1 className="page-title">Forest definition</h1>
        </div>
      </header>

      <p className="field-hint">
        The canopy cover, minimum height, and minimum area thresholds used to classify forest
        across every project's reports. Every account can see these values; changing them
        requires the Administrator role or an individually granted permission.
      </p>

      <ErrorBanner message={error} onRetry={load} />

      <ConfirmDialog
        open={confirmOpen}
        title="Save the forest definition?"
        detail={
          data && form
            ? `Canopy cover ${data.canopy_cover_pct}% → ${form.canopy_cover_pct}%, ` +
              `minimum height ${data.min_height_m}m → ${form.min_height_m}m, ` +
              `minimum area ${data.min_area_ha}ha → ${form.min_area_ha}ha. ` +
              "This affects every report across the platform that classifies forest."
            : ""
        }
        confirmLabel={saving ? "Saving…" : "Save"}
        danger
        onConfirm={handleSave}
        onCancel={() => setConfirmOpen(false)}
      />

      <section className="panel">
        {loading || !data || !form ? (
          <div className="full-screen-center">
            <Spinner label="Loading…" />
          </div>
        ) : (
          <>
            {isCustom ? (
              <p className="field-hint">
                <span className="status-badge tone-review">
                  <span className="status-dot" aria-hidden="true" />
                  Customized from the seeded India defaults
                </span>
              </p>
            ) : null}
            <form
              className="form-grid"
              onSubmit={(e) => {
                e.preventDefault();
                if (data.can_edit) setConfirmOpen(true);
              }}
            >
              {FIELDS.map((f) => (
                <label key={f.key} className="field">
                  <span className="field-label">
                    {f.label} ({f.unit})
                  </span>
                  {data.can_edit ? (
                    <input
                      type="number"
                      className="field-input"
                      step={f.step}
                      min="0"
                      value={form[f.key]}
                      disabled={saving}
                      onChange={(e) => updateField(f.key, e.target.value)}
                    />
                  ) : (
                    <span className="field-input">
                      {data[f.key]}
                      {f.unit}
                    </span>
                  )}
                  {Number(data[f.key]) !== DEFAULTS[f.key] ? (
                    <span className="field-hint">
                      Default: {DEFAULTS[f.key]}
                      {f.unit}
                    </span>
                  ) : null}
                </label>
              ))}
              {data.can_edit ? (
                <div className="form-actions">
                  <button type="submit" className="primary-button" disabled={saving}>
                    {saving ? "Saving…" : "Review & save"}
                  </button>
                </div>
              ) : null}
            </form>
            <p className="field-hint">
              Last updated {formatDate(data.updated_at)}
              {data.updated_by_username ? ` by ${data.updated_by_username}` : ""}.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
