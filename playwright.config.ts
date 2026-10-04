import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/smoke",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:19832", browserName: "chromium" },
  webServer: {
    command: "npm run dev:web",
    url: "http://127.0.0.1:19832",
    reuseExistingServer: false,
    env: { WEB_PORT: "19832", VITE_API_BASE_URL: "http://127.0.0.1:19831/" },
  },
});
