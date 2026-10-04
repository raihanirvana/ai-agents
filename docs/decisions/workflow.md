# Workflow dan approval — DEV-003

Tanggal: 5 Oktober 2026. Pelaksana: Codex. Status implementasi: DONE.
Review awal Claude: **NEEDS_FIX**; perbaikan/recheck implementer dicatat pada
[laporan review](../reviews/DEV-003-review.md). Diff fix belum independent re-review;
checkpoint R4 belum ditutup.
Baseline: `228e0d8327ac85c03d72e9edbb2bc7e4c8daa30b`.
Implementasi dan perbaikan review termasuk assignment DEV-003; commit/push
diotorisasi pengguna setelah perbaikan dan verifikasi.

## Kontrak command

`apps/backend/app/domain/Workflow` memakai database/artifact store DEV-002.
Setiap mutasi memiliki transaksi `BEGIN IMMEDIATE` sendiri: validasi izin,
expected revision, phase, scope/attempt, state, dan event commit bersama.
Exception membatalkan seluruh transaksi, termasuk batch approval. Caller tidak
boleh memanggil command dari transaksi tulis lain atau menahan transaksi selama
operasi model, Git, network, atau process.

`Actor` adalah binding yang **dibuat supervisor/authentication tepercaya**,
bukan field role/id yang diterima dari JSON klien atau keluaran model. API
DEV-008 wajib membentuk actor dari sesi pengguna; worker DEV-004 dari job dan
lease. Actor agent juga diperiksa terhadap owner, role job, lease aktif, project,
generation, scope, stage, dan locator attempt yang disimpan pada tiket.
Capability layanan dipisah menjadi scheduler, builder, verification, integrator;
tidak ada universal `system` atau command `set_status`.

| Aktor | Intent |
| --- | --- |
| Pengguna | Create/edit scope, accept/reject proposal PO, approve scope batch, accept UAT, request changes UAT, cancel, bounded repair extension, approve release, waive baseline. |
| PO dengan lease aktif | Membuat tiket belum approved atau mengusulkan revisi scope pada tiket/job yang sesuai. Proposal tidak langsung mengubah scope. |
| Scheduler | Membaca eligibility dan mengikat job/attempt pada fase yang sesuai; tidak memberikan approval pengguna. |
| Developer | Submit managed commit/candidate dengan receipt broker untuk attempt development yang masih eligible. |
| Builder | Attach build/immutable target dari generation sumber kandidat yang masih valid. |
| Technical lead | Approve technical review atau request changes dengan attempt yang sesuai; mengusulkan penyesuaian melalui pesan, tanpa capability invalidasi contract. |
| QA | Request changes dari attempt QA. QA pass berasal dari layanan verification, bukan ucapan agent. |
| Verification | Membuka UAT dari receipt QA + preview smoke, revalidasi dependency, mencocokkan waiver. Tidak menerima UAT atau memberikan waiver. |
| Integrator | Initialize technical base, menyelesaikan receipt integrasi exact operation/base/tip, rekonsiliasi divergent operation, invalidasi dependency contract. |

Actor UI/UX dapat dikenali sebagai agent ber-lease, tetapi belum memiliki intent
approval khusus. Implementasi soul/model dan penyusunan peran tetap DEV-007.

## State dan histori

Alur utama: `scope_review → ready → development → technical_review → qa → uat
→ integrating → accepted`. Create menyimpan tiket draft dalam transaksi yang sama
sebelum membuat scope version pertama dan masuk scope review.

- Scope document menerima title, description, UAC ber-ID unik (automated/manual),
  dependency ticket IDs, dan optional `reverts_candidate_id`. Setiap edit membuat
  `TicketVersion` immutable baru, beserta snapshot dependency/revert dan content
  digest. Scope lama, proposal/keputusan, approval, dan event tetap tersimpan.
- Batch scope approval memeriksa semua item/revision/version/DAG sebelum menulis
  approval. Setiap batch punya ID yang sama untuk semua item. Ready belum tentu
  eligible: approval versi kini dan semua dependency Accepted/integrated wajib ada.
- Dependency menyimpan accepted scope version, candidate ID, integration SHA.
  Technical review/QA/UAT upstream belum memenuhi dependency. Revert selalu tiket
  baru yang menunjuk accepted candidate; tidak menghapus histori upstream.
- Contract change hanya oleh integrator tepercaya, menginvalidasi edge di jalur
  downstream transitif; edge ke upstream lain tidak berubah. Pin upstream tetap ada,
  tetapi state menjadi `needs_revalidation`; attempt/candidate lama dicabut.
  Receipt required checks harus menyebut ID permintaan revalidasi, scope,
  upstream candidate, dan accepted base saat ini. Bukti perubahan sebelumnya
  tidak bisa dipakai ulang. Receipt disimpan sebagai lampiran pesan immutable
  sehingga tetap terpin walaupun scope/dependency berikutnya berubah.
- Required checks dengan UAC tetap dapat revalidate scope yang sudah approved.
  UAC berubah harus membuat scope version dan approval pengguna baru **serta**
  required checks pada scope/base baru. Edit judul atau UAC tidak menghapus pending
  revalidation pada dependency yang tetap dipakai: pin historis dipertahankan dan
  request ID diganti, sehingga receipt scope lama ditolak. Approval scope bukan
  pengganti checks. `uac_changed=False` pada receipt berarti tidak ada perubahan
  UAC lebih lanjut dari scope version yang disebut receipt.
  Dependency yang dihapus secara eksplisit dari scope baru tidak lagi menjadi gate.
  Upstream menyimpan riwayat contract change pada metadata workflow: tiket baru
  atau edge yang ditambahkan kembali tidak dapat kehilangan kebutuhan checks.
- Edit scope, cancel, request changes, dan contract invalidation meng-cancel
  semua job aktif tiket, menaikkan generation, menghapus lease, dan menerbitkan
  `job.cancellation_requested`. Usage yang sudah dicatat tetap utuh.
  DEV-004 wajib mengonsumsi event ini untuk revoke credential dan stop proses;
  DEV-003 menegakkan otorisasi/state persisten, belum menjalankan cleanup proses.
- Repair dihitung kumulatif pada scope yang sama. Tiga siklus berarti tiga putaran
  review/QA/UAT yang berakhir request-changes, termasuk putaran awal: dua perbaikan
  otomatis dapat berjalan; request-changes ketiga memblokir putaran keempat.
  Ini mempertahankan batas implementasi awal, tidak menambah budget diam-diam.
  Counter `repair_cycles >= repair_limit` menolak eligibility/bind secara langsung,
  walaupun metadata blocker hilang. Revalidasi dependency tidak memberi budget
  repair; otorisasi repair tidak mengesahkan dependency yang belum dicek. Blocker
  `needs_human` dipertahankan ketika ada contract change. Pengguna dapat menambah 1–3
  siklus per keputusan eksplisit; counter lama tidak direset. Ini bukan pengganti
  request/token/time budget worker DEV-004.
- Accepted/Cancelled tidak boleh diedit atau dibatalkan kembali. Integrating
  harus direkonsiliasi dahulu, termasuk sebelum invalidasi contract downstream;
  transaksi invalidasi tersebut rollback seluruhnya bila menemukan Integrating.
  Contract change mencatat event `ticket.dependency_followup_required` dan edge
  historis needs-revalidation pada Accepted/Cancelled, tanpa memasang blocker atau
  mengubah kandidat/fase/workflow penerimaannya. Perubahan kode memakai tiket baru;
  tiket baru yang bergantung pada accepted upstream dengan kontrak terdampak juga
  wajib menjalankan required checks sebelum eligible.

## Target dan evidence

`submit_candidate` memerlukan `commit_artifact_id` **dan** `commit_receipt_id`.
Git commit artifact tetap deduplikasi per project/SHA pada DEV-002. Provenance
berada pada file artifact `report` immutable per attempt, dengan metadata producer
`broker` dan object berikut (tidak menerima field tambahan):

```json
{
  "kind": "candidate_commit",
  "project_id": "project-id",
  "ticket_id": "ticket-id",
  "commit_artifact_id": "git-artifact-id",
  "commit_sha": "managed-commit-sha",
  "base_sha": "accepted-base-sha",
  "source_attempt": {"job_id": "job-id", "generation": 1, "scope_version": 1}
}
```

Receipt harus cocok dengan attempt aktif/terikat, project/ticket, commit dan base
saat submit. SHA sama boleh dipakai pada attempt baru hanya dengan receipt broker
baru yang sah; metadata artifact SHA lama tidak ditimpa. Receipt dilampirkan ke
pesan submission immutable sehingga tetap terpin sesudah kandidat disupersede.
Broker DEV-005/010 harus memverifikasi managed attempt ref/Git dan menerbitkan
receipt ini dari supervisor, bukan menerima deklarasi provenance dari model.
Begitu pula metadata producer pada receipt builder/verification: hanya layanan
tepercaya yang boleh menulis DB/artifact kontrol; field producer pada upload atau
file target tidak boleh dipercaya sebagai bukti asal.

Candidate target manifest mengikat project/ticket/candidate/scope, source/base
SHA, build artifact ID, dan digest build, runner manifest, toolchain, config,
fixture, migration. Build record harus cocok. Builder receipt mengikat source
job/generation; late build dari generation yang dicabut ditolak.

Verification mengikat target artifact ID + digest, suite digest yang sama dengan
runner manifest, snapshot commit/build/context, dan job/generation QA. Pass
memerlukan non-fake, tidak ada infrastructure failure, mandatory IDs nonempty
yang seluruhnya executed, discovered=executed=passed > 0, failed=skipped=0,
command argv + integer exit 0, coverage automated UAC, dan evidence artifact
dari layanan verification. Preview smoke harus cocok dengan target tersebut.
JSON receipt non-object/malformed gagal tertutup sebagai domain error.

UAT pengguna menyebut exact candidate, scope, target, verification, evidence IDs
yang ditampilkan, serta konfirmasi semua manual UAC. Artifact file diperiksa
checksum saat dibaca; flag available saja tidak cukup. Accepted base yang sudah
bergerak menolak approval candidate lama. SHA sama dengan build/config baru
tetap menghasilkan target lain dan membutuhkan QA/UAT baru.

Accept UAT hanya membuat integration operation `pending` dan fase Integrating.
`finish_integration` hanya menerima capability integrator dengan operation ID,
expected base, serta observed tip tepat. Tidak ada perubahan Git di command ini:
DEV-012 harus melakukan Git CAS dan recovery, lalu mengirim receipt tepercaya.
Divergent receipt mencatat base baru, mencabut candidate/attempt, dan kembali ke
development; approval lama tidak dibawa ke candidate baru. Duplicate receipt
dengan revision lama ditolak tanpa mengulang mutasi; idempotent reconciliation
eksternal tetap kewajiban DEV-012.

Release mempunyai snapshot build/target/evidence dan approval pengguna sendiri.
Approval menuntut accepted tip kini dan receipt combined verification yang
terikat ke exact target/digest/tip, non-fake, execution/counts/mandatory IDs lengkap.
Approval tiket tidak mengesahkan release. Penyusunan release snapshot, combined
regression runner, export dan deployment adalah DEV-014.

Waiver adalah approval pengguna bertarget fingerprint report baseline dari
verification. Harus spesifik ticket/scope/base/test/failure signature/environment
digest; kategori UAC dan infrastructure failure ditolak. `details.status=waived`
tersedia untuk API/UI. Matching fingerprint tidak otomatis membuat QA pass:
runner DEV-010/013 harus melaporkan waived terpisah dan menjaga mandatory UAC.

## Migrasi dan cara menjalankan

Migrasi `0002_workflow.py` menambahkan object JSON `projects.workflow`,
`tickets.workflow`, dan immutable `ticket_versions.scope`. Native SQLite ADD/DROP
COLUMN mempertahankan trigger revisi 0001. Database berisi data diuji upgrade,
downgrade, re-upgrade; trigger, revision/immutability, cursor, JSON checks tetap
berfungsi. Kolom lama tidak ditulis ulang. Tiket legacy mendapat metadata `{}`;
job runtime dan accepted tip perlu diikat oleh supervisor/integrator, bukan
disimpulkan diam-diam dari skeleton data.

```sh
cd apps/backend
./.venv/bin/python -m app.persistence upgrade --db /local/path/workflow.sqlite3
./.venv/bin/python -m app.persistence check --db /local/path/workflow.sqlite3
./.venv/bin/python -m pytest tests/domain tests/persistence -q
```

Windows menggunakan `.venv/Scripts/python.exe`; DB dan artifact path lokal harus
gitignored. API/worker/GUI skeleton belum diwire ke command service ini.

## Pemetaan delapan AC dan verifikasi

| AC DEV-003 (urutan backlog) | Implementasi / bukti di `apps/backend/tests/domain/` |
| --- | --- |
| 1 Scope approval pengguna/version dan eligibility | `approve_scope`, `_eligible`; `test_only_user_can_approve_scope`, `test_batch_stale_version_revision_rolls_back_all`, alur lengkap. |
| 2 Batch atomik, revision, DAG, dependency Accepted | `test_workflow.py` batch/dependency/cycle; `test_transactions.py` fault rollback dan dua writer bersamaan. |
| 3 Dependency pins, revert, contract revalidation/UAC | `test_dependencies.py` transitif, repeated contract receipt, required checks, UAC edit; `test_review_regressions.py` hanya edge terdampak, histori Accepted, pending checks melintasi edit; revert tiket baru. |
| 4 Proposal PO/keputusan/edit pengguna dan revoke scope | `test_workflow.py` proposal accept/reject dan scope change; reopen DB pada `test_transactions.py` membuktikan generation/history/usage persisten. |
| 5 Specific actor/intent; tanpa status setter | Role matrix scope, cross-project, expired lease, borrowed job, stale generation, QA promotion, builder/QA receipt terlambat; regression integrator-only dan provenance commit per receipt/attempt. |
| 6 Exact UAT/release target/evidence; same SHA rebuild | `test_evidence.py` identitas UAT/manual UAC, rebuilt config, source generation, QA suite, smoke, corrupt artifact, release proof/frozen target. |
| 7 User-only waiver exact fingerprint dan explicit waived | `test_evidence.py` waiver role, UAC/infrastructure/base/scope/fingerprint dan matching. |
| 8 Cancel, repair limit, Accepted baru, Integrating reconciliation | `test_workflow.py` terminal phases/cancel/repair/revert; divergence/Integrating rollback; regression counter repair independen dan urutan revalidasi/otorisasi repair. |

Test domain memakai DB SQLite/file artifact asli dan **synthetic trusted contract
receipts**, bukan hasil provider/QA harness nyata. Receipt fixture berlabel
`contract_fixture`; `fake_provider=False` pada fixture hanya memenuhi kontrak
input layanan tepercaya, bukan klaim ada agent nyata yang dijalankan. Tidak ada
provider call, deployment, export, push, atau approval produk nyata di tes ini.

Hasil aktual dan perintah verifikasi final dicatat pada log DEV-003 di backlog.
Diff review termasuk semua file baru: `data/dev003/review.patch` (gitignored).
Reviewer harus membaca file aktual/diff dan memverifikasi sendiri; R4 belum ditutup.
