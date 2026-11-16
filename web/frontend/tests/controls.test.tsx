import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useDownloads } from "../src/hooks/use-downloads";
import { api, ApiError, Job } from "../src/lib/api";
const fixture = vi.hoisted(() => ({
  snapshot: (_jobs: Job[]) => {
    void _jobs;
  },
  refresh: vi.fn().mockResolvedValue(undefined),
  close: vi.fn(),
}));
vi.mock("../src/lib/events", async (importOriginal) => {
  const original = await importOriginal<typeof import("../src/lib/events")>();
  return {
    ...original,
    connectDownloads: vi.fn((handlers: { snapshot: (jobs: Job[]) => void }) => {
      fixture.snapshot = handlers.snapshot;
      return { refresh: fixture.refresh, close: fixture.close };
    }),
  };
});
afterEach(() => {
  vi.restoreAllMocks();
  fixture.refresh.mockClear();
});
describe("server-driven lifecycle controls", () => {
  it.each(["pause", "resume", "cancel", "remove"] as const)(
    "sends %s and refreshes server state",
    async (action) => {
      vi.spyOn(api, "action").mockResolvedValue({ id: "job", removed: true });
      const { result } = renderHook(() => useDownloads());
      act(() => fixture.snapshot([{ id: "job", status: "Paused" }]));
      await act(() => result.current.action("job", action));
      expect(api.action).toHaveBeenCalledWith("job", action);
      expect(fixture.refresh).toHaveBeenCalledTimes(1);
      expect(result.current.jobs[0].status).toBe("Paused");
    },
  );
  it("recovers conflicts and suppresses concurrent duplicate actions", async () => {
    let reject: (e: ApiError) => void = () => {};
    vi.spyOn(api, "action").mockImplementation(
      () =>
        new Promise((_resolve, r) => {
          reject = r;
        }),
    );
    const { result } = renderHook(() => useDownloads());
    let first: Promise<void>;
    act(() => {
      first = result.current.action("job", "pause");
      void result.current.action("job", "pause");
    });
    expect(api.action).toHaveBeenCalledTimes(1);
    await act(async () => {
      reject(new ApiError(409));
      await first;
    });
    await waitFor(() => expect(fixture.refresh).toHaveBeenCalledTimes(1));
    expect(result.current.error).toContain("conflicts");
    expect(result.current.pending.job).toBe(false);
  });
});
