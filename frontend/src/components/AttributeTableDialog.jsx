import { useEffect, useRef } from "react";
import { legendEntryColor, legendEntryLabel } from "../lib/symbology.js";
import { formatNumber } from "../lib/format.js";

/**
 * Full-attributes counterpart to the map's small "What's here" pixel popup
 * (Wave: Map Toolbar Enhancement v2, Tier 3, per direct user direction: the
 * mockup's "Quick analysis" context-menu item was renamed to "Attribute
 * table" and, unlike the small popup, opens the COMPLETE unabbreviated data
 * for the clicked point - every band as its own row rather than a single
 * "B1: x · B2: y" joined line, every GEE field (class/value/unit/detail)
 * broken out separately - reusing the exact same rows ProjectMap's
 * inspectPixel/fetchPointRows already fetch (GET /layers/{id}/pixel, GET
 * /projects/{id}/analyses/{id}/point). No new backend endpoint.
 *
 * Same native <dialog> pattern as ConfirmDialog.jsx - real modal semantics
 * (backdrop, focus trap, Esc-to-close) with no extra dependency.
 */
export default function AttributeTableDialog({ data, symbologyState, onClose }) {
  const ref = useRef(null);
  const open = !!data;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  function fieldsForCogRow({ layer, values, error }) {
    if (error) return [{ field: "Error", value: error, isError: true }];
    const symbology = symbologyState[layer.layer_id];
    const hasLegend = !!(layer.class_legend && Object.keys(layer.class_legend).length > 0);
    if (symbology?.mode === "classified" && hasLegend) {
      const raw = values[0];
      if (raw == null) return [{ field: "Class", value: "No data at this point." }];
      const key = String(Math.round(raw));
      const entry = layer.class_legend[key];
      return [
        { field: "Raw value", value: formatNumber(raw, 0) },
        { field: "Class", value: legendEntryLabel(key, entry), swatch: legendEntryColor(entry) },
      ];
    }
    if (values.every((v) => v == null)) return [{ field: "Value", value: "No data at this point." }];
    return values.map((v, i) => ({ field: `Band ${i + 1}`, value: v == null ? "—" : formatNumber(v, 4) }));
  }

  function fieldsForGeeRow({ error, data: geeData }) {
    if (error) return [{ field: "Error", value: error, isError: true }];
    if (geeData.outside_boundary) return [{ field: "Status", value: "Outside the project boundary." }];
    const fields = [];
    if (geeData.class_name) fields.push({ field: "Class", value: geeData.class_name, swatch: geeData.class_color });
    if (geeData.value != null) {
      fields.push({ field: "Value", value: `${formatNumber(geeData.value, 4)}${geeData.unit ? ` ${geeData.unit}` : ""}` });
    }
    if (geeData.detail) fields.push({ field: "Detail", value: geeData.detail });
    if (fields.length === 0) fields.push({ field: "Value", value: "No data at this point." });
    return fields;
  }

  return (
    <dialog
      ref={ref}
      className="confirm-dialog attribute-table-dialog"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
    >
      <h2 className="confirm-dialog-title">Attribute table</h2>
      {data ? (
        <p className="confirm-dialog-detail attribute-table-coords">
          {data.latlng.lat.toFixed(6)}, {data.latlng.lng.toFixed(6)}
        </p>
      ) : null}
      {data?.loading ? (
        <p className="confirm-dialog-detail">Reading attributes…</p>
      ) : !data || data.rows.length === 0 ? (
        <p className="confirm-dialog-detail">No active layer to inspect.</p>
      ) : (
        <div className="attribute-table-scroll">
          <table className="attribute-table">
            <thead>
              <tr>
                <th scope="col">Layer</th>
                <th scope="col">Field</th>
                <th scope="col">Value</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, rowIndex) => {
                const layerLabel = row.gee
                  ? row.analysisName
                  : `${row.layer.type}${row.layer.date_processed ? ` · ${row.layer.date_processed}` : ""}`;
                const fields = row.gee ? fieldsForGeeRow(row) : fieldsForCogRow(row);
                return fields.map((f, i) => (
                  <tr key={`${rowIndex}-${i}`} className={f.isError ? "attribute-table-row-error" : undefined}>
                    {i === 0 ? <th scope="row" rowSpan={fields.length}>{layerLabel}</th> : null}
                    <td>{f.field}</td>
                    <td>
                      {f.swatch ? (
                        <span className="legend-swatch" style={{ background: f.swatch }} aria-hidden="true" />
                      ) : null}
                      {f.value}
                    </td>
                  </tr>
                ));
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onClose}>
          Close
        </button>
      </div>
    </dialog>
  );
}
