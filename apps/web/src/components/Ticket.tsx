import { useEffect, useMemo, useState } from "react";
import type { Artifact, Candidate, Criterion, Message, Preview, TicketDetail, Verification } from "../../../../contracts/api/types";
import { ApiError } from "../api/client";
import { api } from "../api/instance";
import { asScope, diffScope, type ScopeDoc } from "../diff";
import { RUN_STATUS_LABEL, blockerLabel, senderLabel, short, text, when } from "../format";
import { useWorkspace } from "../workspace";
import { Badge, ConfirmButton, FakeBadge } from "./ui";
import DependencyNotice from "./DependencyNotice";
import RebaseNotice from "./RebaseNotice";

export default function TicketPanel() {
  const { detail, board, selectedTicket, selectTicket } = useWorkspace();
  if (!selectedTicket) return <p className="muted pad">Pilih tiket di board untuk melihat scope, bukti, dan keputusan.</p>;
  if (!detail || detail.ticket.id !== selectedTicket) return <p className="muted pad" aria-busy="true">Memuat tiket…</p>;
  const { ticket } = detail;
  const runs = (board?.runs ?? []).filter((r) => r.ticket_id === ticket.id);
  const fake = runs.some((r) => r.fake);
  const blocker = blockerLabel(ticket.blocker);
  return (
    <div className="ticket" data-ticket={ticket.id} key={ticket.id}>
      <header className="ticket-head">
        <button type="button" className="link" onClick={() => selectTicket(null)}>← Tutup</button>
        <h2>#{ticket.number} {ticket.title}</h2>
        <div className="chips">
          <Badge tone="info">{ticket.phase}</Badge><Badge>scope v{ticket.scope_version}</Badge>
          <Badge title="Revisi tiket">rev {ticket.revision}</Badge>
          {fake && <FakeBadge />}
        </div>
        {blocker && <div className="blocker" role="alert"><strong>Blocker:</strong> {blocker}</div>}
        <DependencyNotice ticket={ticket} />
        <RebaseNotice ticket={ticket} />
      </header>
      <ScopeSection detail={detail} />
      <ProposalSection detail={detail} />
      <Dependencies detail={detail} />
      <VerificationPlan detail={detail} />
      <QaResolution key={JSON.stringify(detail.qa_resolution)} detail={detail} />
      <Candidates detail={detail} fake={fake} />
      <Approvals detail={detail} />
      <Work detail={detail} />
      <Messages messages={detail.messages} />
    </div>
  );
}

function currentScope(detail: TicketDetail): { doc: ScopeDoc; version: number } {
  const entry = detail.versions.find((v) => v.version === detail.ticket.scope_version) ?? detail.versions[detail.versions.length - 1];
  return { version: entry?.version ?? 0, doc: entry ? asScope(entry.scope, { title: entry.title, description: entry.description, uac: entry.uac }) : { title: detail.ticket.title, uac: [] } };
}

function ScopeSection({ detail }: { detail: TicketDetail }) {
  const { command, board } = useWorkspace();
  const { ticket } = detail;
  const { doc } = currentScope(detail);
  const [editing, setEditing] = useState<{ doc: ScopeDoc; revision: number } | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const shown = detail.versions.find((v) => v.version === (version ?? ticket.scope_version)) ?? detail.versions[0];
  const editable = ["draft", "scope_review", "ready"].includes(ticket.phase);
  return (
    <section aria-labelledby="scope-h">
      <div className="section-head">
        <h3 id="scope-h">Scope dan UAC</h3>
        {detail.versions.length > 1 && (
          <label className="inline">Versi
            <select value={shown?.version} onChange={(e) => setVersion(Number(e.target.value))}>
              {detail.versions.map((v) => <option key={v.version} value={v.version}>v{v.version}{v.version === ticket.scope_version ? " (saat ini)" : ""}</option>)}
            </select>
          </label>
        )}
      </div>
      {editing ? (
        <><p className="muted">Draf dari rev {editing.revision}.
          {editing.revision !== ticket.revision && " Scope berubah saat Anda mengedit. Salin draf, batalkan, lalu buka editor dari data terbaru; simpan draf lama akan ditolak."}</p>
        <ScopeForm doc={editing.doc} onCancel={() => setEditing(null)} onSave={async (document) => {
          const result = await command<"editScope">(`/tickets/${ticket.id}/scope-versions`, { expected_revision: editing.revision, document });
          if (result) setEditing(null);
        }} /></>
      ) : shown && (
        <>
          <p className="desc">{shown.description || <span className="muted">Tanpa deskripsi</span>}</p>
          <ul className="uac" aria-label="Kriteria penerimaan">
            {shown.uac.map((c) => <li key={c.id}><code>{c.id}</code> {c.text} <Badge tone={c.mode === "manual" ? "warn" : "info"}
              title={c.mode === "manual" ? "Perlu konfirmasi Anda saat UAT" : "Wajib dibuktikan tes otomatis"}>
              {c.mode === "manual" ? "Anda saat UAT" : "otomatis"}</Badge></li>)}
          </ul>
        </>
      )}
      <div className="actions">
        {!editing && editable && <button type="button" onClick={() => setEditing({ doc, revision: ticket.revision })}>Edit scope</button>}
        {ticket.phase === "scope_review" && !editing && (
          <ConfirmButton label={`Setujui scope v${ticket.scope_version}`} confirmLabel="Ya, setujui" onConfirm={async () => {
            await command<"approveScope">(`/projects/${board?.project.id}/scope-approvals`, {
              items: [{ ticket_id: ticket.id, scope_version: ticket.scope_version, expected_revision: ticket.revision }] });
          }} intent={JSON.stringify([ticket.id, ticket.scope_version, ticket.revision])} />
        )}
        {!["cancelled", "accepted"].includes(ticket.phase) && !editing && (
          <ConfirmButton tone="danger" label="Batalkan tiket" confirmLabel="Ya, batalkan"
            intent={JSON.stringify([ticket.id, ticket.revision])}
            onConfirm={async () => { await command<"cancel">(`/tickets/${ticket.id}/cancel`, { expected_revision: ticket.revision }); }} />
        )}
        {ticket.phase === "scope_review" && <span className="muted">Approval selalu atas scope v{ticket.scope_version} tiket ini.</span>}
      </div>
      <AskPo ticketId={ticket.id} />
    </section>
  );
}

function VerificationPlan({ detail }: { detail: TicketDetail }) {
  const plan = detail.verification_plan;
  const criteria = plan?.criteria ?? currentScope(detail).doc.uac.map((c) => ({ ...c, test_ids: [] as string[] }));
  const automatic = criteria.filter((c) => c.mode !== "manual");
  const manual = criteria.filter((c) => c.mode === "manual");
  return (
    <section aria-labelledby="verification-plan-h">
      <h3 id="verification-plan-h">Rencana pengujian</h3>
      <p>{automatic.length} kriteria otomatis · {manual.length} kriteria Anda periksa saat UAT.
        {plan?.status === "planned" && <> Direncanakan {plan.test_count} skenario browser.</>}</p>
      {plan?.status === "not_planned" && <p className="muted">Skenario disiapkan paralel dengan Developer dan harus tersedia sebelum submit. Kemampuan pengujian yang belum tersedia dibahas saat planning.</p>}
      <details><summary>Lihat pembagian pemeriksaan</summary><ul className="plain">
        {criteria.map((c) => <li key={c.id}><code>{c.id}</code> {c.text}{" "}
          <Badge tone={c.mode === "manual" ? "warn" : "info"}>{c.mode === "manual" ? "Checklist UAT" : "Otomatis"}</Badge>
          {c.mode !== "manual" && c.test_ids.length > 0 && <span className="muted"> · {c.test_ids.length} skenario</span>}
        </li>)}
      </ul></details>
      <p className="muted">Hasil otomatis ada pada bukti kandidat. Checklist manual tetap memerlukan konfirmasi Anda; tampilan dan kenyamanan juga dapat dicoba lewat preview.</p>
    </section>
  );
}

function ScopeForm({ doc, onSave, onCancel }: { doc: ScopeDoc; onSave: (d: ScopeDoc) => Promise<void>; onCancel: () => void }) {
  const [title, setTitle] = useState(doc.title);
  const [description, setDescription] = useState(doc.description ?? "");
  const [uac, setUac] = useState<Criterion[]>(doc.uac.map((c) => ({ ...c })));
  const [qaProfile, setQaProfile] = useState<"lightweight" | "manual">(doc.qa_profile ?? "lightweight");
  const [saving, setSaving] = useState(false);
  const patch = (index: number, change: Partial<Criterion>) => setUac((rows) => rows.map((r, i) => (i === index ? { ...r, ...change } : r)));
  return (
    <form className="form" onSubmit={async (e) => {
      e.preventDefault(); setSaving(true);
      await onSave({ ...doc, qa_profile: qaProfile, title, description, uac: uac.filter((c) => c.text.trim()), dependencies: doc.dependencies ?? [] });
      setSaving(false);
    }}>
      <label>Judul<input value={title} onChange={(e) => setTitle(e.target.value)} required maxLength={120} /></label>
      <label>Deskripsi<textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3} /></label>
      <label>Preset QA<select value={qaProfile} onChange={(e) => {
        const value = e.target.value as "lightweight" | "manual"; setQaProfile(value);
        setUac((rows) => rows.map((c) => ({ ...c, mode: value === "manual" ? "manual" : "automated" })));
      }}><option value="lightweight">Otomatis sesuai UAC</option><option value="manual">Smoke otomatis + saya uji manual</option></select></label>
      <p className="muted">Perubahan menjadi scope versi baru dan perlu persetujuan Anda. Build, repo tests, dan smoke tetap wajib.</p>
      <fieldset><legend>Kriteria penerimaan (UAC)</legend>
        {uac.map((c, i) => (
          <div className="uac-row" key={c.id}>
            <code>{c.id}</code>
            <input aria-label={`Teks ${c.id}`} value={c.text} onChange={(e) => patch(i, { text: e.target.value })} />
            <select aria-label={`Mode ${c.id}`} value={c.mode ?? "automated"} onChange={(e) => { setQaProfile("lightweight"); patch(i, { mode: e.target.value as "automated" | "manual" }); }}>
              <option value="automated">otomatis</option><option value="manual">manual</option>
            </select>
          </div>
        ))}
        <button type="button" className="ghost" onClick={() => {
          const used = new Set(uac.map((c) => c.id)); let n = uac.length + 1;
          while (used.has(`UAC-${n}`)) n += 1;
          setUac([...uac, { id: `UAC-${n}`, text: "", mode: "automated" }]);
        }}>+ Tambah UAC</button>
      </fieldset>
      <div className="actions">
        <button type="submit" className="primary" disabled={saving || !title.trim() || uac.every((c) => !c.text.trim())}>Simpan sebagai versi baru</button>
        <button type="button" className="ghost" onClick={onCancel}>Batal</button>
      </div>
    </form>
  );
}

function AskPo({ ticketId }: { ticketId: string }) {
  const { command, board } = useWorkspace();
  const [body, setBody] = useState("");
  return (
    <form className="ask" onSubmit={async (e) => {
      e.preventDefault();
      if (!board || !body.trim()) return;
      const result = await command<"message">(`/projects/${board.project.id}/messages`, {
        body: body.trim(), task: "revise", ticket_id: ticketId });
      if (result) setBody("");
    }}>
      <label>Minta PO merevisi tiket ini
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2} placeholder="Mis. tambahkan kriteria untuk stok habis" />
      </label>
      <button type="submit" disabled={!body.trim()}>Kirim ke PO</button>
    </form>
  );
}

function ProposalSection({ detail }: { detail: TicketDetail }) {
  const { command } = useWorkspace();
  const { ticket } = detail;
  const { doc } = currentScope(detail);
  const decided = new Map<string, Message>();
  for (const m of detail.messages) if (m.reply_to) decided.set(m.reply_to, m);
  const proposals = detail.messages.filter((m) => m.metadata.intent === "scope_proposal");
  if (proposals.length === 0) return null;
  return (
    <section aria-labelledby="prop-h">
      <h3 id="prop-h">Usulan revisi dari PO</h3>
      {[...proposals].reverse().map((proposal) => {
        const verdict = decided.get(proposal.id);
        const stale = proposal.metadata.base_version !== ticket.scope_version;
        const next = asScope(proposal.metadata.document, doc);
        const base = detail.versions.find((v) => v.version === proposal.metadata.base_version);
        const changes = diffScope(base ? asScope(base.scope, { title: base.title, description: base.description, uac: base.uac }) : doc, next);
        return (
          <article className="proposal" key={proposal.id} data-proposal={proposal.id}>
            <div className="chips">
              <Badge tone={verdict ? (verdict.body === "accepted" ? "good" : "neutral") : stale ? "warn" : "info"}>
                {verdict ? (verdict.body === "accepted" ? "Diterima" : "Ditolak") : stale ? "Basi: scope sudah berubah" : "Menunggu keputusan Anda"}
              </Badge>
              <span className="muted">{senderLabel(proposal.sender)} · dari scope v{text(proposal.metadata.base_version)}</span>
              {proposal.metadata.fake === true && <FakeBadge title="Usulan dari fake provider" />}
            </div>
            <table className="diff" aria-label="Perbedaan usulan">
              <tbody>
                {changes.filter((c) => c.kind !== "same").length === 0 && <tr><td colSpan={2} className="muted">Tidak ada perbedaan dari scope asal usulan.</td></tr>}
                {changes.filter((c) => c.kind !== "same").map((c) => (
                  <tr key={c.label} className={`diff-${c.kind}`}>
                    <th scope="row">{c.label}</th>
                    <td>{c.before !== undefined && <del>{c.before || "(kosong)"}</del>} {c.after !== undefined && <ins>{c.after || "(kosong)"}</ins>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!verdict && !stale && (
              <div className="actions">
                <button type="button" className="primary" onClick={() => void command<"proposalDecision">(
                  `/tickets/${ticket.id}/proposals/${proposal.id}/decisions`, { expected_revision: ticket.revision, accept: true })}>Terima revisi</button>
                <button type="button" onClick={() => void command<"proposalDecision">(
                  `/tickets/${ticket.id}/proposals/${proposal.id}/decisions`, { expected_revision: ticket.revision, accept: false })}>Tolak</button>
                <span className="muted">Menerima membuat versi scope baru; approval scope tetap tindakan terpisah.</span>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}

function Dependencies({ detail }: { detail: TicketDetail }) {
  const { board, selectTicket } = useWorkspace();
  if (detail.dependencies.length === 0) return null;
  return (
    <section aria-labelledby="dep-h">
      <h3 id="dep-h">Dependency</h3>
      <ul className="plain">
        {detail.dependencies.map((d) => {
          const upstream = board?.tickets.find((t) => t.id === d.upstream_id);
          return (
            <li key={d.upstream_id}>
              <button type="button" className="link" onClick={() => selectTicket(d.upstream_id)}>
                {upstream ? `#${upstream.number} ${upstream.title}` : short(d.upstream_id)}
              </button>{" "}
              <Badge tone={d.state === "accepted" || d.state === "satisfied" ? "good" : "warn"}>{d.state === "satisfied" || d.state === "accepted" ? "Terpenuhi" : d.state === "needs_revalidation" ? "Perlu validasi ulang" : "Menunggu"}</Badge>
              {d.integration_sha && <span className="muted"> integrasi {short(d.integration_sha, 8)}</span>}
              {d.revalidation ? <Badge tone="bad" title={text(d.revalidation)}>perlu validasi ulang</Badge> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function ArtifactChip({ id, label }: { id: string; label: string }) {
  const { artifactEpoch } = useWorkspace();
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  const [failure, setFailure] = useState("");
  useEffect(() => {
    let alive = true;
    setArtifact(null); setFailure("");
    api.artifact(id).then((r) => { if (alive) setArtifact(r.artifact); },
      (e) => { if (alive) setFailure(e instanceof ApiError ? e.detail.message : "tidak dapat dimuat"); });
    return () => { alive = false; };
  }, [id, artifactEpoch]);
  const missing = failure || artifact?.availability === "unavailable";
  return (
    <span className={`artifact${missing ? " artifact--missing" : ""}`} data-artifact={id}>
      {label} <code>{short(id, 8)}</code>{" "}
      {artifact === null && !failure && <span className="muted">memeriksa…</span>}
      {missing && <Badge tone="bad">tidak tersedia{artifact?.unavailable_reason ? `: ${artifact.unavailable_reason}` : failure ? `: ${failure}` : ""}</Badge>}
      {artifact?.availability === "available" && <a href={api.artifactUrl(id)} target="_blank" rel="noreferrer">buka</a>}
    </span>
  );
}

function QaResolution({ detail }: { detail: TicketDetail }) {
  const { command } = useWorkspace();
  const resolution = detail.qa_resolution;
  const [reason, setReason] = useState("");
  const [owned, setOwned] = useState<Set<string>>(new Set());
  if (!resolution) return null;
  if (!resolution.eligible) return <p className="notice">Keputusan QA manual belum tersedia: {resolution.reason}</p>;
  const criteria = resolution.criteria ?? [];
  const complete = criteria.length > 0 && criteria.every((c) => owned.has(c.id));
  return <section aria-label="Keputusan QA tidak konklusif">
    <h3>QA tidak konklusif</h3>
    <p>Anda dapat mengambil alih pemeriksaan berikut untuk target <code>{short(resolution.target_digest, 12)}</code>.
      Hasil QA tetap gagal. Setelah ini, buka preview dan konfirmasikan hasil Anda pada UAT.</p>
    <div className="evidence">{resolution.evidence_ids?.map((id) => <ArtifactChip key={id} id={id} label="bukti diagnosis" />)}</div>
    <fieldset><legend>Kriteria yang akan saya periksa sendiri</legend>{criteria.map((c) =>
      <label key={c.id}><input type="checkbox" checked={owned.has(c.id)} onChange={(e) => setOwned((previous) => {
        const next = new Set(previous); e.target.checked ? next.add(c.id) : next.delete(c.id); return next;
      })} />{c.id}: {c.text}</label>)}</fieldset>
    <label>Alasan<textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={4000} /></label>
    <ConfirmButton label="Ambil alih dan buka UAT" confirmLabel="Ya, saya periksa kriteria ini" disabled={!complete || !reason.trim()}
      intent={JSON.stringify([detail.ticket.revision, resolution, reason, [...owned]])} onConfirm={async () => {
        await command<"qaManual">(`/tickets/${detail.ticket.id}/qa-manual-decisions`, {
          expected_revision: detail.ticket.revision, candidate_id: resolution.candidate_id!,
          verification_id: resolution.verification_id!, target_artifact_id: resolution.target_artifact_id!,
          target_digest: resolution.target_digest!, diagnosis_artifact_id: resolution.diagnosis_artifact_id!,
          evidence_ids: resolution.evidence_ids!, manual_uac_ids: [...owned], reason: reason.trim(),
        });
      }} />
  </section>;
}

function Candidates({ detail, fake }: { detail: TicketDetail; fake: boolean }) {
  if (detail.candidates.length === 0) {
    return <section aria-labelledby="cand-h"><h3 id="cand-h">Kandidat dan bukti</h3><p className="muted">Belum ada kandidat. Bukti QA muncul setelah harness menjalankan verifikasi.</p></section>;
  }
  return (
    <section aria-labelledby="cand-h">
      <h3 id="cand-h">Kandidat dan bukti</h3>
      {fake && <p className="notice">Tiket ini memiliki run fake. Hasil di bawah tidak membuktikan QA nyata atau kompatibilitas provider.</p>}
      {detail.candidates.map((c) => <CandidateCard key={c.id} candidate={c} detail={detail} />)}
      <UatSection detail={detail} />
    </section>
  );
}

function CandidateCard({ candidate: c, detail }: { candidate: Candidate; detail: TicketDetail }) {
  return (
    <article className="candidate" data-candidate={c.id}>
      <div className="chips"><Badge tone={c.status === "superseded" || c.status === "rejected" ? "neutral" : "info"}>{c.status}</Badge>
        <span className="muted">scope v{c.scope_version}{c.scope_version !== detail.ticket.scope_version ? " (bukan scope saat ini)" : ""}</span></div>
      <dl className="ids">
        <dt>Commit</dt><dd><code>{short(c.commit_sha, 12)}</code> · basis <code>{short(c.base_sha, 12)}</code></dd>
        <dt>Target build</dt><dd>{c.target_digest ? <code title={c.target_digest}>{short(c.target_digest, 16)}</code> : <span className="muted">belum ada target terverifikasi</span>}</dd>
        <dt>Artefak</dt>
        <dd>
          <ArtifactChip id={c.commit_artifact_id} label="commit" />
          {c.build_artifact_id && <> · <ArtifactChip id={c.build_artifact_id} label="build" /></>}
          {c.target_artifact_id && <> · <ArtifactChip id={c.target_artifact_id} label="target" /></>}
        </dd>
      </dl>
      {c.qa_waiver && <p className="notice">QA belum konklusif · keputusan manual pengguna: {c.qa_waiver.reason}. Kriteria {c.qa_waiver.manual_uac_ids.join(", ")} harus dikonfirmasi saat UAT.</p>}
      {c.verifications.length === 0 && <p className="muted">Belum ada verifikasi untuk target ini.</p>}
      {c.verifications.map((v) => <VerificationRow key={v.id} v={v} />)}
      <div className="evidence">{c.evidence_ids.filter((id) => !c.verifications.some((v) => v.evidence_ids.includes(id)))
        .map((id) => <ArtifactChip key={id} id={id} label="bukti kandidat / preview" />)}</div>
      <IntegrationRow candidate={c} />
      <PreviewPanel candidate={c} detail={detail} />
    </article>
  );
}

const PREVIEW_LABEL: Record<Preview["status"], string> = {
  requested: "Menunggu supervisor", starting: "Memulai…", ready: "Siap dibuka", stopping: "Menghentikan…", stopped: "Berhenti", failed: "Gagal",
};
const STOP_REASON: Record<string, string> = {
  user_stop: "dihentikan oleh Anda", switched: "digantikan preview lain (hanya satu preview aktif)",
  superseded: "kandidat diganti atau tiket meninggalkan UAT", worker_restart: "worker dimulai ulang; buka ulang dari artefak yang sama",
  worker_stopped: "worker berhenti",
};

/** localhost link only: the preview must never be opened on the control host, which holds the session cookie. */
function previewLink(url: string | null): string | null {
  try { return url && new URL(url).hostname === "localhost" ? url : null; } catch { return null; }
}

function PreviewPanel({ candidate, detail }: { candidate: Candidate; detail: TicketDetail }) {
  const { command } = useWorkspace();
  const { ticket } = detail;
  const p = candidate.live_preview ?? null;
  const eligible = ticket.phase === "uat" && candidate.status === "verified" && candidate.scope_version === ticket.scope_version;
  if (!eligible && !p) return null;
  const running = p !== null && ["requested", "starting", "ready"].includes(p.status);
  const link = p?.status === "ready" ? previewLink(p.url) : null;
  const d = p?.details ?? {};
  const fixture = d.fixture && typeof d.fixture === "object" && !Array.isArray(d.fixture) ? text(d.fixture.id) : "";
  return (
    <section className="preview" aria-label="Preview untuk UAT" data-preview-status={p?.status ?? "none"}>
      <div className="section-head">
        <h4>Preview untuk UAT</h4>
        {p && <Badge tone={p.status === "ready" ? "good" : p.status === "failed" ? "bad" : p.status === "stopped" ? "neutral" : "info"}>{PREVIEW_LABEL[p.status]}</Badge>}
      </div>
      {!p && <p className="muted">Belum dibuka. Preview dijalankan on-demand dari artefak build yang sudah diuji; tidak harus selalu hidup.</p>}
      {p && (
        <dl className="ids">
          <dt>Target</dt><dd><code title={p.target_digest}>{short(p.target_digest, 16)}</code></dd>
          <dt>Build</dt><dd><code title={text(d.build_digest)}>{short(text(d.build_digest), 16)}</code> · config <code>{short(text(d.config_digest), 10)}</code></dd>
          <dt>Fixture</dt><dd>{fixture || "—"} <span className="muted">(stateless; reset sesuai manifest bukan target baru)</span></dd>
          <dt>Bukti</dt><dd>{Array.isArray(d.evidence_ids) ? d.evidence_ids.length : 0} artefak terpin</dd>
        </dl>
      )}
      {p?.status === "requested" && <p className="notice" role="status">Menunggu supervisor memulai preview. Worker harus berjalan.</p>}
      {link && (
        <p className="preview-link">
          <a href={link} target="_blank" rel="noopener noreferrer">Buka preview di tab baru</a>{" "}
          <span className="muted">({new URL(link).host}: origin terpisah dari kontrol, tanpa cookie sesi, tanpa jaringan keluar)</span>
        </p>
      )}
      {p?.status === "failed" && <p className="notice notice--bad" role="alert">Preview gagal: {p.error || "tidak diketahui"}. Tidak ada proses yang tertinggal.</p>}
      {p && ["stopped", "failed"].includes(p.status) && p.stop_reason && <p className="muted">Berhenti: {STOP_REASON[p.stop_reason] ?? p.stop_reason}.</p>}
      <div className="actions">
        {eligible && !running && p?.status !== "stopping" && (
          <button type="button" className="primary" onClick={() => void command<"startPreview">(`/tickets/${ticket.id}/candidates/${candidate.id}/previews`, {})}>
            {p ? "Buka ulang preview" : "Buka preview"}
          </button>
        )}
        {p && running && (
          <button type="button" onClick={() => void command<"stopPreview">(`/previews/${p.id}/stop`, {})}>Hentikan preview</button>
        )}
        <span className="muted">Persetujuan UAT mengacu pada target dan bukti di atas, bukan pada proses preview yang hidup.</span>
      </div>
    </section>
  );
}

const INTEGRATION_LABEL: Record<NonNullable<Candidate["integration"]>["status"], string> = {
  pending: "Menunggu integrator", done: "Terintegrasi ke accepted", diverged: "Base berubah: perlu rebase dan QA/UAT baru",
  blocked: "Diblokir: perlu pemeriksaan operator",
};

/** Accepted is recorded only after the accepted ref really moved; this shows where that operation stands. */
function IntegrationRow({ candidate: c }: { candidate: Candidate }) {
  const op = c.integration;
  if (!op) return null;
  return (
    <div className="integration" data-integration-status={op.status}>
      <Badge tone={op.status === "done" ? "good" : op.status === "pending" ? "info" : op.status === "blocked" ? "bad" : "warn"}>
        {INTEGRATION_LABEL[op.status]}
      </Badge>{" "}
      <span className="muted">base <code>{short(op.expected_base, 10)}</code> → <code>{short(op.target_sha, 10)}</code>
        {op.observed_tip && <> · ref teramati <code>{short(op.observed_tip, 10)}</code></>}
        {c.integrated_sha && <> · accepted <code>{short(c.integrated_sha, 10)}</code></>}</span>
      {op.reason && <p className="notice notice--bad">{op.reason}</p>}
      {op.evidence_artifact_id && <ArtifactChip id={op.evidence_artifact_id} label="bukti integrasi" />}
    </div>
  );
}

function VerificationRow({ v }: { v: Verification }) {
  return (
    <div className="verification" data-verification={v.id}>
      <Badge tone={v.status === "passed" ? "good" : v.status === "failed" ? "bad" : "warn"}>{v.status === "incomplete" ? "incomplete (belum lulus)" : v.status}</Badge>{" "}
      <span className="muted">
        {Object.entries(v.counts).map(([k, n]) => `${k} ${n}`).join(" · ")}
      </span>
      <div className="coverage">
        {Object.entries(v.uac_coverage).map(([uac, tests]) => <span key={uac}><code>{uac}</code> {tests.length > 0 ? `${tests.length} tes` : <Badge tone="bad">tanpa tes</Badge>}</span>)}
      </div>
      <div className="evidence">{v.evidence_ids.map((id) => <ArtifactChip key={id} id={id} label="bukti" />)}</div>
    </div>
  );
}

function UatSection({ detail }: { detail: TicketDetail }) {
  const { ticket } = detail;
  const target = useMemo(() => {
    for (const c of [...detail.candidates].reverse()) {
      if (c.status === "superseded" || c.status === "rejected" || c.scope_version !== ticket.scope_version) continue;
      const pinnedId = c.preview && typeof c.preview === "object" && !Array.isArray(c.preview)
        ? c.preview.verification_id : undefined;
      const v = c.verifications.find((x) => x.id === pinnedId && (x.status === "passed" || c.qa_waiver?.verification_id === x.id)
        && x.target_digest === c.target_digest);
      if (v && c.target_artifact_id && c.target_digest) return { candidate: c, verification: v };
    }
    return null;
  }, [detail.candidates, ticket.scope_version]);
  const manualUac = (detail.versions.find((v) => v.version === ticket.scope_version)?.uac ?? []).filter((c) => c.mode === "manual" || target?.candidate.qa_waiver?.manual_uac_ids.includes(c.id));
  if (ticket.phase !== "uat") return null;
  if (!target) return <p className="notice">UAT belum bisa diputuskan: tidak ada kandidat dengan verifikasi lulus untuk scope saat ini.</p>;
  const { candidate, verification } = target;
  const identity = JSON.stringify([ticket.id, ticket.scope_version, candidate.id, candidate.target_artifact_id,
    candidate.target_digest, verification.id, candidate.evidence_ids, manualUac]);
  return <UatDecision key={identity} detail={detail} candidate={candidate} verification={verification} manualUac={manualUac} identity={identity} />;
}

function UatDecision({ detail, candidate, verification, manualUac, identity }: {
  detail: TicketDetail; candidate: Candidate; verification: Verification; manualUac: Criterion[]; identity: string;
}) {
  const { command } = useWorkspace();
  const { ticket } = detail;
  const [reason, setReason] = useState("");
  const [manual, setManual] = useState<Set<string>>(new Set());
  const allManual = manualUac.every((c) => manual.has(c.id));
  return (
    <div className="uat" aria-label="Keputusan UAT">
      <h4>Keputusan UAT untuk target <code>{short(candidate.target_digest, 12)}</code></h4>
      <p className="muted">{candidate.qa_waiver ? "QA otomatis belum konklusif. Anda mengambil alih kriteria di bawah untuk target ini." : "Tes otomatis lulus untuk target ini."} Coba alur utama dan kenyamanan aplikasi lewat preview.
        {manualUac.length > 0 && " Centang kriteria manual hanya setelah Anda memeriksanya; hasil otomatis tidak mengisi checklist ini."}</p>
      {manualUac.length > 0 && (
        <fieldset><legend>Konfirmasi UAC manual (Anda yang memeriksa)</legend>
          {manualUac.map((c) => (
            <label key={c.id}><input type="checkbox" checked={manual.has(c.id)}
              onChange={(e) => setManual((prev) => { const n = new Set(prev); e.target.checked ? n.add(c.id) : n.delete(c.id); return n; })} /> {c.id}: {c.text}</label>
          ))}
        </fieldset>
      )}
      <div className="actions">
        <ConfirmButton label="Terima (UAT)" confirmLabel="Ya, terima target ini" disabled={!allManual}
          intent={`${identity}:${ticket.revision}`}
          onConfirm={async () => {
            await command<"uat">(`/tickets/${ticket.id}/uat-decisions`, {
              expected_revision: ticket.revision, candidate_id: candidate.id, scope_version: ticket.scope_version,
              target_artifact_id: candidate.target_artifact_id!, target_digest: candidate.target_digest!,
              verification_id: verification.id, evidence_ids: candidate.evidence_ids, manual_uac_ids: [...manual] });
          }} />
        {!allManual && <span className="muted">Konfirmasi semua UAC manual lebih dulu.</span>}
      </div>
      <form className="ask" onSubmit={async (e) => {
        e.preventDefault();
        const result = await command<"changes">(`/tickets/${ticket.id}/request-changes`, {
          expected_revision: ticket.revision, candidate_id: candidate.id, reason: reason.trim() });
        if (result) setReason("");
      }}>
        <label>Minta perubahan<textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} /></label>
        <button type="submit" disabled={!reason.trim()}>Kirim permintaan perubahan</button>
      </form>
    </div>
  );
}

function Approvals({ detail }: { detail: TicketDetail }) {
  const { command, board } = useWorkspace();
  const { ticket } = detail;
  const [cycles, setCycles] = useState(1);
  const [fingerprint, setFingerprint] = useState("");
  const [reason, setReason] = useState("");
  const needsHuman = typeof ticket.blocker === "object" && ticket.blocker !== null && !Array.isArray(ticket.blocker) && ticket.blocker.reason === "needs_human";
  return (
    <section aria-labelledby="appr-h">
      <h3 id="appr-h">Persetujuan dan waiver</h3>
      {detail.approvals.length === 0 ? <p className="muted">Belum ada persetujuan tercatat.</p> : (
        <ul className="plain">
          {detail.approvals.map((a) => {
            const details = a.details && typeof a.details === "object" && !Array.isArray(a.details) ? a.details : {};
            const waived = a.type.includes("waiver") || details.status === "waived";
            return (
              <li key={a.id} data-approval={a.type}>
                <Badge tone={waived ? "warn" : "good"}>{waived ? "waiver baseline" : a.type}</Badge>{" "}
                {a.scope_version !== null && <span className="muted">scope v{a.scope_version} · </span>}
                {a.target_digest && <span className="muted">target <code>{short(a.target_digest, 12)}</code> · </span>}
                {a.evidence_ids.length > 0 && <span className="muted">{a.evidence_ids.length} bukti</span>}
                {waived && typeof details.reason === "string" && <div className="muted">Alasan: {details.reason}</div>}
              </li>
            );
          })}
        </ul>
      )}
      {needsHuman && (
        <form className="ask" onSubmit={async (e) => {
          e.preventDefault();
          await command<"repair">(`/tickets/${ticket.id}/repair-authorizations`, { expected_revision: ticket.revision, additional_cycles: cycles });
        }}>
          <label>Izinkan siklus perbaikan tambahan
            <input type="number" min={1} max={3} step={1} value={cycles} onChange={(e) => setCycles(Math.min(3, Math.max(1, Math.trunc(Number(e.target.value)) || 1)))} />
          </label>
          <button type="submit">Izinkan {cycles} siklus</button>
        </form>
      )}
      <details>
        <summary>Waiver baseline (keputusan khusus pengguna)</summary>
        <form className="form" onSubmit={async (e) => {
          e.preventDefault();
          if (!board) return;
          const result = await command<"waiver">(`/projects/${board.project.id}/baseline-waivers`, {
            expected_revision: ticket.revision, ticket_id: ticket.id, fingerprint_artifact_id: fingerprint.trim(), reason: reason.trim() });
          if (result) { setFingerprint(""); setReason(""); }
        }}>
          <p className="muted">Waiver mengikat satu fingerprint kegagalan baseline yang sudah tercatat; tidak mengubah hasil QA.</p>
          <label>ID artefak fingerprint<input value={fingerprint} onChange={(e) => setFingerprint(e.target.value)} /></label>
          <label>Alasan<textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} /></label>
          <button type="submit" disabled={!fingerprint.trim() || !reason.trim()}>Catat waiver</button>
        </form>
      </details>
    </section>
  );
}

function Work({ detail }: { detail: TicketDetail }) {
  const { board } = useWorkspace();
  const runs = (board?.runs ?? []).filter((r) => r.ticket_id === detail.ticket.id);
  if (runs.length === 0) return null;
  return (
    <section aria-labelledby="work-h">
      <h3 id="work-h">Status pekerjaan</h3>
      <ul className="plain">
        {runs.map((r) => (
          <li key={r.id}>{senderLabel(`agent:${r.role}`)} · {r.stage} · <Badge tone={r.status === "failed" ? "bad" : r.status.startsWith("waiting") ? "warn" : "info"}>{RUN_STATUS_LABEL[r.status]}</Badge>{" "}
            {r.fake && <FakeBadge />}</li>
        ))}
      </ul>
    </section>
  );
}

function Messages({ messages }: { messages: Message[] }) {
  const visible = messages.filter((m) => !["scope_proposal", "scope_decision"].includes(String(m.metadata.intent)));
  return (
    <section aria-labelledby="msg-h">
      <h3 id="msg-h">Pesan dan handoff</h3>
      {visible.length === 0 ? <p className="muted">Belum ada pesan.</p> : (
        <ol className="messages compact">
          {visible.map((m) => (
            <li key={m.id}><strong>{senderLabel(m.sender)}</strong> <span className="muted">{when(m.created_at)}</span>
              {m.metadata.fake === true && <> <FakeBadge /></>}<p>{m.metadata.intent === "rebase_request"
                ? "Kode utama proyek berubah. Pekerjaan tiket ini perlu digabungkan dengan kode terbaru, kemudian melalui review teknis, QA, dan UAT kembali. Pekerjaan sebelumnya tetap tersimpan."
                : m.body}</p></li>
          ))}
        </ol>
      )}
    </section>
  );
}
