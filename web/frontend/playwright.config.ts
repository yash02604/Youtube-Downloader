import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/browser",
  workers: 1,
  use: {
    baseURL: "http://localhost:3000",
    channel: process.env.DOWNLOADER_BROWSER_CHANNEL || "msedge",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: [
    {
      command: process.env.FRONTEND_SERVER_COMMAND || "npm run dev",
      url: "http://localhost:3000",
      reuseExistingServer: false,
      timeout: 120000,
    },
    {
      command: `"${process.env.DOWNLOADER_TEST_PYTHON || "python"}" "../../tests/fixtures/web_api.py"`,
      url: "http://localhost:8000/api/health",
      reuseExistingServer: false,
      timeout: 30000,
    },
  ],
});
