"use client";
import { memo } from "react";
import { Action, Job } from "../lib/api";
import { bytes, duration } from "../lib/format";
const pauseable = new Set([
  "Queued",
  "Downloading",
  "Fetching info",
  "Processing",
  "Merging",
  "Converting",
]);
const resumable = new Set(["Paused", "Error", "Interrupted"]);
const removable = new Set([
  "Paused",
  "Error",
  "Interrupted",
  "Cancelled",
  "Completed",
]);
const terminal = new Set(["Completed", "Cancelled"]);
export const DownloadRow = memo(function DownloadRow({
  job,
  pending,
  onAction,
}: {
  job: Job;
  pending: boolean;
  onAction: (id: string, action: Action) => void;
}) {
  const percent = Math.min(100, Math.max(0, job.progress || 0));
  return (
    <article className="job" aria-label={job.title || job.filename || job.id}>
      <div className="job-heading">
        <div className="file-icon" aria-hidden="true">
          {job.status === "Completed" ? "✓" : "↓"}
        </div>
        <div className="job-title">
          <h3>{job.title || job.filename || "Download"}</h3>
          <p>{job.quality || job.profile || "Quality pending"}</p>
        </div>
        <span
          className={`badge ${job.status === "Completed" ? "complete" : job.status === "Error" ? "failed" : ""}`}
        >
          {job.status}
        </span>
      </div>
      <div className="progress-line">
        <progress
          max={100}
          value={percent}
          aria-label={`${job.title || "Download"} progress`}
        />
        <span>{percent.toFixed(1)}%</span>
      </div>
      <div className="job-bottom">
        <div className="job-meta">
          <span>
            {bytes(job.downloaded)} /{" "}
            {job.file_size ? bytes(job.file_size) : "Unknown size"}
          </span>
          <span>{bytes(job.speed)}/s</span>
          <span>ETA {duration(job.eta)}</span>
          {job.stage && job.stage !== job.status && <span>{job.stage}</span>}
        </div>
        <div className="actions">
          {pauseable.has(job.status) && (
            <button
              disabled={pending}
              onClick={() => onAction(job.id, "pause")}
            >
              Pause
            </button>
          )}
          {resumable.has(job.status) && (
            <button
              disabled={pending}
              onClick={() => onAction(job.id, "resume")}
            >
              Resume
            </button>
          )}
          {!terminal.has(job.status) && job.status !== "Cancelling" && (
            <button
              disabled={pending}
              onClick={() => onAction(job.id, "cancel")}
            >
              Cancel
            </button>
          )}
          {removable.has(job.status) && (
            <button
              disabled={pending}
              onClick={() => onAction(job.id, "remove")}
            >
              Remove
            </button>
          )}
        </div>
      </div>
      {job.error_category && (
        <p className="message error">
          {job.error_category === "LOGIN_REQUIRED"
            ? "YouTube requires sign-in or verification for this video."
            : "This download could not be completed. You can retry with Resume."}
        </p>
      )}
      {job.playlist_title && (
        <p className="caption">
          {job.playlist_title} · {job.playlist_index} / {job.playlist_count}
        </p>
      )}
    </article>
  );
});
