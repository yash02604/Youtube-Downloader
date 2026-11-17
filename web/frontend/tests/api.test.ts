import { afterEach, describe, it, expect, vi } from "vitest";
import { api, ApiError, DownloadInput } from "../src/lib/api";
afterEach(() => vi.unstubAllGlobals());
const input: DownloadInput = {
  url: "https://youtu.be/tRoYSagYP7U",
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
describe("public API helpers", () => {
  it.each([
    ["health", "/api/health", "GET"],
    ["list", "/api/downloads", "GET"],
  ] as const)("%s uses public endpoint", async (method, path, verb) => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    await api[method]();
    expect(fetch.mock.calls[0][0]).toContain(path);
    expect(fetch.mock.calls[0][1].method).toBe(verb);
  });
  it("analysis and creation send controlled JSON", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    await api.analyze(input.url);
    await api.create(input);
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ url: input.url });
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(input);
  });
  it.each(["pause", "resume", "cancel", "remove"] as const)(
    "%s maps lifecycle request",
    async (action) => {
      const fetch = vi.fn().mockImplementation(async () => new Response("{}"));
      vi.stubGlobal("fetch", fetch);
      await api.action("job", action);
      expect(fetch.mock.calls[0][1].method).toBe(
        action === "remove" ? "DELETE" : "POST",
      );
      expect(fetch.mock.calls[0][0]).toContain(
        action === "remove" ? "/job" : "/job/" + action,
      );
    },
  );
  it("does not expose raw server errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response("C:/secret token=123", { status: 500 }),
        ),
    );
    await expect(api.health()).rejects.toThrow(
      "The request could not be completed.",
    );
  });
  it("maps network failures to a safe availability error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("raw secret")));
    await expect(api.health()).rejects.toEqual(new ApiError(0));
  });
  it.each([
    ["LOGIN_REQUIRED", 409, "YouTube requires sign-in or verification for this video."],
    ["PO_TOKEN_REQUIRED", 422, "YouTube requires additional playback verification on this server."],
    ["PO_PROVIDER_UNAVAILABLE", 503, "The server's playback verification provider is unavailable."],
    ["PO_PROVIDER_INITIALIZATION_FAILED", 503, "The server's playback verification provider could not start."],
    ["PO_TOKEN_GENERATION_FAILED", 502, "YouTube playback verification could not be completed."],
    ["JS_RUNTIME_UNAVAILABLE", 503, "The server's YouTube JavaScript runtime is unavailable."],
    ["EJS_UNAVAILABLE", 503, "The server's YouTube challenge solver is unavailable."],
    ["UPSTREAM_REJECTED", 502, "YouTube rejected the video analysis request."],
    ["ANALYSIS_FAILED", 500, "The video could not be analyzed."],
    ["LOGIN_REQUIRED", 502, "YouTube requires sign-in or verification for this video."],
  ])("maps explicit analysis code %s at HTTP %i safely", async (code, status, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ code, message: "C:/secret token=123" }), { status: Number(status) },
    )));
    await expect(api.analyze(input.url)).rejects.toMatchObject({
      status, code, message,
    });
  });
  it.each([
    "C:/secret token=123",
    JSON.stringify({ code: "UNKNOWN", message: "C:/secret token=123" }),
    JSON.stringify({ detail: { code: "LOGIN_REQUIRED", message: "raw secret" } }),
    JSON.stringify({ message: "LOGIN_REQUIRED" }),
    "null",
    JSON.stringify({ code: "toString", message: "raw secret" }),
  ])("keeps unrecognized analysis conflict bodies generic: %s", async (body) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 409 })));
    await expect(api.analyze(input.url)).rejects.toMatchObject({
      status: 409,
      message: "This request conflicts with the current download state.",
    });
  });
  it.each([
    "LOGIN_REQUIRED", "PO_PROVIDER_UNAVAILABLE",
    "PO_PROVIDER_INITIALIZATION_FAILED", "PO_TOKEN_GENERATION_FAILED",
  ])("keeps lifecycle conflicts generic even with analysis code %s", async (code) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ code, message: "raw secret" }), { status: 409 },
    )));
    await expect(api.action("job", "pause")).rejects.toMatchObject({
      status: 409,
      message: "This request conflicts with the current download state.",
    });
  });
});
