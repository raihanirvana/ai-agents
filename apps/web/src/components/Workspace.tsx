import { lazy, Suspense, useEffect, useState } from "react";
import { ACTIVE_RUN } from "../format";
import { useWorkspace } from "../workspace";
import Activity from "./Activity";
import Onboarding from "./Onboarding";
import Releases from "./Releases";
import Board from "./Board";
import Chat from "./Chat";
import TicketPanel from "./Ticket";
const Office = lazy(() => import("../features/office/Office"));
import { Badge, ErrorBanner, FakeBadge } from "./ui";

type Tab = "chat" | "ticket" | "activity" | "release" | "office";

export default function Workspace({ onProjects, onLogout }: { onProjects: () => void; onLogout: () => void }) {
  const { board, connection, error, dismissError, selectedTicket, command, busy } = useWorkspace();
  const [tab, setTab] = useState<Tab>("chat");
  const [editing, setEditing] = useState(false);
  const [brief, setBrief] = useState("");
  const [briefRevision, setBriefRevision] = useState(0);
  useEffect(() => { if (selectedTicket) setTab("ticket"); }, [selectedTicket]);

  const active = board?.runs.filter((r) => ACTIVE_RUN.has(r.status)).length ?? 0;
  const fake = board?.runs.some((r) => r.fake) ?? false;
  return (
    <div className="shell">
      <header className="topbar">
        <button type="button" className="link" onClick={onProjects}>← Proyek</button>
        <h1>{board?.project.name ?? "Memuat proyek…"}</h1>
        <div className="topbar-status">
          {fake && <FakeBadge title="Proyek ini punya run fake; hasilnya bukan QA/model nyata" />}
          <Badge tone={connection === "live" ? "good" : connection === "reconnecting" ? "warn" : "neutral"}>
            {connection === "live" ? "Terhubung" : connection === "reconnecting" ? "Menyambung ulang…" : "Memuat…"}
          </Badge>
          {busy && <span className="muted" role="status">Menyimpan…</span>}
          <button type="button" className="ghost" onClick={onLogout}>Keluar</button>
        </div>
      </header>
      <ErrorBanner error={error} onDismiss={dismissError} />
      <Onboarding />
      {board && (
        <details className="brief" open={editing} onToggle={(e) => {
          if (e.currentTarget.open && !editing) { setBrief(board.project.brief); setBriefRevision(board.project.revision); }
          setEditing(e.currentTarget.open);
        }}>
          <summary>Brief <span className="muted">v{board.project.brief_version}</span></summary>
          {editing && (
            <form className="form" onSubmit={async (e) => {
              e.preventDefault();
              const result = await command<"brief">(`/projects/${board.project.id}/brief`, { expected_revision: briefRevision, brief });
              if (result) setEditing(false);
            }}>
              <textarea aria-label="Brief proyek" rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} />
              <div className="actions"><button type="submit" className="primary">Simpan brief</button>
                <span className="muted">Mengubah brief tidak menyetujui atau mengubah tiket.</span></div>
            </form>
          )}
          {!editing && <p>{board.project.brief || <span className="muted">Belum ada brief.</span>}</p>}
        </details>
      )}
      <div className="layout">
        <Board />
        <aside className="side" aria-label="Panel kerja">
          <div className="tabs" role="tablist">
            {([["chat", "Chat PO"], ["ticket", "Tiket"], ["activity", `Aktivitas${active ? ` (${active})` : ""}`], ["release", "Release"], ["office", "Kantor"]] as [Tab, string][]).map(([id, label]) => (
              <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? "tab tab--on" : "tab"}
                onClick={() => { setTab(id); }}>{label}</button>
            ))}
          </div>
          <div className="tab-body" role="tabpanel">
            {tab === "chat" && <Chat />}
            {tab === "ticket" && <TicketPanel />}
            {tab === "activity" && <Activity />}
            {tab === "release" && <Releases />}
            {tab === "office" && <Suspense fallback={<p className="muted pad" role="status">Menyiapkan kantor…</p>}><Office /></Suspense>}
          </div>
        </aside>
      </div>
    </div>
  );
}
