import { defineConfig } from "@playwright/test";

/**
 * E2E harness (issue #128): drives the real dev stack — uvicorn
 * backend on :8099 plus the Vite dev server on :5173 (its `/api`
 * proxy targets :8099). The backend data dir is a disposable folder
 * under `e2e/`; specs seed state through the public REST API.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env["CI"] ? 1 : 0,
  use: { baseURL: "http://localhost:5173" },
  webServer: [
    {
      command: "python -m uvicorn app.main:app --port 8099",
      cwd: "../backend",
      port: 8099,
      env: { FF_DATA_DIR: "../frontend/e2e/.data" },
      reuseExistingServer: !process.env["CI"],
      timeout: 30_000,
    },
    {
      command: "npm run dev -- --port 5173 --strictPort",
      port: 5173,
      reuseExistingServer: !process.env["CI"],
      timeout: 30_000,
    },
  ],
});
