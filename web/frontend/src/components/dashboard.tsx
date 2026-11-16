"use client";
import { AnalysisForm } from "./analysis-form";
import { DownloadRow } from "./download-row";
import { useDownloads } from "../hooks/use-downloads";
export function Dashboard() {
  const { jobs, status, error, pending, action, addJob } = useDownloads();
  return (
    <>
      <header className="header">
        <div className="brand">
          <span aria-hidden="true">↓</span>
          <span>Video Download Manager</span>
        </div>
        <div className="header-right">
          <span className="connection" role="status">
            <i className={status === "Service connected" ? "online" : ""} />
            {status}
          </span>
          <details className="settings">
            <summary>Settings</summary>
            <div className="settings-popover">
              <strong>Local workspace</strong>
              <p>
                Single-video YouTube downloads. Quality and media options are
                set per download.
              </p>
              <p>
                Files stay on this computer. Browser file delivery is not
                available yet.
              </p>
            </div>
          </details>
        </div>
      </header>
      <main>
        <section className="intro">
          <h1>Your next download, simplified.</h1>
          <p>
            Analyze a video, choose your quality, and keep everything in view.
          </p>
        </section>
        <div className="workspace">
          <AnalysisForm onCreated={addJob} />
          <section
            className="panel downloads"
            aria-labelledby="downloads-heading"
          >
            <div className="section-heading">
              <div>
                <h2 id="downloads-heading">Downloads</h2>
                <p>Progress, without the guesswork.</p>
              </div>
              <span className="count">{jobs.length}</span>
            </div>
            {error && (
              <p role="alert" className="message error">
                {error}
              </p>
            )}
            {jobs.length ? (
              <div className="job-list">
                {jobs.map((job) => (
                  <DownloadRow
                    key={job.id}
                    job={job}
                    pending={!!pending[job.id]}
                    onAction={action}
                  />
                ))}
              </div>
            ) : (
              <div className="empty-state">
                <span aria-hidden="true">≡</span>
                <h3>Your queue is clear</h3>
                <p>
                  Added downloads will appear here with live progress and
                  controls.
                </p>
              </div>
            )}
          </section>
        </div>
        <footer>
          <span aria-hidden="true">▣</span> Local workspace · Files stay on this
          computer
        </footer>
      </main>
    </>
  );
}
