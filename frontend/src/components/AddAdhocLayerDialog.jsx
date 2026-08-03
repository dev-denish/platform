import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../config.js";
import ErrorBanner from "./ErrorBanner.jsx";
import Spinner from "./Spinner.jsx";

// Raster + vector/KML only - CSV needs lat/lon column selection, exactly the
// kind of extra required field this quick-add flow exists to skip (see the
// backend's matching allow-list in app/api/v1/adhoc_layers.py).
const ACCEPTED_EXTENSIONS = [".tif", ".tiff", ".img", ".geojson", ".json", ".kml", ".zip"];
const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

/**
 * Wave 3 (Added Layers): the "+ Add layer" quick-upload triggered from the
 * Layers panel, without leaving the map view. Same shape as
 * AddExternalLayerDialog (a native <dialog>, self-contained form state) but
 * posts a real file via FormData - closer to UploadPage's handleSubmit - and
 * then polls the returned job, same POLL_INTERVAL_MS/TERMINAL_STATUSES
 * convention as UploadPage, just inline here rather than a 3-step wizard:
 * only a file and a display name are ever asked for.
 */
export default function AddAdhocLayerDialog({ open, projectId, onCreated, onCancel }) {
  const ref = useRef(null);
  const [file, setFile] = useState(null);
  const [displayName, setDisplayName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      setFile(null);
      setDisplayName("");
      setJob(null);
      setError(null);
      dialog.showModal();
    }
    if (!open && dialog.open) dialog.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Poll GET /jobs/{id} until it reaches a terminal status - same
  // self-chaining setTimeout as UploadPage.jsx, so a slow response can't
  // overlap the next poll.
  useEffect(() => {
    if (!job || TERMINAL_STATUSES.includes(job.status)) return undefined;
    let cancelled = false;

    async function poll() {
      if (cancelled) return;
      try {
        const latest = await apiFetch(`/jobs/${job.job_id}`);
        if (cancelled) return;
        setJob((prev) => ({ ...prev, ...latest }));
        if (latest.status === "succeeded") {
          await onCreated();
        } else if (!TERMINAL_STATUSES.includes(latest.status)) {
          setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setError(err.message ?? "Could not check upload status.");
      }
    }

    const timer = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.job_id, job?.status]);

  function valid() {
    return !!file && displayName.trim().length > 0;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("display_name", displayName.trim());
      const accepted = await apiFetch(`/projects/${projectId}/adhoc-layers`, {
        method: "POST",
        body,
      });
      setJob({ job_id: accepted.job_id, status: "queued" });
    } catch (err) {
      setError(err.message ?? "Could not add this layer.");
    } finally {
      setSubmitting(false);
    }
  }

  const isProcessing = job && !TERMINAL_STATUSES.includes(job.status);
  const isFailure = job && (job.status === "failed" || job.status === "dead_letter");

  return (
    <dialog
      ref={ref}
      className="confirm-dialog"
      onCancel={onCancel}
      onClick={(e) => {
        if (e.target === ref.current) onCancel?.();
      }}
    >
      <h2 className="confirm-dialog-title">Add a layer</h2>
      <p className="confirm-dialog-detail">
        A quick raster or vector/KML addition to this project's map - no formal
        metadata, and it's kept out of Key Metrics and Landscape Evolution.
      </p>

      {isProcessing ? (
        <div className="full-screen-center">
          <Spinner label={job.status === "running" ? "Processing…" : "Queued…"} />
        </div>
      ) : (
        <div className="form-grid">
          <label className="field field-wide">
            <span className="field-label">File</span>
            <input
              type="file"
              accept={ACCEPTED_EXTENSIONS.join(",")}
              className="field-file"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              disabled={submitting}
            />
            <span className="field-hint">Raster (.tif/.img) or vector (.geojson/.kml/shapefile .zip).</span>
          </label>
          <label className="field field-wide">
            <span className="field-label">Display name</span>
            <input
              className="field-input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="e.g. Field boundary sketch"
              disabled={submitting}
            />
          </label>
        </div>
      )}

      <ErrorBanner message={error} />
      <div className="form-actions">
        <button type="button" className="ghost-button" onClick={onCancel} disabled={submitting || isProcessing}>
          {isFailure ? "Close" : "Cancel"}
        </button>
        {!isProcessing ? (
          <button
            type="button"
            className="primary-button"
            disabled={submitting || !valid()}
            onClick={handleSubmit}
          >
            {submitting ? "Adding…" : isFailure ? "Try again" : "Add layer"}
          </button>
        ) : null}
      </div>
    </dialog>
  );
}
