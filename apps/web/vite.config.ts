import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const host = env.WEB_HOST ?? "127.0.0.1";
  if (host !== "127.0.0.1") throw new Error("WEB_HOST must be 127.0.0.1 for the local control UI");
  function portValue(value: string, name: string) {
    const port = Number(value);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(`${name} must be an integer between 1 and 65535`);
    }
    return port;
  }
  const port = portValue(env.WEB_PORT ?? "5173", "WEB_PORT");
  const previewPort = portValue(env.WEB_PREVIEW_PORT ?? "5174", "WEB_PREVIEW_PORT");
  return {
    root: "apps/web",
    envDir: process.cwd(),
    plugins: [react()],
    server: { host, port, strictPort: true },
    preview: { host, port: previewPort, strictPort: true },
    build: { outDir: "../../dist", emptyOutDir: true },
  };
});
