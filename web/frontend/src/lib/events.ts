import { api, API_URL, DownloadEvent, Job } from "./api";
export type ConnectionStatus =
  "Connecting…" | "Service connected" | "Reconnecting…" | "Service unavailable";
export function applyEvent(job: Job, event: DownloadEvent): Job {
  return {
    ...job,
    status: event.status || job.status,
    stage: event.stage ?? job.stage,
    filename: event.filename || job.filename,
    downloaded: event.downloaded_bytes ?? job.downloaded,
    file_size: event.total_bytes ?? job.file_size,
    speed: event.speed ?? job.speed,
    eta: event.eta ?? job.eta,
    progress: event.progress ?? job.progress,
    error_category:
      event.error_category !== undefined
        ? event.error_category
        : job.error_category,
    error: event.error_message !== undefined ? event.error_message : job.error,
  };
}
export interface EventChannel {
  onopen: ((event: Event) => void) | null;
  onerror: ((event: Event) => void) | null;
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
}
export function connectDownloads(
  handlers: {
    snapshot: (jobs: Job[]) => void;
    event: (event: DownloadEvent) => boolean;
    status: (status: ConnectionStatus) => void;
  },
  deps = {
    api,
    source: () => new EventSource(API_URL + "/api/events") as EventChannel,
  },
) {
  let closed = false,
    source: EventChannel | null = null,
    timer: ReturnType<typeof setTimeout> | undefined,
    refreshing: Promise<void> | null = null,
    buffered: DownloadEvent[] = [],
    attempt = 0;
  const abort = new AbortController();
  function refresh(): Promise<void> {
    if (refreshing) return refreshing;
    let unknown = false;
    refreshing = deps.api
      .list(abort.signal)
      .then((jobs) => {
        if (!closed) {
          handlers.snapshot(jobs);
          const updates = buffered;
          buffered = [];
          updates.forEach((event) => {
            if (!handlers.event(event)) unknown = true;
          });
        }
      })
      .finally(() => {
        refreshing = null;
        if (unknown && !closed)
          void refresh().catch(() => handlers.status("Reconnecting…"));
      });
    return refreshing;
  }
  function later() {
    if (closed || timer) return;
    timer = setTimeout(
      () => {
        timer = undefined;
        void start();
      },
      Math.min(15000, 1000 * 2 ** Math.min(attempt++, 4)),
    );
  }
  async function start() {
    try {
      await deps.api.health(abort.signal);
      await refresh();
      if (closed) return;
      source = deps.source();
      source.onopen = () => {
        void refresh()
          .then(() => {
            if (!closed) {
              attempt = 0;
              handlers.status("Service connected");
            }
          })
          .catch(() => handlers.status("Reconnecting…"));
      };
      source.onerror = () => {
        if (!closed) handlers.status("Reconnecting…");
      };
      source.addEventListener("download", (message) => {
        try {
          const event = JSON.parse(message.data) as DownloadEvent;
          if (!event.download_id || typeof event.kind !== "string") return;
          if (refreshing) {
            if (buffered.length >= 128) {
              buffered = [];
              source?.close();
              later();
              return;
            }
            buffered.push(event);
          } else if (!handlers.event(event))
            void refresh().catch(() => handlers.status("Reconnecting…"));
        } catch {
          /* Ignore malformed event payloads; snapshots remain authoritative. */
        }
      });
      source.addEventListener("resync", () => {
        source?.close();
        source = null;
        handlers.status("Reconnecting…");
        void refresh().then(later).catch(later);
      });
    } catch {
      if (!closed) {
        handlers.status("Service unavailable");
        later();
      }
    }
  }
  void start();
  return {
    refresh,
    close() {
      closed = true;
      abort.abort();
      source?.close();
      if (timer) clearTimeout(timer);
    },
  };
}
