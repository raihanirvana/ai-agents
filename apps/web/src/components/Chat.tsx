import { useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "../../../../contracts/api/types";
import { ACTIVE_RUN, loadDraft, saveDraft, senderLabel, when } from "../format";
import { useWorkspace } from "../workspace";
import { Badge, FakeBadge } from "./ui";

type Task = "breakdown" | "revise" | "note";
const TASKS: { task: Task; label: string; help: string }[] = [
  { task: "breakdown", label: "Minta breakdown", help: "PO memecah kebutuhan menjadi usulan tiket" },
  { task: "revise", label: "Revisi tiket", help: "PO mengusulkan revisi scope; Anda yang memutuskan" },
  { task: "note", label: "Catatan saja", help: "Disimpan tanpa menjalankan PO" },
];

export default function Chat() {
  const { board, messages, command, projectId, selectTicket } = useWorkspace();
  const draftKey = `draft:${projectId}`;
  const [body, setBody] = useState(() => loadDraft(draftKey));
  const [task, setTask] = useState<Task>("breakdown");
  const [ticketId, setTicketId] = useState("");
  const log = useRef<HTMLOListElement>(null);

  useEffect(() => saveDraft(draftKey, body), [draftKey, body]);
  const conversation = useMemo(() => messages
    .filter((m) => m.metadata.intent !== "scope_decision")
    .filter((m) => m.thread_id.startsWith("chat:") || m.sender.startsWith("user") ||
      (m.kind === "input_request" && m.recipient?.startsWith("user")))
    .sort((a, b) => a.created_at.localeCompare(b.created_at) || a.thread_id.localeCompare(b.thread_id) || a.seq - b.seq), [messages]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [conversation.length]);

  if (!board) return <p className="muted pad">Memuat percakapan…</p>;
  const working = board.runs.filter((r) => r.role === "po" && ACTIVE_RUN.has(r.status));
  const tickets = board.tickets.filter((t) => t.phase !== "cancelled");
  const needsTicket = task === "revise";
  const canSend = body.trim().length > 0 && (!needsTicket || ticketId !== "");

  return (
    <div className="chat">
      <ol className="messages" ref={log} aria-label="Percakapan dengan PO" aria-live="polite">
        {conversation.length === 0 && <li className="muted pad">Mulai dengan menjelaskan brief, mis. “Buat aplikasi kedai kopi: profil, menu, transaksi”.</li>}
        {conversation.map((m) => <ChatMessage key={m.id} message={m} onOpenTicket={selectTicket} />)}
        {working.map((r) => (
          <li key={r.id} className="msg msg--agent msg--working" aria-label="PO sedang bekerja">
            <strong>Product Owner</strong> <Badge tone={r.status === "waiting_quota" ? "warn" : "info"}>
              {r.status === "waiting_input" ? "menunggu jawaban" : r.status === "waiting_quota" ? "menunggu kuota provider" : r.status === "queued" ? "antre" : "sedang menyusun jawaban…"}</Badge>
            {r.fake && <> <FakeBadge /></>}
          </li>
        ))}
      </ol>
      <form className="composer" onSubmit={async (e) => {
        e.preventDefault();
        if (!canSend) return;
        const result = await command<"message">(`/projects/${board.project.id}/messages`, {
          body: body.trim(), task, ticket_id: needsTicket ? ticketId : null });
        if (result) setBody("");
      }}>
        <div className="composer-row">
          <label className="inline">Tindakan
            <select value={task} onChange={(e) => setTask(e.target.value as Task)}>
              {TASKS.map((t) => <option key={t.task} value={t.task}>{t.label}</option>)}
            </select>
          </label>
          {needsTicket && (
            <label className="inline">Tiket
              <select value={ticketId} onChange={(e) => setTicketId(e.target.value)} required>
                <option value="">Pilih tiket…</option>
                {tickets.map((t) => <option key={t.id} value={t.id}>#{t.number} {t.title}</option>)}
              </select>
            </label>
          )}
          <span className="muted">{TASKS.find((t) => t.task === task)?.help}</span>
        </div>
        <textarea aria-label="Pesan untuk PO" value={body} rows={3} onChange={(e) => setBody(e.target.value)}
          placeholder="Tulis pesan untuk PO…"
          onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") e.currentTarget.form?.requestSubmit(); }} />
        <div className="actions"><button type="submit" className="primary" disabled={!canSend}>Kirim</button>
          <span className="muted">PO tetap menjawab saat developer bekerja. Ctrl+Enter untuk kirim.</span></div>
      </form>
    </div>
  );
}

function ChatMessage({ message: m, onOpenTicket }: { message: Message; onOpenTicket: (id: string) => void }) {
  const { board, command } = useWorkspace();
  const mine = m.sender.startsWith("user");
  const created = Array.isArray(m.metadata.ticket_ids) ? (m.metadata.ticket_ids as string[]) : [];
  const assumptions = Array.isArray(m.metadata.assumptions) ? (m.metadata.assumptions as string[]) : [];
  const [answer, setAnswer] = useState("");
  const input = m.kind === "input_request" ? m.input : undefined;
  const asksUser = input && m.recipient?.startsWith("user");
  const waitingRun = board?.runs.find((r) => r.status === "waiting_input" && r.request_id === m.id);
  const nonblocking = m.metadata.intent === "user_escalation" && m.metadata.source_request_id == null;
  return (
    <li className={`msg ${mine ? "msg--user" : "msg--agent"}`} data-message={m.id}>
      <div className="msg-meta"><strong>{senderLabel(m.sender)}</strong> <span className="muted">{when(m.created_at)}</span>
        {m.metadata.fake === true && <> <FakeBadge title="Jawaban fake provider; bukan model nyata" /></>}</div>
      <p>{m.body}</p>
      {assumptions.length > 0 && <ul className="assumptions">{assumptions.map((a) => <li key={a}>Asumsi: {a}</li>)}</ul>}
      {created.length > 0 && (
        <div className="created" aria-label="Tiket yang diusulkan">
          {created.map((id) => {
            const ticket = board?.tickets.find((t) => t.id === id);
            return <button type="button" className="chip-link" key={id} onClick={() => onOpenTicket(id)}>{ticket ? `#${ticket.number} ${ticket.title}` : id.slice(0, 8)}</button>;
          })}
        </div>
      )}
      {input && (
        <div className="input-request" data-input-status={input.status}>
          <Badge tone={input.status === "open" ? "warn" : "neutral"}>{input.status === "open" ? "menunggu jawaban" : input.status}</Badge>
          {input.answer && <p className="muted">Jawaban: {input.answer}</p>}
          {asksUser && input.status === "open" && board && (waitingRun || nonblocking) && (
            <form onSubmit={async (e) => {
              e.preventDefault();
              const result = waitingRun ? await command<"input">(`/runs/${waitingRun.id}/input`, {
                expected_revision: waitingRun.revision, request_id: m.id, scope_version: input.scope_version,
                generation: input.generation ?? waitingRun.generation, answer: answer.trim(),
              }) : await command<"nonblockingInput">(`/projects/${board.project.id}/inputs/${m.id}`, {
                scope_version: input.scope_version, generation: input.generation ?? 0, answer: answer.trim() });
              if (result) setAnswer("");
            }}>
              <textarea aria-label="Jawaban Anda" rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} />
              <button type="submit" disabled={!answer.trim()}>Jawab</button>
            </form>
          )}
        </div>
      )}
    </li>
  );
}
