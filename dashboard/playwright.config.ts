import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Against a running Jarvis (the Docker demo in CI): E2E_BASE_URL=http://localhost:8080 npm run e2e.
// Otherwise against the gateway over its in-memory Jarvis, which serves the built dashboard (dist/).
const external = process.env.E2E_BASE_URL;
const port = 8098;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // one robot: the tests change its state (mode, faults) in turn
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: external ?? `http://127.0.0.1:${port}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: process.env.E2E_VIDEO ? "on" : "off",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
  ],
  webServer: external
    ? undefined
    : {
        command: [
          "python3 -m jarvis_gateway.cli --fake --scenario --anonymous-role admin --dashboard dist",
          `--port ${port} --data-dir ${mkdtempSync(join(tmpdir(), "jarvis-e2e-"))}`,
        ].join(" "),
        env: { PYTHONPATH: "../gateway/jarvis_gateway:../lake/jarvis_lake" },
        url: `http://127.0.0.1:${port}/api/v1/health`,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
