import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const python = process.env.API_TEST_PYTHON || resolve("apps/backend/.venv/Scripts/python.exe");

export default defineConfig({
  testDir: "./tests/api-browser",
  workers: 1,
  timeout: 30000,
  use: { browserName: "chromium", baseURL: "http://127.0.0.1:19842" },
  webServer: {
    command: `node tests/api-browser/build-client.mjs && "${python}" tests/api-browser/server.py`,
    url: "http://127.0.0.1:19841/health",
    reuseExistingServer: false,
  },
});
