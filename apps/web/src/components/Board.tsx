import { useMemo, useState } from "react";
import type { Run, Ticket } from "../../../../contracts/api/types";
import { ACTIVE_RUN, PHASES, RUN_STATUS_LABEL, blockerLabel, byPriority } from "../format";
import { useWorkspace } from "../workspace";
import { Badge, ConfirmButton, FakeBadge } from "./ui";
import DependencyNotice from "./DependencyNotice";

export default function Board() {
  const { board, command, selectedTicket, selectTicket, connection } = useWorkspace();
  const [dragging, setDragging] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const runsByTicket = useMemo(() => {
    const map = new Map<string, Run[]>();
    for (const run of board?.runs ?? []) if (run.ticket_id) map.set(run.ticket_id, [...(map.get(run.ticket_id) ?? []), run]);
    return map;
  }, [board?.runs]);

  if (!board) return <section className="board" aria-busy="true"><p className="muted pad">Memuat board…</p></section>;
  const tickets = board.tickets;
  const cancelled = tickets.filter((t) => t.phase === "cancelled");

  /** Re-rank one column after a move: only tickets whose priority actually changes are sent. */
  async function reorder(column: Ticket[], movedId: string, targetId: string | null, after = false) {
    const order = column.filter((t) => t.id !== movedId);
    const moved = column.find((t) => t.id === movedId);
    if (!moved) return;
    const at = targetId === null ? order.length : order.findIndex((t) => t.id === targetId) + (after ? 1 : 0);
    order.splice(Math.max(0, at), 0, moved);
    for (const [index, ticket] of order.entries()) {
      const priority = order.length - index;
      if (ticket.priority === priority) continue;
      const saved = await command<"priority">(`/tickets/${ticket.id}/priority`, { expected_revision: ticket.revision, priority });
      if (!saved) return; // conflict/error already surfaced; the refreshed board shows the real order
    }
  }

  const approvable = tickets.filter((t) => picked.has(t.id) && t.phase === "scope_review");

  return (
    <section className="board" aria-label="Board tiket">
      <div className="board-note" role="note">
        Seret kartu dalam satu kolom untuk mengubah prioritas (angka lebih besar = lebih atas). Phase hanya berpindah lewat tindakan eksplisit di detail tiket.
        {connection === "reconnecting" && <strong> Koneksi event terputus; menyambung ulang…</strong>}
      </div>
      {notice && <p className="notice" role="status">{notice}</p>}
      <div className="columns">
        {PHASES.filter((p) => p.phase !== "draft" || tickets.some((t) => t.phase === "draft")).map(({ phase, label, hint }) => {
          const column = tickets.filter((t) => t.phase === phase).sort(byPriority);
          return (
            <div className="column" key={phase} data-phase={phase}
              onDragOver={(e) => { if (dragging) e.preventDefault(); }}
              onDrop={(e) => {
                e.preventDefault();
                const moved = tickets.find((t) => t.id === dragging);
                setDragging(null);
                if (!moved) return;
                if (moved.phase !== phase) setNotice("Phase tidak bisa diubah dengan drag/drop. Gunakan tindakan eksplisit di detail tiket.");
                else { setNotice(""); void reorder(column, moved.id, null); }
              }}>
              <header>
                <h3>{label}</h3><span className="count" aria-label={`${column.length} tiket`}>{column.length}</span>
              </header>
              <p className="column-hint">{hint}</p>
              {phase === "scope_review" && column.length > 0 && (
                <div className="batch">
                  <label><input type="checkbox" checked={column.every((t) => picked.has(t.id))}
                    onChange={(e) => setPicked(e.target.checked ? new Set(column.map((t) => t.id)) : new Set())} /> Pilih semua</label>
                  <ConfirmButton label={`Setujui scope terpilih (${approvable.length})`} confirmLabel="Ya, setujui scope" disabled={approvable.length === 0}
                    intent={JSON.stringify(approvable.map((t) => [t.id, t.scope_version, t.revision]))}
                    onConfirm={async () => {
                      const result = await command<"approveScope">(`/projects/${board.project.id}/scope-approvals`, {
                        items: approvable.map((t) => ({ ticket_id: t.id, scope_version: t.scope_version, expected_revision: t.revision })) });
                      if (result) setPicked(new Set());
                    }}>
                    <span className="muted">{approvable.map((t) => `#${t.number} v${t.scope_version}`).join(", ")}</span>
                  </ConfirmButton>
                </div>
              )}
              <ul className="cards">
                {column.map((ticket, index) => {
                  const runs = runsByTicket.get(ticket.id) ?? [];
                  const active = runs.filter((r) => ACTIVE_RUN.has(r.status));
                  const blocker = blockerLabel(ticket.blocker);
                  return (
                    <li key={ticket.id}>
                      <article className={`card${selectedTicket === ticket.id ? " card--selected" : ""}${dragging === ticket.id ? " card--dragging" : ""}`}
                        draggable aria-label={`Tiket ${ticket.number}: ${ticket.title}`}
                        onClick={(e) => {
                          if ((e.target as HTMLElement).closest("button, input, .card-actions")) return;
                          selectTicket(ticket.id);
                        }}
                        onDragStart={(e) => { setDragging(ticket.id); e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", ticket.id); }}
                        onDragEnd={() => setDragging(null)}
                        onDragOver={(e) => { if (dragging) e.preventDefault(); }}
                        onDrop={(e) => {
                          e.preventDefault(); e.stopPropagation();
                          const moved = tickets.find((t) => t.id === dragging);
                          setDragging(null);
                          if (!moved || moved.id === ticket.id) return;
                          if (moved.phase !== phase) setNotice("Phase tidak bisa diubah dengan drag/drop. Gunakan tindakan eksplisit di detail tiket.");
                          else { setNotice(""); void reorder(column, moved.id, ticket.id); }
                        }}>
                        <div className="card-top">
                          {phase === "scope_review" && (
                            <input type="checkbox" aria-label={`Pilih tiket ${ticket.number} untuk approval`} checked={picked.has(ticket.id)}
                              onChange={(e) => setPicked((prev) => { const next = new Set(prev); e.target.checked ? next.add(ticket.id) : next.delete(ticket.id); return next; })} />
                          )}
                          <button type="button" className="card-title" onClick={() => selectTicket(ticket.id)}>
                            <span className="num">#{ticket.number}</span> {ticket.title}
                          </button>
                        </div>
                        <div className="chips">
                          <Badge>scope v{ticket.scope_version}</Badge>
                          <Badge title="Prioritas">P{ticket.priority}</Badge>
                          {blocker && <Badge tone="bad">{blocker}</Badge>}
                          {runs.some((r) => r.fake) && <FakeBadge />}
                          {active.map((r) => (
                            <Badge key={r.id} tone={r.status === "waiting_input" || r.status === "waiting_quota" ? "warn" : "info"}>
                              {RUN_STATUS_LABEL[r.status]}
                            </Badge>
                          ))}
                        </div>
                        <DependencyNotice ticket={ticket} />
                        <div className="card-actions">
                          <button type="button" className="ghost small" aria-label={`Naikkan prioritas tiket ${ticket.number}`} disabled={index === 0}
                            onClick={() => void reorder(column, ticket.id, column[index - 1].id)}>▲</button>
                          <button type="button" className="ghost small" aria-label={`Turunkan prioritas tiket ${ticket.number}`} disabled={index === column.length - 1}
                            onClick={() => void reorder(column, ticket.id, column[index + 1].id, true)}>▼</button>
                        </div>
                      </article>
                    </li>
                  );
                })}
                {column.length === 0 && <li className="empty">Kosong</li>}
              </ul>
            </div>
          );
        })}
      </div>
      {cancelled.length > 0 && (
        <details className="cancelled"><summary>Dibatalkan ({cancelled.length})</summary>
          <ul>{cancelled.map((t) => <li key={t.id}><button type="button" className="link" onClick={() => selectTicket(t.id)}>#{t.number} {t.title}</button></li>)}</ul>
        </details>
      )}
    </section>
  );
}
