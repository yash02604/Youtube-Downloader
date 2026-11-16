import { test, expect } from "@playwright/test";
test("local dashboard lifecycle, SSE, resync and responsive layout", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto("/");
  await expect(page).toHaveTitle("Video Download Manager");
  await expect(
    page.getByText("Service connected", { exact: true }),
  ).toBeVisible();
  const job = page.getByRole("article", {
    name: "A walk through the mountains",
  });
  await expect(job.getByText("25.0%")).toBeVisible();
  await request.post("http://localhost:8000/__test__/progress");
  await expect(job.getByText("62.0%")).toBeVisible();
  await job.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(job.getByText("Paused", { exact: true })).toBeVisible();
  await job.getByRole("button", { name: "Resume", exact: true }).click();
  await expect(job.getByText("Downloading", { exact: true })).toBeVisible();
  await request.post("http://localhost:8000/__test__/resync");
  await expect(job.getByText("80.0%")).toBeVisible();
  await expect(
    page.getByText("Service connected", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("YouTube URL").fill("https://youtu.be/tRoYSagYP7U");
  await page.getByRole("button", { name: "Analyze", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "A walk through the mountains" }).first(),
  ).toBeVisible();
  await expect(page.getByLabel("Video quality")).toBeVisible();
  await page.screenshot({
    path:
      process.env.FRONTEND_DESKTOP_SCREENSHOT ||
      "../../.test-tmp/frontend-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: /Download$/ }).click();
  await expect(page.getByText("Download added to your queue.")).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(2);
  await job
    .first()
    .getByRole("button", { name: "Cancel", exact: true })
    .click();
  await expect(
    job.first().getByText("Cancelled", { exact: true }),
  ).toBeVisible();
  await job
    .first()
    .getByRole("button", { name: "Remove", exact: true })
    .click();
  await expect(page.getByRole("article")).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Analyze", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path:
      process.env.FRONTEND_MOBILE_SCREENSHOT ||
      "../../.test-tmp/frontend-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
