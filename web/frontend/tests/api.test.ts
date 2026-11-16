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
});
