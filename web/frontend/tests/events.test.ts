import { afterEach, describe, it, expect, vi } from "vitest";
import { api, Job, DownloadEvent } from "../src/lib/api";
import { applyEvent, connectDownloads, EventChannel } from "../src/lib/events";
class Source implements EventChannel {
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  listeners = new Map<string, (e: MessageEvent) => void>();
  close = vi.fn();
  addEventListener(name: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(name, fn);
  }
  emit(name: string, data: unknown) {
    this.listeners.get(name)?.(
      new MessageEvent(name, { data: JSON.stringify(data) }),
    );
  }
}
afterEach(() => vi.useRealTimers());
function setup() {
  vi.useFakeTimers();
  const channel = new Source();
  const jobs: Job[] = [
    { id: "a", status: "Queued" },
    { id: "b", status: "Paused" },
  ];
  const handlers = {
    snapshot: vi.fn(),
    event: vi.fn((event: DownloadEvent) =>
      jobs.some((job) => job.id === event.download_id),
    ),
    status: vi.fn(),
  };
  const list = vi.fn().mockResolvedValue(jobs);
  const health = vi.fn().mockResolvedValue({ status: "ok" });
  const connection = connectDownloads(handlers, {
    api: { ...api, list, health },
    source: () => channel,
  });
  return { channel, jobs, handlers, list, connection };
}
describe("SSE synchronization", () => {
  it("loads preexisting snapshots before opening events", async () => {
    const s = setup();
    await vi.advanceTimersByTimeAsync(0);
    expect(s.handlers.snapshot).toHaveBeenCalledWith(s.jobs);
    expect(s.channel.listeners.has("download")).toBe(true);
    s.connection.close();
  });
  it("updates only the matching job and preserves status fields", () => {
    const old: Job = { id: "a", status: "Downloading", downloaded: 0 };
    const next = applyEvent(old, {
      download_id: "a",
      kind: "progress",
      downloaded_bytes: 50,
      progress: 50,
    });
    expect(next.downloaded).toBe(50);
    expect(next.status).toBe("Downloading");
    expect(old.downloaded).toBe(0);
  });
  it("clears server error state when a resumed event explicitly clears it", () => {
    const job: Job = {
      id: "a",
      status: "Error",
      error_category: "LOGIN_REQUIRED",
      error: "Verification",
    };
    const next = applyEvent(job, {
      download_id: "a",
      kind: "queued",
      status: "Queued",
      error_category: null,
      error_message: null,
    });
    expect(next.error_category).toBeNull();
    expect(next.error).toBeNull();
  });
  it("delivers events and recovers unknown IDs with a snapshot", async () => {
    const s = setup();
    await vi.advanceTimersByTimeAsync(0);
    s.channel.emit("download", {
      download_id: "a",
      kind: "progress",
      progress: 50,
    });
    expect(s.handlers.event).toHaveBeenCalledWith(
      expect.objectContaining({ download_id: "a" }),
    );
    s.channel.emit("download", { download_id: "unknown", kind: "queued" });
    await vi.advanceTimersByTimeAsync(0);
    expect(s.list).toHaveBeenCalledTimes(2);
    s.connection.close();
  });
  it("resync closes the stream, refreshes, and reconnects after bounded delay", async () => {
    const s = setup();
    await vi.advanceTimersByTimeAsync(0);
    s.channel.emit("resync", { refresh: "/api/downloads" });
    await vi.advanceTimersByTimeAsync(0);
    expect(s.channel.close).toHaveBeenCalled();
    expect(s.list).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(s.list).toHaveBeenCalledTimes(3);
    s.connection.close();
  });
  it("refreshes on reconnection and cleans up on disposal", async () => {
    const s = setup();
    await vi.advanceTimersByTimeAsync(0);
    s.channel.onerror?.(new Event("error"));
    expect(s.handlers.status).toHaveBeenCalledWith("Reconnecting…");
    s.channel.onopen?.(new Event("open"));
    await vi.advanceTimersByTimeAsync(0);
    expect(s.list).toHaveBeenCalledTimes(2);
    expect(s.handlers.status).toHaveBeenCalledWith("Service connected");
    s.connection.close();
    expect(s.channel.close).toHaveBeenCalled();
  });
});
