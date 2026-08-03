/**
 * "Edit classes" action for an already-ingested classified raster layer
 * (Wave: editable class legend) - reuses the upload-time Class Legend
 * Builder as-is (no `file` prop, so its "Scan file" button is naturally
 * disabled - there's no raw upload left to scan post-ingest) to add/remove/
 * rename/recolor legend values, then PATCHes the result. The backend
 * recomputes this layer's area stats from its real stored COG and persists
 * them - `onSaved` is how the caller (LayersPanel) knows to refetch layers/
 * KPIs/evolution so every dashboard reflects the new numbers immediately.
 */
import { useState } from "react";
import ClassLegendBuilder, { buildLegend, rowsFromLegend } from "./ClassLegendBuilder.jsx";
import { apiFetch } from "../config.js";

export default function ClassLegendEditor({ layer, onSaved }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState(() => rowsFromLegend(layer.class_legend));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  function reopen() {
    setRows(rowsFromLegend(layer.class_legend));
    setError(null);
    setOpen(true);
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await apiFetch(`/layers/${layer.layer_id}/class-legend`, {
        method: "PATCH",
        body: JSON.stringify({ class_legend: buildLegend(rows) }),
      });
      setOpen(false);
      await onSaved?.();
    } catch (err) {
      setError(err.message ?? "Could not update this layer's classes.");
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="ghost-button" onClick={reopen}>
        Edit classes
      </button>
    );
  }

  return (
    <div className="class-legend-editor">
      <ClassLegendBuilder rows={rows} onChange={setRows} />
      {error ? <span className="field-hint field-hint-error">{error}</span> : null}
      <div className="symbology-popover-footer">
        <button type="button" className="ghost-button" onClick={() => setOpen(false)} disabled={saving}>
          Cancel
        </button>
        <button type="button" className="primary-button" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save classes"}
        </button>
      </div>
    </div>
  );
}
