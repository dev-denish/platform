import { useState } from "react";
import shpwrite from "@mapbox/shp-write";
import { apiFetch } from "../config.js";
import { toFeatureCollection } from "../lib/draw.js";

// Same convention as AddAdhocLayerDialog/UploadPage - a drawn shape goes
// through the exact same adhoc-layer upload + job pipeline as a real file, so
// it polls the same way.
const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

const HINTS = {
  point: "Click the map to place a point",
  line: "Click the map to start a line",
  polygon: "Click the map to start a shape (3+ points)",
};

const MIN_POINTS = { point: 1, line: 2, polygon: 3 };

/**
 * The floating panel for the toolbar's Draw tool - live vertex count/hint while
 * a shape is being drawn, then a small inline form to name it and either save
 * it into the project (as an ad-hoc vector layer, same pipeline as a real
 * upload) or download it as a zipped shapefile. Either action, both, or
 * neither; "New shape" resets to draw again, "Cancel" discards.
 *
 * Mode selection lives in the top toolbar's Draw menu, same split as
 * MeasureTools - this only shows what that selection is doing.
 */
export default function DrawTools({ mode, points, finished, onFinish, onReset, onCancel, projectId, onRefreshLayers }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(null); // "save" | "download"
  const [status, setStatus] = useState(null);

  if (mode === "none") return null;

  const safeName = name.trim() || "drawing";

  function reset() {
    setName("");
    setStatus(null);
    onReset();
  }

  async function handleSave() {
    setBusy("save");
    setStatus(null);
    try {
      const fc = toFeatureCollection(mode, points, safeName);
      const file = new File([JSON.stringify(fc)], "drawing.geojson", { type: "application/geo+json" });
      const body = new FormData();
      body.append("file", file);
      body.append("display_name", safeName);
      const accepted = await apiFetch(`/projects/${projectId}/adhoc-layers`, { method: "POST", body });
      let job = { status: "queued" };
      while (!TERMINAL_STATUSES.includes(job.status)) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        job = await apiFetch(`/jobs/${accepted.job_id}`);
      }
      if (job.status === "succeeded") {
        setStatus("Saved to project.");
        await onRefreshLayers?.();
      } else {
        setStatus("Could not save this shape.");
      }
    } catch (err) {
      setStatus(err.message ?? "Could not save this shape.");
    } finally {
      setBusy(null);
    }
  }

  async function handleDownload() {
    setBusy("download");
    setStatus(null);
    try {
      const blob = await shpwrite.zip(toFeatureCollection(mode, points, safeName), {
        outputType: "blob",
        // jszip's DEFLATE is documented as buggy by shp-write itself.
        compression: "STORE",
        types: { point: safeName, polyline: safeName, polygon: safeName },
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${safeName}.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatus(err.message ?? "Could not build the shapefile.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="draw-tools">
      {finished ? (
        <>
          <input
            className="field-input draw-tools-input"
            aria-label="Shape name"
            placeholder="Shape name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={!!busy}
          />
          {projectId ? (
            <button type="button" className="ghost-button" disabled={!!busy} onClick={handleSave}>
              {busy === "save" ? "Saving…" : "Save to project"}
            </button>
          ) : null}
          <button type="button" className="ghost-button" disabled={!!busy} onClick={handleDownload}>
            {busy === "download" ? "Building…" : "Download shapefile"}
          </button>
          <button type="button" className="ghost-button" disabled={!!busy} onClick={reset}>
            New shape
          </button>
          {status ? <span className="measure-result">{status}</span> : null}
        </>
      ) : (
        <>
          <span className="measure-result">
            {points.length === 0 ? HINTS[mode] : `${points.length} point${points.length === 1 ? "" : "s"}`}
          </span>
          {mode !== "point" ? (
            <button
              type="button"
              className="ghost-button"
              disabled={points.length < MIN_POINTS[mode]}
              onClick={onFinish}
            >
              Finish
            </button>
          ) : null}
        </>
      )}
      <button type="button" className="ghost-button" disabled={!!busy} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
