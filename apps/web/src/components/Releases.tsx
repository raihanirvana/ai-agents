import { useState } from "react";
import type { Release } from "../../../../contracts/api/types";
import { ACTIVE_RUN, short, text, when } from "../format";
import { api } from "../api/instance";
import { useWorkspace } from "../workspace";
import { Badge, ConfirmButton } from "./ui";

const STATUS: Record<Release["status"], { label: string; tone: "info" | "good" | "bad" | "warn" | "neutral" }> = {
  draft: { label: "Draf: menunggu approval Anda", tone: "info" },
  approved: { label: "Disetujui (belum diekspor, belum di-deploy)", tone: "good" },
  exported: { label: "Diekspor lokal (belum di-push, belum di-deploy)", tone: "good" },
  deployed: { label: "Di-deploy", tone: "good" },
  failed: { label: "Gagal / dibatalkan", tone: "bad" },
};

/** Release: freeze -> one combined verification target -> your approval -> explicit local export. Never "deployed" here. */
export default function Releases() {
  const { board, command } = useWorkspace();
  if (!board) return <p className="muted pad">Memuat release…</p>;
  const project = board.project;
  const running = board.runs.filter((r) => r.stage === "release" && ACTIVE_RUN.has(r.status));
  const accepted = board.tickets.filter((t) => t.phase === "accepted").length;
  const hasDraft = board.releases.some((r) => r.status === "draft");
  return (
    <div className="releases">
      <section aria-labelledby="rel-h">
        <h3 id="rel-h">Release</h3>
        <p className="muted">
          Membekukan accepted tip dan tiket Accepted yang belum masuk release. Verifikasi (build, repo tests, regression browser
          gabungan) berjalan pada satu target. Tiket yang diterima setelah freeze masuk release berikutnya.
        </p>
        {running.map((r) => (
          <p key={r.id} className="notice" role="status" data-release-run={r.status}>
            Operasi release {r.status === "queued" ? "antre" : "berjalan"}. Integrasi tiket baru ditahan sampai selesai. Worker harus berjalan.
          </p>
        ))}
        <div className="actions">
          <ConfirmButton label={`Bekukan dan verifikasi release (${accepted} tiket Accepted)`} confirmLabel="Ya, bekukan"
            disabled={accepted === 0 || hasDraft || running.length > 0}
            onConfirm={async () => { await command<"freezeRelease">(`/projects/${project.id}/releases`, { expected_revision: project.revision }); }} />
          {hasDraft && <span className="muted">Setujui atau batalkan draf yang ada lebih dulu.</span>}
        </div>
      </section>
      {board.releases.length === 0 && <p className="muted">Belum ada release.</p>}
      {board.releases.map((release) => <ReleaseCard key={release.id} release={release} />)}
    </div>
  );
}

function ReleaseCard({ release }: { release: Release }) {
  const { board, command, selectTicket } = useWorkspace();
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [diffReviewed, setDiffReviewed] = useState(false);
  const reviewDiffs = release.technical_review_evidence_ids ?? [];
  const mode = board?.project.mode;
  const items = release.scope.flatMap((e) => e.uac.filter((u) => release.checklist.includes(`${e.ticket_id}:${u.id}`)).map((u) => ({
    key: `${e.ticket_id}:${u.id}`, label: `#${e.number} ${u.id}: ${u.text}` })));
  const allConfirmed = release.checklist.every((k) => confirmed.has(k));
  const state = STATUS[release.status];
  const [report, ...rest] = release.evidence_ids;
  const exported = release.export;
  return (
    <article className="release" data-release={release.id} data-status={release.status}>
      <div className="section-head">
        <h4>Release <code>{short(release.id, 8)}</code></h4>
        <Badge tone={state.tone}>{state.label}</Badge>
      </div>
      <dl className="ids">
        <dt>Accepted tip</dt><dd><code title={release.accepted_tip}>{short(release.accepted_tip, 12)}</code></dd>
        <dt>Target</dt><dd><code title={release.target_digest}>{short(release.target_digest, 16)}</code> · build <code>{short(release.build_artifact_id, 8)}</code></dd>
        <dt>Bukti</dt>
        <dd>
          <a href={api.artifactUrl(report)} target="_blank" rel="noreferrer">Laporan verifikasi gabungan</a>
          <span className="muted"> · {rest.length} artefak lain terpin (suite, gate, log, screenshot)</span>
        </dd>
        <dt>Dibuat</dt><dd>{when(release.created_at)}</dd>
      </dl>
      <h5>Isi release ({release.scope.length} tiket)</h5>
      <ul className="plain">
        {release.scope.map((e) => (
          <li key={e.ticket_id}>
            <button type="button" className="link" onClick={() => selectTicket(e.ticket_id)}>#{e.number} {e.title}</button>{" "}
            <span className="muted">scope v{e.scope_version} · <code>{short(e.integrated_sha, 8)}</code></span>
            {e.affected_by_sync === true && <> <Badge tone="warn" title={(e.overlap_files ?? []).join(", ")}>terdampak sinkronisasi</Badge></>}
          </li>
        ))}
      </ul>
      {release.status === "draft" && (
        <div className="approve">
          {reviewDiffs.length > 0 && <fieldset>
            <legend>Review teknis sinkronisasi untuk target ini</legend>
            <p><a href={api.artifactUrl(reviewDiffs[0])} target="_blank" rel="noreferrer">Diff perubahan sumber</a> · <a href={api.artifactUrl(reviewDiffs[1])} target="_blank" rel="noreferrer">Diff gabungan release</a></p>
            <label><input type="checkbox" checked={diffReviewed} onChange={e => setDiffReviewed(e.target.checked)} /> Saya telah meninjau kedua diff dan menyetujui perubahan kode gabungan pada target ini.</label>
          </fieldset>}
          {items.length > 0 ? (
            <fieldset><legend>Checklist UAC manual untuk release ini (Anda yang memeriksa)</legend>
              {items.map((item) => (
                <label key={item.key}><input type="checkbox" checked={confirmed.has(item.key)}
                  onChange={(e) => setConfirmed((prev) => { const n = new Set(prev); e.target.checked ? n.add(item.key) : n.delete(item.key); return n; })} /> {item.label}</label>
              ))}
            </fieldset>
          ) : <p className="muted">Tidak ada UAC manual yang harus dikonfirmasi untuk release ini.</p>}
          <div className="actions">
            <ConfirmButton label="Setujui release ini" confirmLabel="Ya, setujui target ini" disabled={!allConfirmed || (reviewDiffs.length > 0 && !diffReviewed)}
              onConfirm={async () => {
                await command<"approveRelease">(`/releases/${release.id}/decisions`, { expected_revision: release.revision,
                  target_artifact_id: release.target_artifact_id, target_digest: release.target_digest,
                  evidence_ids: release.evidence_ids, manual_uac_ids: [...confirmed],
                  ...(reviewDiffs.length > 0 ? { reviewed_diff_ids: reviewDiffs } : {}) });
              }} />
            <ConfirmButton tone="danger" label="Batalkan draf" confirmLabel="Ya, batalkan"
              onConfirm={async () => { await command<"discardRelease">(`/releases/${release.id}/discard`, { expected_revision: release.revision }); }} />
            {mode === "existing" && (
              <button type="button" onClick={() => void command<"syncRelease">(`/releases/${release.id}/sync`, { expected_revision: release.revision })}>
                Sinkronkan ke HEAD repo sumber
              </button>
            )}
          </div>
          <p className="muted">Approval hanya untuk target dan bukti di atas. Build atau konfigurasi yang berubah membutuhkan target dan approval baru.</p>
        </div>
      )}
      {release.status === "approved" && (
        <div className="actions">
          <ConfirmButton label="Ekspor lokal (patch + bundle)" confirmLabel="Ya, ekspor"
            onConfirm={async () => { await command<"exportRelease">(`/releases/${release.id}/export`, { expected_revision: release.revision }); }} />
          {mode === "existing" && (
            <button type="button" onClick={() => void command<"syncRelease">(`/releases/${release.id}/sync`, { expected_revision: release.revision })}>
              Sinkronkan ke HEAD repo sumber
            </button>
          )}
          <span className="muted">Ekspor tidak melakukan push, PR, atau deployment. Ditolak bila repo sumber berubah sejak release dibangun.</span>
        </div>
      )}
      {exported && (
        <div className="export" aria-label="Hasil ekspor">
          <p>Basis <code>{short(exported.base_sha, 10)}</code> → tip <code>{short(exported.tip, 10)}</code> · cabang <code>{exported.branch}</code></p>
          <p>
            <a href={api.artifactUrl(exported.patch_artifact_id)} target="_blank" rel="noreferrer">Unduh patch</a> ·{" "}
            <a href={api.artifactUrl(exported.bundle_artifact_id)} target="_blank" rel="noreferrer">Unduh Git bundle</a>
          </p>
          <p className="muted">{exported.how_to_use}</p>
          <p className="muted">Pushed: {exported.pushed ? "ya" : "tidak"} · Deployed: {exported.deployed ? "ya" : "tidak"}</p>
        </div>
      )}
      {release.status === "failed" && <p className="notice notice--bad">Verifikasi tidak lulus atau draf dibatalkan. Buka laporan verifikasi untuk alasannya; release gagal tidak dapat disetujui.</p>}
      {text(release.deployment) && <p className="muted">Deployment: {text(release.deployment)}</p>}
    </article>
  );
}
