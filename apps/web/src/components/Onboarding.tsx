import { useState } from 'react';
import { useWorkspace } from '../workspace';
import { api } from '../api/instance';

export default function Onboarding() {
  const { board, command, busy } = useWorkspace();
  const [manifest, setManifest] = useState('');
  const [patch, setPatch] = useState('');
  const [sha, setSha] = useState('');
  const [localError, setLocalError] = useState('');
  if (!board || board.project.mode !== 'existing') return null;
  const p = board.project;
  const detail = p.onboarding_detail;
  const active = board.runs.some(r => r.stage === 'onboarding' && ['queued', 'running', 'waiting_input', 'waiting_quota'].includes(r.status));
  return <details className="brief" open={!p.accepted_tip}>
    <summary>Onboarding repo existing — {p.onboarding}</summary>
    <p>Repo sumber hanya dibaca. Install/build/test/start berjalan di clone managed dalam sandbox. Runner tersedia: static React/Vite, package-lock npm, flat Node TAP, migrations none.</p>
    {detail?.blocker && <p role="alert">Blocker: {detail.blocker}</p>}
    {detail?.source_sha && <p>Source SHA: <code>{detail.source_sha}</code> · Baseline: <code>{detail.baseline_sha}</code></p>}
    {detail?.dirty && <div><p>Repo sumber memiliki perubahan lokal. {detail.patch_applied ? 'Patch eksplisit telah diterapkan.' : 'Perubahan lokal tidak dibawa ke clone.'}</p><pre>{detail.dirty_status?.join('\n')}</pre>{(detail.dirty_total ?? 0) > (detail.dirty_status?.length ?? 0) && <p>… dan {(detail.dirty_total ?? 0) - (detail.dirty_status?.length ?? 0)} perubahan lain tidak ditampilkan.</p>}</div>}
    {detail?.required_checks && <p>Required baseline checks: {detail.required_checks}. Failure baseline perlu perbaikan atau waiver pengguna yang cocok per scope; UAC dan infrastructure failure tetap wajib.</p>}
    {detail?.report_artifact_id && <p><a href={api.artifactUrl(detail.report_artifact_id)} target="_blank" rel="noreferrer">Laporan baseline dan bukti commands</a></p>}
    {active && <p role="status">Job onboarding aktif; worker onboarding/pipeline diperlukan. Lihat Aktivitas untuk stop/log.</p>}
    {!p.accepted_tip && !active && <form className="form" onSubmit={async e => {
      e.preventDefault(); setLocalError('');
      try {
        const parsed: unknown = JSON.parse(manifest);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('Manifest harus JSON object.');
        await command<'onboarding'>(`/projects/${p.id}/onboarding`, { expected_revision: p.revision,
          manifest: parsed as Record<string, unknown>, patch: patch.trim() ? patch : null,
          source_sha: sha.trim() || null });
      } catch (error) { setLocalError(error instanceof Error ? error.message : String(error)); }
    }}>
      <label>Runner manifest JSON<textarea rows={8} value={manifest} onChange={e => setManifest(e.target.value)} required placeholder="Gunakan examples/dev010/runner-manifest.json" /></label>
      <label>Source HEAD SHA (wajib bila memilih patch)<input value={sha} onChange={e => setSha(e.target.value)} maxLength={40} required={!!patch.trim()} /></label>
      <label>Patch eksplisit (opsional)<textarea rows={4} value={patch} onChange={e => setPatch(e.target.value)} placeholder="Tempel git diff --binary untuk perubahan yang ingin dibawa. Untracked file harus dimasukkan secara eksplisit." /></label>
      {localError && <p role="alert">{localError}</p>}
      <button type="submit" className="primary" disabled={busy}>Mulai onboarding clone managed{patch.trim() ? ' dengan patch terpilih' : ''}</button>
    </form>}
  </details>;
}
