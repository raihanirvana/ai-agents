import { useEffect, useState } from "react";

type HealthState = "checking" | "connected" | "unavailable";
const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");

export default function App() {
  const [health, setHealth] = useState<HealthState>("checking");

  useEffect(() => {
    let active = true;
    let poll: number | undefined;
    let controller: AbortController | undefined;
    let timeout: number | undefined;

    async function checkHealth() {
      controller = new AbortController();
      timeout = window.setTimeout(() => controller?.abort(), 3000);
      try {
        const response = await fetch(`${apiBaseUrl}/health`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) throw new Error("Health request failed");
        const body: unknown = await response.json();
        const connected = body !== null && typeof body === "object" &&
          "status" in body && body.status === "ok";
        if (active) setHealth(connected ? "connected" : "unavailable");
      } catch {
        if (active) setHealth("unavailable");
      } finally {
        window.clearTimeout(timeout);
        if (active) poll = window.setTimeout(checkHealth, 2000);
      }
    }

    void checkHealth();

    return () => {
      active = false;
      window.clearTimeout(poll);
      controller?.abort();
      window.clearTimeout(timeout);
    };
  }, []);

  const labels: Record<HealthState, string> = {
    checking: "Memeriksa backend…",
    connected: "Backend terhubung",
    unavailable: "Backend tidak tersedia",
  };

  return (
    <main className="page-shell">
      <section className="welcome-card" aria-labelledby="welcome-title">
        <div className="brand-mark" aria-hidden="true">AI</div>
        <p className="eyebrow">AI SOFTWARE DEVELOPMENT TEAM</p>
        <h1 id="welcome-title">Ruang kerja tim development</h1>
        <p className="intro">
          Satu tempat untuk merencanakan, membangun, dan memeriksa pekerjaan
          software bersama tim AI.
        </p>
        <div className={`health health--${health}`} role="status" aria-live="polite">
          <span className="health-dot" aria-hidden="true" />
          {labels[health]}
        </div>
        <p className="footer-note">Fondasi lokal · tanpa akun model</p>
      </section>
    </main>
  );
}
