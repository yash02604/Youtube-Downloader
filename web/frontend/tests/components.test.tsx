import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AnalysisForm } from "../src/components/analysis-form";
import { DownloadRow } from "../src/components/download-row";
import { api, ApiError } from "../src/lib/api";
afterEach(() => vi.restoreAllMocks());
function submit() {
  fireEvent.change(screen.getByLabelText("YouTube URL"), {
    target: { value: "https://youtu.be/tRoYSagYP7U" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Analyze" }));
}
describe("analysis form", () => {
  it("shows loading, analysis preview, and submits selected backend options", async () => {
    let resolve: (value: {
      title: string;
      formats: [];
      resolutions: number[];
    }) => void = () => {};
    vi.spyOn(api, "analyze").mockImplementation(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    );
    vi.spyOn(api, "create").mockResolvedValue({ id: "new" });
    const created = vi.fn().mockResolvedValue(undefined);
    render(<AnalysisForm onCreated={created} />);
    submit();
    expect(screen.getByRole("button", { name: "Analyzing…" })).toBeDisabled();
    resolve({ title: "Mountain film", formats: [], resolutions: [2160, 1080] });
    await screen.findByText("Mountain film");
    fireEvent.change(screen.getByLabelText("Type"), {
      target: { value: "Audio Only" },
    });
    expect(screen.getByLabelText("Video quality")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Download$/ }));
    await waitFor(() => expect(created).toHaveBeenCalledWith("new"));
    expect(api.create).toHaveBeenCalledWith(
      expect.objectContaining({
        download_type: "Audio Only",
        video_quality: "Best Available",
      }),
    );
  });
  it.each([
    [409, "YouTube requires sign-in or verification for this video."],
    [400, "Enter a supported YouTube URL and check the download options."],
    [0, "Downloader service is not available."],
  ] as const)("shows safe analysis error %i", async (status, message) => {
    vi.spyOn(api, "analyze").mockRejectedValue(new ApiError(status));
    render(<AnalysisForm onCreated={vi.fn()} />);
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
  });
});
describe("download rows", () => {
  it("renders live progress, speed, ETA, and server-driven controls", () => {
    const action = vi.fn();
    const job = {
      id: "one",
      title: "Mountain film",
      status: "Downloading",
      progress: 50,
      downloaded: 1024,
      file_size: 2048,
      speed: 1024,
      eta: 18,
    };
    const { rerender } = render(
      <DownloadRow job={job} pending={false} onAction={action} />,
    );
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "50");
    expect(screen.getByText("1.0 KB/s")).toBeInTheDocument();
    expect(screen.getByText("ETA 0:18")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(action).toHaveBeenCalledWith("one", "pause");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(action).toHaveBeenCalledWith("one", "cancel");
    rerender(
      <DownloadRow
        job={{ ...job, status: "Paused" }}
        pending={false}
        onAction={action}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    expect(action).toHaveBeenCalledWith("one", "resume");
    expect(action).toHaveBeenCalledWith("one", "remove");
    expect(
      screen.queryByRole("button", { name: "Pause" }),
    ).not.toBeInTheDocument();
  });
  it("displays completion without offering file serving", () => {
    render(
      <DownloadRow
        job={{ id: "one", status: "Completed", progress: 100 }}
        pending={false}
        onAction={vi.fn()}
      />,
    );
    expect(screen.getByText("Completed")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Download file" }),
    ).not.toBeInTheDocument();
  });
});
