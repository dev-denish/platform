import { useEffect, useState } from "react";
import { Download, FileText } from "lucide-react";
import { API_BASE, apiFetch, getToken } from "../../config.js";
import { formatDate } from "../../lib/format.js";
import EmptyState from "../EmptyState.jsx";
import ErrorBanner from "../ErrorBanner.jsx";
import Spinner from "../Spinner.jsx";

const POLL_INTERVAL_MS = 2000;
const TERMINAL_STATUSES = ["succeeded", "failed", "dead_letter"];

/** PDF report generation (Wave: PDF report). Options are the project's
 * already-computed analyses ONLY (GET .../report/options never offers one
 * with no real data, mirroring the analysis catalog's own "in-development
 * gets an honest empty state, never fake data" rule) - selecting some and
 * generating always goes through the async job/poll pattern UploadPage.jsx
 * already established, since every section needs its own fresh GEE map-tile
 * fetch on top of chart/PDF rendering (see backend's report_service.py). */
export default function ReportGenerator({ projectId }) {
  const [options, setOptions] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(() => new Set());
  const [submitting, setSubmitting] = useState(false);
  const [job, setJob] = useState(null); // {job_id, status, result, error}

  useEffect(() => {
    let cancelled = false;
    apiFetch(`/projects/${projectId}/report/options`)
      .then((data) => {
        if (!cancelled) setOptions(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message ?? "Could not load available analyses.");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Self-chaining setTimeout (not setInterval), same convention as
  // UploadPage.jsx's own ingest-job poller - a slow response can't overlap
  // the next poll.
  useEffect(() => {
    if (!job || TERMINAL_STATUSES.includes(job.status)) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      if (cancelled) return;
      try {
        const latest = await apiFetch(`/jobs/${job.job_id}`);
        if (!cancelled) setJob((prev) => ({ ...prev, ...latest }));
      } catch (err) {
        if (!cancelled) setError(err.message ?? "Could not check report status.");
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [job]);

  function toggle(analysisId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(analysisId)) next.delete(analysisId);
      else next.add(analysisId);
      return next;
    });
  }

  async function handleGenerate() {
    setSubmitting(true);
    setError(null);
    try {
      const accepted = await apiFetch(`/projects/${projectId}/report`, {
        method: "POST",
        body: JSON.stringify({ analysis_ids: [...selected] }),
      });
      setJob({ job_id: accepted.job_id, status: "queued" });
    } catch (err) {
      setError(err.message ?? "Failed to start report generation.");
    } finally {
      setSubmitting(false);
    }
  }

  function resetToStart() {
    setJob(null);
    setError(null);
  }

  /** Authenticated file download: a plain <a href> can't carry the Bearer
   * token, so this fetches the PDF as a blob and triggers the download via a
   * throwaway object URL - the standard pattern for an auth-gated binary
   * download (this app's only other export, Excel, is generated entirely
   * client-side and needs no auth header at all). */
  async function handleDownload() {
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/reports/${job.job_id}/download`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      });
      if (!res.ok) throw new Error(`Download failed (${res.status}).`);
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") ?? "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = match ? match[1] : "report.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError(err.message ?? "Failed to download the report.");
    }
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">PDF report</h2>
      </div>
      <ErrorBanner message={error} />
      {!options ? (
        <Spinner label="Loading available analyses…" />
      ) : options.analyses.length === 0 ? (
        <EmptyState
          title="No computed analyses yet"
          description="Run an analysis in the Analyses panel first, then generate a report from its results."
        />
      ) : (
        <>
          <p className="field-hint">Select which computed analyses to include in the report:</p>
          <div className="report-checklist">
            {options.analyses.map((a) => (
              <label key={a.analysis_id} className="field field-wide checkbox-field">
                <input
                  type="checkbox"
                  checked={selected.has(a.analysis_id)}
                  onChange={() => toggle(a.analysis_id)}
                  disabled={!!job && !TERMINAL_STATUSES.includes(job.status)}
                />
                <span>
                  {a.name}
                  {a.is_multi_year ? " (full year-series trend)" : ""} — computed{" "}
                  {formatDate(a.computed_at)}
                </span>
              </label>
            ))}
          </div>

          {!job ? (
            <div className="form-actions">
              <button
                type="button"
                className="primary-button"
                disabled={selected.size === 0 || submitting}
                onClick={handleGenerate}
              >
                <FileText size={14} strokeWidth={2} className="icon" aria-hidden="true" />
                {submitting ? "Starting…" : "Generate report"}
              </button>
            </div>
          ) : job.status === "succeeded" ? (
            <div className="form-actions">
              <button type="button" className="primary-button" onClick={handleDownload}>
                <Download size={14} strokeWidth={2} className="icon" aria-hidden="true" />
                Download report
              </button>
              <button type="button" className="ghost-button" onClick={resetToStart}>
                Generate another
              </button>
            </div>
          ) : job.status === "failed" || job.status === "dead_letter" ? (
            <>
              <ErrorBanner
                message={job.error?.message ?? "Report generation failed."}
              />
              <div className="form-actions">
                <button type="button" className="ghost-button" onClick={resetToStart}>
                  Try again
                </button>
              </div>
            </>
          ) : (
            <Spinner
              label={
                job.status === "running"
                  ? "Generating report… this can take about a minute."
                  : "Queued for generation…"
              }
            />
          )}
        </>
      )}
    </section>
  );
}
