import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../config.js";
import ErrorBanner from "../components/ErrorBanner.jsx";
import Spinner from "../components/Spinner.jsx";
import { DATASET_TYPES } from "../lib/roles.js";
import { formatNumber } from "../lib/format.js";

const ACCEPTED_EXTENSIONS = [".tif", ".tiff", ".img"];

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
  class_legend: "",
};

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

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
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
    return form.file && form.project_name.trim().length > 0;
  }

  // Mirrors the backend's real rule (app/api/v1/datasets.py): accuracy_score
  // is a classification-accuracy metric, so it's only REQUIRED when a
  // class_legend is supplied - there's no classification to be accurate about
  // for a raw, unclassified scene. If it IS provided, it must still be 0-100.
  function hasLegend() {
    const raw = form.class_legend.trim();
    if (!raw) return false;
    try {
      const parsed = JSON.parse(raw);
      return !!parsed && typeof parsed === "object" && Object.keys(parsed).length > 0;
    } catch {
      return false;
    }
  }

  function step2Valid() {
    if (!form.source.trim() || !form.date_processed) return false;
    const legendRaw = form.class_legend.trim();
    if (legendRaw) {
      try {
        JSON.parse(legendRaw);
      } catch {
        return false;
      }
    }
    const accRaw = form.accuracy_score.trim();
    if (!accRaw) return !hasLegend();
    const acc = Number(accRaw);
    return !Number.isNaN(acc) && acc >= 0 && acc <= 100;
  }

  async function handleSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      if (form.class_legend.trim()) {
        JSON.parse(form.class_legend); // validate before sending
      }
      const body = new FormData();
      body.append("file", form.file);
      body.append("project_name", form.project_name);
      body.append("region", form.region || "Unspecified");
      body.append("dataset_type", form.dataset_type);
      body.append("source", form.source);
      body.append("classification_method", form.classification_method);
      if (form.accuracy_score.trim()) body.append("accuracy_score", form.accuracy_score);
      body.append("date_processed", form.date_processed);
      body.append("pixel_size_m", form.pixel_size_m || "10");
      if (form.class_legend.trim()) body.append("class_legend", form.class_legend);

      // 202 + {job_id, status_url}: the ingest itself is now a background job -
      // stage the polling state and switch to the tracking view.
      const accepted = await apiFetch("/datasets/upload", { method: "POST", body });
      pollStartRef.current = Date.now();
      setJobPollError(null);
      setTimedOut(false);
      setJob({ job_id: accepted.job_id, status: "queued", result: null, error: null });
      setStep(4);
    } catch (err) {
      setError(
        err.message?.includes("JSON")
          ? "Class legend must be valid JSON, e.g. {\"1\": \"Forest\", \"2\": \"Water\"}."
          : err.message ?? "Upload failed."
      );
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
              <span className="field-label">Raster file</span>
              <input
                type="file"
                accept={ACCEPTED_EXTENSIONS.join(",")}
                className="field-file"
                onChange={(e) => update("file", e.target.files?.[0] ?? null)}
              />
              <span className="field-hint">Accepted: {ACCEPTED_EXTENSIONS.join(", ")} · up to 2 GiB</span>
            </label>
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
            <label className="field field-wide">
              <span className="field-label">Class legend (optional JSON)</span>
              <textarea
                className="field-input field-textarea"
                value={form.class_legend}
                onChange={(e) => update("class_legend", e.target.value)}
                placeholder={
                  isSatellite()
                    ? "Leave blank for raw, unclassified imagery. Only add a legend if this is a classified product, e.g. {\"1\": \"Forest\", \"2\": \"Water\"}."
                    : '{"1": "Forest", "2": "Water"}'
                }
                rows={3}
              />
              {isSatellite() ? (
                <span className="field-hint">
                  Satellite / Raw Imagery is usually unclassified - most uploads of
                  this type should leave this blank.
                </span>
              ) : null}
            </label>
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
              <ReviewRow label="Project" value={form.project_name} />
              <ReviewRow label="Region" value={form.region} />
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
