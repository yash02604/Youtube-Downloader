export const videoQualities = [
  "Best Available",
  "Best MP4 Compatible",
  "2160p / 4K",
  "1440p / 2K",
  "1080p",
  "720p",
  "480p",
  "360p",
] as const;
export const audioQualities = [
  "Best Original Audio",
  "MP3 High Quality",
] as const;
export type Action = "pause" | "resume" | "cancel" | "remove";
export interface Metadata {
  title: string | null;
  channel?: string | null;
  uploader?: string | null;
  duration?: number | null;
  thumbnail?: string | null;
  resolutions?: number[] | null;
  subtitles?: string[] | null;
  automatic_subtitles?: string[] | null;
  formats: {
    format_id?: string | null;
    height?: number | null;
    fps?: number | null;
    vcodec?: string | null;
    acodec?: string | null;
    ext?: string | null;
  }[];
}
export interface Job {
  id: string;
  title?: string | null;
  filename?: string | null;
  status: string;
  stage?: string | null;
  profile?: string | null;
  quality?: string | null;
  progress?: number | null;
  downloaded?: number | null;
  file_size?: number | null;
  speed?: number | null;
  eta?: number | null;
  error_category?: string | null;
  error?: string | null;
  playlist_title?: string | null;
  playlist_index?: number | null;
  playlist_count?: number | null;
}
export interface DownloadInput {
  url: string;
  filename?: string;
  destination: string;
  download_type: "Video" | "Audio Only";
  video_quality: (typeof videoQualities)[number];
  audio_quality: (typeof audioQualities)[number];
  subtitle_languages: string[];
  download_subtitles: boolean;
  embed_subtitles: boolean;
  save_thumbnail: boolean;
  embed_thumbnail: boolean;
  embed_metadata: boolean;
}
export interface DownloadEvent {
  download_id: string;
  status?: string;
  kind: string;
  stage?: string | null;
  filename?: string | null;
  downloaded_bytes?: number;
  total_bytes?: number;
  speed?: number;
  eta?: number;
  progress?: number;
  error_category?: string | null;
  error_message?: string | null;
}
export const API_URL = (
  process.env.NODE_ENV === "production"
    ? ""
    : process.env.NEXT_PUBLIC_DOWNLOADER_API_URL || "http://localhost:8000"
).replace(/\/$/, "");
export class ApiError extends Error {
  constructor(public status: number) {
    super(
      status === 400
        ? "Enter a supported YouTube URL and check the download options."
        : status === 404
          ? "The video or download is not available."
          : status === 409
            ? "This request conflicts with the current download state."
            : status === 503 || status === 0
              ? "Downloader service is not available."
              : "The request could not be completed.",
    );
  }
}
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(API_URL + path, {
      method,
      cache: "no-store",
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(120000)])
        : AbortSignal.timeout(120000),
    });
  } catch {
    throw new ApiError(0);
  }
  if (!response.ok) throw new ApiError(response.status);
  return response.json();
}
export const api = {
  health: (signal?: AbortSignal) =>
    request<{ status: string }>("/api/health", "GET", undefined, signal),
  analyze: (url: string, signal?: AbortSignal) =>
    request<Metadata>("/api/analyze", "POST", { url }, signal),
  list: (signal?: AbortSignal) =>
    request<Job[]>("/api/downloads", "GET", undefined, signal),
  get: (id: string) => request<Job>("/api/downloads/" + encodeURIComponent(id)),
  create: (input: DownloadInput) =>
    request<{ id: string }>("/api/downloads", "POST", input),
  action: (id: string, action: Action) =>
    request<Job | { id: string; removed: boolean }>(
      "/api/downloads/" +
        encodeURIComponent(id) +
        (action === "remove" ? "" : "/" + action),
      action === "remove" ? "DELETE" : "POST",
    ),
};
export function errorText(error: unknown) {
  return error instanceof ApiError
    ? error.message
    : "The request could not be completed.";
}
