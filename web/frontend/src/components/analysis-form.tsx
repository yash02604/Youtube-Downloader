"use client";
import Image from "next/image";
import { FormEvent, useRef, useState } from "react";
import {
  api,
  ApiError,
  audioQualities,
  DownloadInput,
  errorText,
  Metadata,
  videoQualities,
} from "../lib/api";
import { duration } from "../lib/format";
const defaults: Omit<DownloadInput, "url"> = {
  destination: ".",
  download_type: "Video",
  video_quality: "Best Available",
  audio_quality: "Best Original Audio",
  subtitle_languages: [],
  download_subtitles: false,
  embed_subtitles: false,
  save_thumbnail: false,
  embed_thumbnail: false,
  embed_metadata: false,
};
export function AnalysisForm({
  onCreated,
}: {
  onCreated: (id: string) => Promise<void>;
}) {
  const [url, setUrl] = useState(""),
    [metadata, setMetadata] = useState<Metadata | null>(null),
    [options, setOptions] = useState(defaults),
    [analyzing, setAnalyzing] = useState(false),
    [creating, setCreating] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const busy = useRef(false),
    submitLock = useRef(false);
  function option<K extends keyof typeof options>(
    key: K,
    value: (typeof options)[K],
  ) {
    setOptions((previous) => ({ ...previous, [key]: value }));
  }
  async function analyze(event: FormEvent) {
    event.preventDefault();
    if (busy.current || !url.trim()) return;
    busy.current = true;
    setAnalyzing(true);
    setError("");
    setMetadata(null);
    setNotice("");
    try {
      const result = await api.analyze(url.trim());
      setMetadata(result);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 409
          ? "YouTube requires sign-in or verification for this video."
          : errorText(err),
      );
    } finally {
      busy.current = false;
      setAnalyzing(false);
    }
  }
  async function download() {
    if (submitLock.current || !metadata) return;
    submitLock.current = true;
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const result = await api.create({
        ...options,
        url: url.trim(),
        filename: options.filename || undefined,
      });
      setNotice("Download added. Synchronizing status…");
      await onCreated(result.id);
      setNotice("Download added to your queue.");
    } catch (err) {
      setError(errorText(err));
    } finally {
      submitLock.current = false;
      setCreating(false);
    }
  }
  const languages = [
    ...new Set([
      ...(metadata?.subtitles || []),
      ...(metadata?.automatic_subtitles || []),
    ]),
  ].filter((value) => /^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$/.test(value));
  return (
    <section className="panel composer" aria-labelledby="new-download">
      <h2 id="new-download">New download</h2>
      <form onSubmit={analyze}>
        <label htmlFor="video-url">YouTube URL</label>
        <input
          id="video-url"
          type="url"
          required
          maxLength={2048}
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          disabled={analyzing || creating}
          onChange={(e) => {
            setUrl(e.target.value);
            setMetadata(null);
            setNotice("");
            setError("");
          }}
        />
        <button
          className="primary analyze"
          disabled={analyzing || creating || !url.trim()}
        >
          {analyzing ? "Analyzing…" : "Analyze"}
        </button>
      </form>
      {error && (
        <p className="message error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="message" role="status">
          {notice}
        </p>
      )}
      {metadata ? (
        <>
          <div className="preview">
            {metadata.thumbnail ? (
              <Image
                unoptimized
                width={640}
                height={360}
                src={metadata.thumbnail}
                alt="Video thumbnail"
                className="thumbnail"
                onError={(e) => {
                  e.currentTarget.style.display = "none";
                }}
              />
            ) : (
              <div
                className="thumbnail empty-thumbnail"
                aria-label="No thumbnail available"
              >
                ▷
              </div>
            )}
            <h3>{metadata.title || "Untitled video"}</h3>
            <p>
              {metadata.channel || metadata.uploader || "Unknown channel"}{" "}
              <span className="separator">·</span> {duration(metadata.duration)}
            </p>
            <p className="caption">
              {metadata.resolutions?.length
                ? metadata.resolutions.map((value) => `${value}p`).join(" · ")
                : "Resolution information unavailable"}
            </p>
          </div>
          <fieldset className="quality">
            <legend>Quality settings</legend>
            <label>
              Type
              <select
                value={options.download_type}
                onChange={(e) =>
                  option(
                    "download_type",
                    e.target.value as DownloadInput["download_type"],
                  )
                }
              >
                <option>Video</option>
                <option>Audio Only</option>
              </select>
            </label>
            <label>
              Video quality
              <select
                disabled={options.download_type === "Audio Only"}
                value={options.video_quality}
                onChange={(e) =>
                  option(
                    "video_quality",
                    e.target.value as DownloadInput["video_quality"],
                  )
                }
              >
                {videoQualities.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
            {options.video_quality === "Best MP4 Compatible" &&
              options.download_type === "Video" && (
                <p className="caption">
                  Prioritizes compatibility; quality may be lower.
                </p>
              )}
            <label>
              Audio quality
              <select
                value={options.audio_quality}
                onChange={(e) =>
                  option(
                    "audio_quality",
                    e.target.value as DownloadInput["audio_quality"],
                  )
                }
              >
                {audioQualities.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </label>
          </fieldset>
          <details className="options">
            <summary>More options</summary>
            <div className="options-body">
              <label>
                File name (optional)
                <input
                  value={options.filename || ""}
                  maxLength={120}
                  onChange={(e) => option("filename", e.target.value)}
                />
              </label>
              <label>
                Folder inside Downloads
                <input
                  value={options.destination}
                  maxLength={240}
                  placeholder="videos"
                  onChange={(e) => option("destination", e.target.value)}
                />
              </label>
              <p className="caption">
                Use a relative folder, such as courses/week-1.
              </p>
              {(
                [
                  "download_subtitles",
                  "embed_subtitles",
                  "save_thumbnail",
                  "embed_thumbnail",
                  "embed_metadata",
                ] as const
              ).map((key) => (
                <label className="check" key={key}>
                  <input
                    type="checkbox"
                    checked={options[key]}
                    onChange={(e) => option(key, e.target.checked)}
                  />
                  {key
                    .replaceAll("_", " ")
                    .replace(/^./, (c) => c.toUpperCase())}
                </label>
              ))}
              {(options.download_subtitles || options.embed_subtitles) && (
                <label>
                  Subtitle languages
                  <select
                    multiple
                    value={options.subtitle_languages}
                    onChange={(e) =>
                      option(
                        "subtitle_languages",
                        Array.from(
                          e.target.selectedOptions,
                          (option) => option.value,
                        ),
                      )
                    }
                  >
                    {languages.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                  </select>
                  <span className="caption">
                    {languages.length
                      ? "Choose one or more languages."
                      : "No supported subtitle languages were reported."}
                  </span>
                </label>
              )}
            </div>
          </details>
          <button
            className="primary download"
            type="button"
            disabled={
              creating ||
              ((options.download_subtitles || options.embed_subtitles) &&
                !options.subtitle_languages.length)
            }
            onClick={download}
          >
            {creating ? "Adding…" : "↓  Download"}
          </button>
        </>
      ) : (
        <div className="composer-empty">
          <span className="outline-arrow" aria-hidden="true">
            ↓
          </span>
          <h3>Start with a video link</h3>
          <p>
            Preview the video and choose your quality before adding it to the
            queue.
          </p>
          <span className="caption">
            YouTube videos · Shorts · Live · Embed links
          </span>
        </div>
      )}
    </section>
  );
}
