import { useCallback, useEffect, useRef, useState } from "react";
import type { Project } from "../../../../contracts/api/types";
import { ApiError } from "../api/client";
import { api } from "../api/instance";
import { ErrorBanner } from "./ui";

export default function Projects({ onOpen, onAuthLost }: { onOpen: (id: string) => void; onAuthLost: () => void }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [mode, setMode] = useState<"new" | "existing">("new");
  const [repo, setRepo] = useState("");
  const [busy, setBusy] = useState(false);
  // One key per logical creation: a retry after a network failure returns the same project.
  const keys = useRef(new Map<string, string>());

  const fail = useCallback((e: unknown) => { if (e instanceof ApiError && e.status === 401) onAuthLost(); else setError(e); }, [onAuthLost]);
  const load = useCallback(() => api.projects().then((r) => setProjects(r.projects), fail), [fail]);
  useEffect(() => { void load(); }, [load]);

  return (
    <main className="projects">
      <h1>Proyek</h1>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div className="projects-grid">
        <section aria-labelledby="list-h">
          <h2 id="list-h">Proyek Anda</h2>
          {projects === null ? <p className="muted" aria-busy="true">Memuat…</p> : projects.length === 0 ? <p className="muted">Belum ada proyek.</p> : (
            <ul className="project-list">
              {projects.map((p) => (
                <li key={p.id}><button type="button" onClick={() => onOpen(p.id)}>
                  <strong>{p.name}</strong><span className="muted">{p.mode === "existing" ? "repo existing" : "proyek baru"}</span>
                </button></li>
              ))}
            </ul>
          )}
        </section>
        <section aria-labelledby="new-h">
          <h2 id="new-h">Proyek baru</h2>
          <form className="form" onSubmit={async (e) => {
            e.preventDefault(); setBusy(true); setError(null);
            const body = { name: name.trim(), mode, brief: brief.trim(), repo_ref: mode === "existing" ? repo.trim() : null };
            const signature = JSON.stringify(body);
            const key = keys.current.get(signature) ?? crypto.randomUUID();
            keys.current.set(signature, key);
            try {
              const result = await api.command<"createProject">("/projects", body, key);
              keys.current.delete(signature);
              onOpen(result.project.id);
            } catch (failure) { if (failure instanceof ApiError && failure.status < 500) keys.current.delete(signature); fail(failure); }
            finally { setBusy(false); }
          }}>
            <label>Nama<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} /></label>
            <label>Jenis
              <select value={mode} onChange={(e) => setMode(e.target.value as "new" | "existing")}>
                <option value="new">Proyek baru</option><option value="existing">Repo lokal existing</option>
              </select>
            </label>
            {mode === "existing" && (
              <label>Path repo lokal<input value={repo} onChange={(e) => setRepo(e.target.value)} required />
                <span className="muted">Path pada host worker (WSL memakai path Linux). Setelah dibuat, pilih manifest untuk onboarding clone managed.</span></label>
            )}
            <label>Brief (opsional)
              <textarea value={brief} onChange={(e) => setBrief(e.target.value)} rows={4} placeholder="Mis. Aplikasi kedai kopi dengan profil, menu, dan transaksi." />
            </label>
            <button type="submit" className="primary" disabled={busy || !name.trim()}>Buat proyek</button>
          </form>
        </section>
      </div>
    </main>
  );
}
