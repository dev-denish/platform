import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../config.js";
import ErrorBanner from "../components/ErrorBanner.jsx";
import Spinner from "../components/Spinner.jsx";
import { DATASET_TYPES } from "../lib/roles.js";
import { formatNumber } from "../lib/format.js";
import ClassLegendBuilder, { buildLegend } from "../components/ClassLegendBuilder.jsx";

const RASTER_EXTENSIONS = [".tif", ".tiff", ".img"];
// Wave: multi-format layers. ".zip" = a shapefile bundle (.shp/.shx/.dbf/
// .prj together, see backend's app/services/ingestion/vector.py) - a lone
// ".shp" can't be parsed without its sibling files.
const VECTOR_EXTENSIONS = [".geojson", ".json", ".kml", ".csv", ".zip"];
const ACCEPTED_EXTENSIONS = [...RASTER_EXTENSIONS, ...VECTOR_EXTENSIONS];

function fileExtension(file) {
  if (!file) return "";
  const i = file.name.lastIndexOf(".");
  return i === -1 ? "" : file.name.slice(i).toLowerCase();
}

/** Reads just the first line of a File client-side, for the CSV lat/lon
 * column picker below - a UX convenience only. The backend re-validates the
 * chosen columns against the REAL uploaded file's header before ingesting
 * (never trusts this client-side parse) - see datasets.py's upload endpoint. */
function readCsvHeader(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const firstLine = String(reader.result).split(/\r?\n/, 1)[0] ?? "";
      resolve(firstLine.split(",").map((c) => c.trim().replace(/^"|"$/g, "")));
    };
    // A header row is always well within the first slice of any real CSV -
    // no need to read the whole (possibly huge) file just for column names.
    reader.readAsText(file.slice(0, 8192));
  });
}

// Phase 2: POST /datasets/upload returns 202 + {job_id, status_url} immediately;
// the ingest result is discovered by polling GET /jobs/{id} until it reaches a
// terminal status.
const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // give up auto-polling after 5 minutes
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

const INITIAL = {
  file: null,
  project_name: "",
  region: "Unspecified",
  dataset_type: "LULC",
  source: "",
  classification_method: "",
  accuracy_score: "",
  date_processed: "",
  pixel_size_m: "10",
  class_legend_rows: [],
  lat_column: "",
  lon_column: "",
  // Wave: Reference Layer Library. When true, the project name/region below
  // are never sent - the backend always resolves the one shared library
  // project instead (see IngestMetadata.is_reference).
  is_reference: false,
};

const REFERENCE_LIBRARY_PLACEHOLDER = "Reference Layer Library";

export default function UploadPage() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(INITIAL);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState(null); // {job_id, status, result, error, ...} - merged from JobOut
  const [jobPollError, setJobPollError] = useState(null);
  const [timedOut, setTimedOut] = useState(false);
  const [pollGen, setPollGen] = useState(0); // bumped to force the poll loop to restart
  const pollStartRef = useRef(null);
  // Wave: multi-format layers. Column names from the selected CSV's own
  // header row, for the lat/lon dropdowns below - null while no CSV is
  // selected (or before it's been read yet).
  const [csvColumns, setCsvColumns] = useState(null);
  const [csvReadError, setCsvReadError] = useState(null);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function isVectorFile(file) {
    return VECTOR_EXTENSIONS.includes(fileExtension(file));
  }

  function isCsvFile(file) {
    return fileExtension(file) === ".csv";
  }

  async function selectFile(file) {
    setForm((f) => ({ ...f, file, lat_column: "", lon_column: "" }));
    setCsvColumns(null);
    setCsvReadError(null);
    if (!file || !isCsvFile(file)) return;
    try {
      const columns = await readCsvHeader(file);
      setCsvColumns(columns);
    } catch {
      setCsvReadError("Could not read this CSV's header row.");
    }
  }

  function resetToStart() {
    setForm(INITIAL);
    setJob(null);
    setJobPollError(null);
    setTimedOut(false);
    setStep(1);
  }

  function checkAgain() {
    pollStartRef.current = Date.now();
    setTimedOut(false);
    setPollGen((g) => g + 1);
  }

  // Poll GET /jobs/{id} until it reaches a terminal status, or give up after
  // POLL_TIMEOUT_MS. Self-chaining setTimeout (not setInterval) so a slow
  // response can't overlap the next poll.
  useEffect(() => {
    if (step !== 4 || !job || TERMINAL_STATUSES.includes(job.status)) return undefined;

    let cancelled = false;

    async function poll() {
      if (cancelled) return;
      if (Date.now() - pollStartRef.current > POLL_TIMEOUT_MS) {
        setTimedOut(true);
        return;
      }
      try {
        const latest = await apiFetch(`/jobs/${job.job_id}`);
        if (cancelled) return;
        setJobPollError(null);
        setJob((prev) => ({ ...prev, ...latest }));
        if (!TERMINAL_STATUSES.includes(latest.status)) {
          setTimeout(poll, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        setJobPollError(err.message ?? "Could not check job status.");
        setTimeout(poll, POLL_INTERVAL_MS);
      }
    }

    const timer = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, job?.job_id, pollGen]);

  function isSatellite() {
    return form.dataset_type === "Satellite / Raw Imagery";
  }

  function step1Valid() {
    if (!form.file) return false;
    if (!form.is_reference && form.project_name.trim().length === 0) return false;
    // Wave: multi-format layers - explicit lat/lon column selection is
    // required for a CSV upload, never guessed from header names (see
    // backend's IngestMetadata.lat_column/lon_column docstring).
    if (isCsvFile(form.file)) return !!form.lat_column && !!form.lon_column;
    return true;
  }

  // Mirrors the backend's real rule (app/api/v1/datasets.py): accuracy_score
  // is a classification-accuracy metric, so it's only REQUIRED when a
  // class_legend is supplied - there's no classification to be accurate about
  // for a raw, unclassified scene.
  function hasLegend() {
    return Object.keys(buildLegend(form.class_legend_rows)).length > 0;
  }

  function step2Valid() {
    if (!form.source.trim() || !form.date_processed) return false;
    const accRaw = form.accuracy_score.trim();
    if (!accRaw) return !hasLegend();
    const acc = Number(accRaw);
    return !Number.isNaN(acc) && acc >= 0 && acc <= 100;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      const legend = buildLegend(form.class_legend_rows);
      const body = new FormData();
      body.append("file", form.file);
      body.append("project_name", form.is_reference ? REFERENCE_LIBRARY_PLACEHOLDER : form.project_name);
      body.append("region", form.region || "Unspecified");
      body.append("dataset_type", form.dataset_type);
      body.append("is_reference", form.is_reference ? "true" : "false");
      body.append("source", form.source);
      body.append("classification_method", form.classification_method);
      if (form.accuracy_score.trim()) body.append("accuracy_score", form.accuracy_score);
      body.append("date_processed", form.date_processed);
      body.append("pixel_size_m", form.pixel_size_m || "10");
      if (Object.keys(legend).length > 0) body.append("class_legend", JSON.stringify(legend));
      if (isCsvFile(form.file)) {
        body.append("lat_column", form.lat_column);
        body.append("lon_column", form.lon_column);
      }

      // 202 + {job_id, status_url}: the ingest itself is now a background job -
      // stage the polling state and switch to the tracking view.
      const accepted = await apiFetch("/datasets/upload", { method: "POST", body });
      pollStartRef.current = Date.now();
      setJobPollError(null);
      setTimedOut(false);
      setJob({ job_id: accepted.job_id, status: "queued", result: null, error: null });
      setStep(4);
    } catch (err) {
      setError(err.message ?? "Upload failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (step === 4 && job) {
    const isTerminal = TERMINAL_STATUSES.includes(job.status);
    const isFailure = job.status === "failed" || job.status === "dead_letter";

    if (!isTerminal) {
      return (
        <div className="page">
          <header className="page-header">
            <div>
              <p className="page-eyebrow">Upload dataset</p>
              <h1 className="page-title">Processing ingest</h1>
            </div>
          </header>
          <section className="panel">
            <div className="full-screen-center">
              <Spinner
                label={
                  job.status === "running"
                    ? "Processing your dataset…"
                    : "Queued for processing…"
                }
              />
            </div>
            <ErrorBanner message={jobPollError} />
            {timedOut ? (
              <div className="form-actions">
                <span className="field-hint">
                  Still working - this is taking longer than expected. You can keep
                  waiting and check again, or come back later; the job keeps running
                  in the background.
                </span>
                <button type="button" className="primary-button" onClick={checkAgain}>
                  Check again
                </button>
              </div>
            ) : null}
          </section>
        </div>
      );
    }

    if (isFailure) {
      return (
        <div className="page">
          <header className="page-header">
            <div>
              <p className="page-eyebrow">Upload dataset</p>
              <h1 className="page-title">Ingest failed</h1>
            </div>
          </header>
          <section className="panel">
            <ErrorBanner
              message={
                job.error?.message ??
                (job.status === "dead_letter"
                  ? "The ingest failed after multiple attempts."
                  : "The ingest failed.")
              }
            />
            <div className="form-actions">
              <button type="button" className="primary-button" onClick={resetToStart}>
                Try again
              </button>
            </div>
          </section>
        </div>
      );
    }

    // succeeded
    const ingest = job.result;
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <p className="page-eyebrow">Upload dataset</p>
            <h1 className="page-title">Ingest complete</h1>
          </div>
        </header>
        <section className="panel">
          {ingest ? (
            <>
              <div className="stat-grid">
                <div className="stat-card">
                  <span className="stat-label">Total area</span>
                  <span className="stat-value">
                    {formatNumber(ingest.total_area_ha)} <span className="stat-unit">ha</span>
                  </span>
                </div>
                {ingest.class_stats
                  ? Object.entries(ingest.class_stats).map(([label, area]) => (
                      <div className="stat-card" key={label}>
                        <span className="stat-label">{label}</span>
                        <span className="stat-value">
                          {formatNumber(area)} <span className="stat-unit">ha</span>
                        </span>
                      </div>
                    ))
                  : null}
              </div>
              {/* No class_legend was supplied at upload, so there's no per-class
                  breakdown - this was an unclassified scene; show generic band
                  statistics instead. */}
              {!ingest.class_stats && ingest.band_stats ? (
                <dl className="review-list">
                  <ReviewRow label="Band min" value={formatNumber(ingest.band_stats.min)} />
                  <ReviewRow label="Band max" value={formatNumber(ingest.band_stats.max)} />
                  <ReviewRow label="Band mean" value={formatNumber(ingest.band_stats.mean)} />
                  <ReviewRow label="Band std. dev." value={formatNumber(ingest.band_stats.stddev)} />
                </dl>
              ) : null}
            </>
          ) : (
            <ErrorBanner message="The job succeeded but returned no result." />
          )}
          <div className="form-actions">
            {ingest?.project_id ? (
              <Link to={`/projects/${ingest.project_id}`} className="primary-button">
                View project →
              </Link>
            ) : null}
            <button type="button" className="ghost-button" onClick={resetToStart}>
              Ingest another dataset
            </button>
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <p className="page-eyebrow">Ingestion</p>
          <h1 className="page-title">Upload dataset</h1>
        </div>
      </header>

      <ol className="step-track">
        <StepTab n={1} label="File & project" active={step === 1} done={step > 1} />
        <StepTab n={2} label="Metadata" active={step === 2} done={step > 2} />
        <StepTab n={3} label="Review & submit" active={step === 3} done={false} />
      </ol>

      <section className="panel">
        {step === 1 ? (
          <div className="form-grid">
            <label className="field field-wide">
              <span className="field-label">File</span>
              <input
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="field-file"
                onChange={(e) => selectFile(e.target.files?.[0] ?? null)}
              />
              <span className="field-hint">
                Raster: {RASTER_EXTENSIONS.join(", ")} · Vector: {VECTOR_EXTENSIONS.join(", ")}{" "}
                (shapefile as a .zip bundle) · up to 2 GiB
              </span>
            </label>
            {isCsvFile(form.file) ? (
              <>
                <ErrorBanner message={csvReadError} />
                <label className="field">
                  <span className="field-label">Latitude column</span>
                  <select
                    className="field-input"
                    value={form.lat_column}
                    onChange={(e) => update("lat_column", e.target.value)}
                    disabled={!csvColumns}
                  >
                    <option value="">Select a column…</option>
                    {(csvColumns ?? []).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="field-label">Longitude column</span>
                  <select
                    className="field-input"
                    value={form.lon_column}
                    onChange={(e) => update("lon_column", e.target.value)}
                    disabled={!csvColumns}
                  >
                    <option value="">Select a column…</option>
                    {(csvColumns ?? []).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <span className="field-hint">
                    Every other row must be a valid coordinate here, or the upload is
                    rejected - never guessed from column names.
                  </span>
                </label>
              </>
            ) : null}
            <label className="field field-wide checkbox-field">
              <input
                type="checkbox"
                checked={form.is_reference}
                onChange={(e) => update("is_reference", e.target.checked)}
              />
              <span>Add as a shared reference layer (visible on every project, not just one)</span>
            </label>
            {!form.is_reference ? (
              <>
                <label className="field">
                  <span className="field-label">Project name</span>
                  <input
                    className="field-input"
                    value={form.project_name}
                    onChange={(e) => update("project_name", e.target.value)}
                    placeholder="e.g. Rimba Raya Corridor"
                  />
                  <span className="field-hint">Matched or created by exact name.</span>
                </label>
                <label className="field">
                  <span className="field-label">Region</span>
                  <input
                    className="field-input"
                    value={form.region}
                    onChange={(e) => update("region", e.target.value)}
                  />
                </label>
              </>
            ) : null}
            <div className="form-actions">
              <button type="button" className="primary-button" disabled={!step1Valid()} onClick={() => setStep(2)}>
                Continue →
              </button>
            </div>
          </div>
        ) : null}

        {step === 2 ? (
          <div className="form-grid">
            <label className="field">
              <span className="field-label">Dataset type</span>
              <select
                className="field-input"
                value={form.dataset_type}
                onChange={(e) => update("dataset_type", e.target.value)}
              >
                {DATASET_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Source</span>
              <input
                className="field-input"
                value={form.source}
                onChange={(e) => update("source", e.target.value)}
                placeholder="e.g. Sentinel-2 L2A"
              />
            </label>
            <label className="field">
              <span className="field-label">Classification method</span>
              <input
                className="field-input"
                value={form.classification_method}
                onChange={(e) => update("classification_method", e.target.value)}
                placeholder="e.g. Random forest"
              />
            </label>
            {!isSatellite() || hasLegend() ? (
              <label className="field">
                <span className="field-label">Accuracy score (%){hasLegend() ? "" : " (optional)"}</span>
                <input
                  type="number"
                  min="0"
                  max="100"
                  step="0.1"
                  className="field-input"
                  value={form.accuracy_score}
                  onChange={(e) => update("accuracy_score", e.target.value)}
                />
                <span className="field-hint">
                  Required only when a class legend is supplied below - there's no
                  classification to be accurate about otherwise.
                </span>
              </label>
            ) : null}
            <label className="field">
              <span className="field-label">Date processed</span>
              <input
                type="date"
                className="field-input"
                value={form.date_processed}
                onChange={(e) => update("date_processed", e.target.value)}
              />
            </label>
            {/* Wave: multi-format layers - pixel size and a class legend are
                raster concepts only; a vector upload has no raster grid or
                classified band values at all. */}
            {!isVectorFile(form.file) ? (
              <>
                <label className="field">
                  <span className="field-label">Pixel size (m)</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    className="field-input"
                    value={form.pixel_size_m}
                    onChange={(e) => update("pixel_size_m", e.target.value)}
                  />
                </label>
                <div className="field field-wide">
                  <span className="field-label">Class legend (optional)</span>
                  <ClassLegendBuilder
                    rows={form.class_legend_rows}
                    onChange={(rows) => update("class_legend_rows", rows)}
                    file={form.file}
                  />
                  <span className="field-hint">
                    {isSatellite()
                      ? "Satellite / Raw Imagery is usually unclassified - most uploads of this type should leave this blank."
                      : "Leave empty for raw, unclassified imagery. Only add classes if this is a classified product."}
                  </span>
                </div>
              </>
            ) : null}
            <div className="form-actions">
              <button type="button" className="ghost-button" onClick={() => setStep(1)}>
                ← Back
              </button>
              <button type="button" className="primary-button" disabled={!step2Valid()} onClick={() => setStep(3)}>
                Continue →
              </button>
            </div>
          </div>
        ) : null}

        {step === 3 ? (
          <div className="form-grid">
            <dl className="review-list">
              <ReviewRow label="File" value={form.file?.name} />
              {form.is_reference ? (
                <ReviewRow label="Scope" value="Shared reference layer (every project)" />
              ) : (
                <>
                  <ReviewRow label="Project" value={form.project_name} />
                  <ReviewRow label="Region" value={form.region} />
                </>
              )}
              <ReviewRow label="Type" value={form.dataset_type} />
              <ReviewRow label="Source" value={form.source} />
              <ReviewRow label="Classification method" value={form.classification_method || "—"} />
              <ReviewRow
                label="Accuracy"
                value={form.accuracy_score.trim() ? `${form.accuracy_score}%` : "—"}
              />
              <ReviewRow label="Date processed" value={form.date_processed} />
              <ReviewRow label="Pixel size" value={`${form.pixel_size_m} m`} />
            </dl>

            <ErrorBanner message={error} />

            <div className="form-actions">
              <button type="button" className="ghost-button" onClick={() => setStep(2)} disabled={submitting}>
                ← Back
              </button>
              <button type="button" className="primary-button" onClick={handleSubmit} disabled={submitting}>
                {submitting ? "Ingesting…" : "Submit for ingestion"}
              </button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function StepTab({ n, label, active, done }) {
  return (
    <li className={`step-tab ${active ? "step-tab-active" : ""} ${done ? "step-tab-done" : ""}`}>
      <span className="step-tab-number">{done ? "✓" : n}</span>
      {label}
    </li>
  );
}

function ReviewRow({ label, value }) {
  return (
    <div className="review-row">
      <dt>{label}</dt>
      <dd className="mono-cell">{value}</dd>
    </div>
  );
}
