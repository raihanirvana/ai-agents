import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { api } from "../api/instance";

type Health = "checking" | "connected" | "unavailable";
const HEALTH_LABEL: Record<Health, string> = {
  checking: "Memeriksa backend…", connected: "Backend terhubung", unavailable: "Backend tidak tersedia",
};

/** Polls the public health endpoint; the same status text the DEV-001 foundation exposed. */
function useHealth(): Health {
  const [health, setHealth] = useState<Health>("checking");
  useEffect(() => {
    let active = true;
    let poll: number | undefined;
    let timeout: number | undefined;
    let controller: AbortController | undefined;
    async function check() {
      controller = new AbortController();
      timeout = window.setTimeout(() => controller?.abort(), 3000);
      try {
        const response = await fetch(`${api.base}/health`, { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Health request failed");
        const body: unknown = await response.json();
        const ok = body !== null && typeof body === "object" && "status" in body && body.status === "ok";
        if (active) setHealth(ok ? "connected" : "unavailable");
      } catch { if (active) setHealth("unavailable"); }
      finally { window.clearTimeout(timeout); if (active) poll = window.setTimeout(check, 2000); }
    }
    void check();
    return () => { active = false; window.clearTimeout(poll); window.clearTimeout(timeout); controller?.abort(); };
  }, []);
  return health;
}

export default function Login({ onLogin, expired }: { onLogin: () => void; expired: boolean }) {
  const health = useHealth();
  const [code, setCode] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <main className="page-shell">
      <section className="welcome-card" aria-labelledby="welcome-title">
        <div className="brand-mark" aria-hidden="true">AI</div>
        <p className="eyebrow">AI SOFTWARE DEVELOPMENT TEAM</p>
        <h1 id="welcome-title">Ruang kerja tim development</h1>
        <p className="intro">Satu tempat untuk merencanakan, membangun, dan memeriksa pekerjaan software bersama tim AI.</p>
        <div className={`health health--${health}`} role="status" aria-live="polite">
          <span className="health-dot" aria-hidden="true" />{HEALTH_LABEL[health]}
        </div>
        <form className="login" onSubmit={async (e) => {
          e.preventDefault(); setBusy(true); setFailure("");
          try { await api.login(code.trim()); setCode(""); onLogin(); }
          catch (error) { setFailure(error instanceof ApiError ? "Kode login ditolak." : "Tidak dapat menghubungi backend."); }
          finally { setBusy(false); }
        }}>
          {expired && <p className="notice" role="alert">Sesi berakhir. Masuk kembali.</p>}
          <label>Kode login lokal
            <input type="password" autoComplete="off" value={code} onChange={(e) => setCode(e.target.value)} required />
          </label>
          <button type="submit" className="primary" disabled={busy || !code.trim()}>Masuk</button>
          {failure && <p className="notice notice--bad" role="alert">{failure}</p>}
          <p className="footer-note">Kode ada di berkas <code>data/auth/login-code</code>. Kontrol hanya di 127.0.0.1.</p>
        </form>
      </section>
    </main>
  );
}
