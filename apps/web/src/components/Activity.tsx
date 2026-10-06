import { useEffect, useState } from "react";
import type { Message, Run } from "../../../../contracts/api/types";
import { api } from "../api/instance";
import { ApiError } from "../api/client";
import { ACTIVE_RUN, ROLE_LABEL, RUN_STATUS_LABEL, blockerLabel, text, when } from "../format";
import { useWorkspace } from "../workspace";
import { Badge, ConfirmButton, FakeBadge } from "./ui";

const tone = (status: Run["status"]) =>
  status === "failed" ? "bad" : status === "waiting_input" || status === "waiting_quota" ? "warn" : status === "succeeded" ? "good" : ACTIVE_RUN.has(status) ? "info" : "neutral";

export default function Activity() {
  const { board, selectTicket } = useWorkspace();
  if (!board) return <p className="muted pad">Memuat aktivitas…</p>;
  const runs = [...board.runs].sort((a, b) => Number(ACTIVE_RUN.has(b.status)) - Number(ACTIVE_RUN.has(a.status)));
  const blocked = board.tickets.filter((t) => blockerLabel(t.blocker));
  return (
    <div className="activity">
      <section aria-labelledby="blk-h">
        <h3 id="blk-h">Blocker</h3>
        {blocked.length === 0 ? <p className="muted">Tidak ada blocker.</p> : (
          <ul className="plain">{blocked.map((t) => (
            <li key={t.id}><button type="button" className="link" onClick={() => selectTicket(t.id)}>#{t.number} {t.title}</button>{" "}
              <Badge tone="bad">{blockerLabel(t.blocker)}</Badge></li>
          ))}</ul>
        )}
      </section>
      <section aria-labelledby="run-h">
        <h3 id="run-h">Run tim</h3>
        {runs.length === 0 && <p className="muted">Belum ada run. Kirim pesan ke PO untuk memulai.</p>}
        <ul className="runs">{runs.map((run) => <RunRow key={run.id} run={run} />)}</ul>
      </section>
    </div>
  );
}

function usageText(run: Run): string {
  const parts = Object.entries(run.usage).filter(([k, v]) => typeof v === "number" && !k.startsWith("_")).map(([k, v]) => `${k} ${String(v)}`);
  const unknown = Array.isArray(run.usage._unknown) && run.usage._unknown.length > 0 ? `tidak diketahui: ${run.usage._unknown.join(", ")}` : "";
  return [...parts, unknown].filter(Boolean).join(" · ") || "belum ada penggunaan";
}

function RunRow({ run }: { run: Run }) {
  const { board, command, selectTicket, messages } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<{ input: Message | null; logs: Message[]; failure: string } | null>(null);
  const [answer, setAnswer] = useState("");
  const ticket = board?.tickets.find((t) => t.id === run.ticket_id);
  const active = ACTIVE_RUN.has(run.status);
  // Reload on every project event: revision/status change means the question or log may have changed.
  useEffect(() => {
    if (!open && run.status !== "waiting_input") return;
    let alive = true;
    Promise.all([api.run(run.id), run.generation > 0 ? api.logs(run.id, run.generation) : Promise.resolve({ messages: [] })]).then(
      ([r, l]) => { if (alive) setInfo({ input: r.input, logs: l.messages, failure: "" }); },
      (e) => { if (alive) setInfo({ input: null, logs: [], failure: e instanceof ApiError ? e.detail.message : "tidak dapat dimuat" }); });
    return () => { alive = false; };
  }, [open, run.id, run.generation, run.status, run.revision, messages.length]);
  const input = info?.input?.input;
  useEffect(() => { setAnswer(""); }, [info?.input?.id, run.generation]);
  const result = text(run.result?.error ?? run.result?.reason ?? run.result?.message);
  return (
    <li className="run" data-run={run.id} data-status={run.status}>
      <div className="run-head">
        <strong>{ROLE_LABEL[run.role] ?? run.role}</strong> <span className="muted">{run.stage}</span>
        <Badge tone={tone(run.status)}>{RUN_STATUS_LABEL[run.status]}</Badge>
        {run.fake ? <FakeBadge title={`Runtime ${run.runtime}: fake, bukan QA/model nyata`} /> : <Badge title={`Runtime ${run.runtime}`}>{run.runtime}</Badge>}
        <span className="run-actions">
          <button type="button" className="ghost small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Tutup" : "Detail"}</button>
          {active && (
            <ConfirmButton tone="danger" label="Stop" confirmLabel="Ya, hentikan run"
              onConfirm={async () => { await command<"stop">(`/runs/${run.id}/stop`, {}); }} />
          )}
        </span>
      </div>
      {ticket && <div className="muted">Tiket <button type="button" className="link" onClick={() => selectTicket(ticket.id)}>#{ticket.number} {ticket.title}</button>{run.scope_version ? ` · scope v${run.scope_version}` : ""}</div>}
      {run.result?.qa_status === "suite_repaired" && <p className="notice" role="status">Selector QA diperbaiki. Menunggu pengujian ulang pada target baru.</p>}
      {run.result?.failure_kind === "test_contract" && run.status === "failed" && <p className="notice" role="status">Kontrak tes QA perlu diperiksa. Tiket tetap di QA.</p>}
      {run.result?.failure_kind === "infrastructure" && run.status === "failed" && <p className="notice" role="status">Runner atau infrastruktur QA bermasalah. Lihat detail dan status retry.</p>}
      {run.result?.failure_kind === "provider" && run.status === "failed" && <p className="notice" role="status">Permintaan ke provider model gagal. Lihat detail dan status retry.</p>}
      {run.result?.qa_status === "failed" && run.result?.request_changes === true && <p className="notice" role="status">QA gagal; bukti kegagalan dikirim ke developer.</p>}
      {run.status === "waiting_quota" && <p className="notice" role="status">Menunggu kuota provider{run.available_at ? `; dicoba lagi sekitar ${when(run.available_at)}` : ""}. Run tidak gagal.</p>}
      {run.status === "waiting_input" && (
        <div className="notice" role="status">
          <strong>Menunggu jawaban{input ? ` dari ${input.recipient.replace("agent:", "")}` : ""}.</strong>
          {info?.input && <p>{info.input.body}</p>}
          {input && input.status === "open" && input.recipient.startsWith("user") && info?.input?.id === run.request_id && input.generation === run.generation && (
            <form onSubmit={async (e) => {
              e.preventDefault();
              const result = await command<"input">(`/runs/${run.id}/input`, {
                expected_revision: run.revision, request_id: info.input!.id, scope_version: input.scope_version,
                generation: input.generation ?? run.generation, answer: answer.trim() });
              if (result) setAnswer("");
            }}>
              <textarea aria-label="Jawaban untuk run" rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} />
              <button type="submit" disabled={!answer.trim()}>Jawab dan lanjutkan</button>
            </form>
          )}
        </div>
      )}
      {run.status === "failed" && result && <p className="notice notice--bad">{result}</p>}
      {open && (
        <div className="run-detail">
          <dl className="ids">
            <dt>Penggunaan</dt><dd>{usageText(run)}</dd>
            <dt>Attempt</dt><dd>{run.attempt} · generation {run.generation} · lane {run.lane}</dd>
            {Object.keys(run.limits).length > 0 && <><dt>Batas</dt><dd>{Object.entries(run.limits).filter(([k]) => k !== "budget_key").map(([k, v]) => `${k} ${String(v)}`).join(" · ")}</dd></>}
          </dl>
          {info?.failure && <p className="notice notice--bad">{info.failure}</p>}
          <ol className="logs" aria-label="Log run">
            {info?.logs.length === 0 && <li className="muted">Belum ada log.</li>}
            {info?.logs.map((l) => <li key={l.id}>{l.body}</li>)}
          </ol>
        </div>
      )}
    </li>
  );
}
