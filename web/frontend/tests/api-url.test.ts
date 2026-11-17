import { afterEach, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
});

it("uses same-origin REST and SSE in production even with a stale local URL", async () => {
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("NEXT_PUBLIC_DOWNLOADER_API_URL", "http://localhost:8000");
  vi.resetModules();
  const fetch = vi.fn().mockImplementation(async () => new Response("[]"));
  const source = { addEventListener: vi.fn(), close: vi.fn() };
  const EventSource = vi.fn(function () { return source; });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("EventSource", EventSource);
  const { api } = await import("../src/lib/api");
  await api.health();
  expect(fetch.mock.calls[0][0]).toBe("/api/health");
  const { connectDownloads } = await import("../src/lib/events");
  const connection = connectDownloads({ snapshot: vi.fn(), event: vi.fn(), status: vi.fn() });
  await vi.waitFor(() => expect(EventSource).toHaveBeenCalledWith("/api/events"));
  connection.close();
});

it("keeps the configurable local development API", async () => {
  vi.stubEnv("NODE_ENV", "development");
  vi.stubEnv("NEXT_PUBLIC_DOWNLOADER_API_URL", "http://localhost:8100/");
  vi.resetModules();
  const { API_URL } = await import("../src/lib/api");
  expect(API_URL).toBe("http://localhost:8100");
});
