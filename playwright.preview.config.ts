import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

/**
 * DEV-011 preview tests: real Vite app, real API, real PreviewService with Docker (needs Linux or WSL + the pinned
 * node image). On Windows the backend fixture runs inside WSL; set PREVIEW_TEST_PYTHON to a Linux interpreter.
 */
const windows = process.platform === "win32";
const python = process.env.PREVIEW_TEST_PYTHON || (windows ? "/root/aiagent-dev002-venv/bin/python" : resolve("apps/backend/.venv/bin/python"));
const backend = windows
  ? `wsl.exe --cd "${process.cwd()}" -e ${python} tests/web-browser/preview_server.py`
  : `"${python}" tests/web-browser/preview_server.py`;

export default defineConfig({
  testDir: "./tests/web-browser",
  testMatch: /preview\.spec\.ts/,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  use: { browserName: "chromium", baseURL: "http://127.0.0.1:19861", viewport: { width: 1366, height: 768 } },
  webServer: [
    {
      command: "npm run dev:web",
      url: "http://127.0.0.1:19861",
      reuseExistingServer: false,
      env: { WEB_PORT: "19861", VITE_API_BASE_URL: "http://127.0.0.1:19860" },
    },
    { command: backend, url: "http://127.0.0.1:19862/ready", reuseExistingServer: false, timeout: 120000 },
  ],
});
