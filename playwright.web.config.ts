import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

const python = process.env.API_TEST_PYTHON || resolve("apps/backend/.venv/Scripts/python.exe");

/** DEV-009 GUI tests: the real Vite app against the real API; the PO model is the labelled FakeProvider. */
export default defineConfig({
  testDir: "./tests/web-browser",
  workers: 1,
  timeout: 45000,
  expect: { timeout: 10000 },
  use: { browserName: "chromium", baseURL: "http://127.0.0.1:19851", viewport: { width: 1366, height: 768 } },
  webServer: [
    {
      command: "npm run dev:web",
      url: "http://127.0.0.1:19851",
      reuseExistingServer: false,
      env: { WEB_PORT: "19851", VITE_API_BASE_URL: "http://127.0.0.1:19850" },
    },
    {
      command: `"${python}" tests/web-browser/server.py`,
      port: 19852,
      reuseExistingServer: false,
    },
  ],
});
