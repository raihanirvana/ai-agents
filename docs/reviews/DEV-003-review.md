# DEV-003 — code review

Tanggal: 5 Oktober 2026. Reviewer: Claude (Opus 5.5). Implementer: Codex.
Verdict review awal: **NEEDS_FIX**. DEV-003 dibuka kembali menjadi **IN_PROGRESS** karena
AC5 (izin/intensi spesifik) dan AC8 (batas repair) belum terpenuhi. Ini review
DEV-003, bukan penutupan checkpoint R4 yang juga mencakup DEV-004.

Perbaikan berikutnya oleh implementer ada pada bagian recheck di bawah.
Verdict Claude dan temuan pada snapshot awal tetap dipertahankan sebagai histori;
recheck implementer bukan independent re-review atas diff fix.

Baseline: `228e0d8327ac85c03d72e9edbb2bc7e4c8daa30b`.
Staged tree yang diperiksa: `6b134fc855fe5e21107b91427e9c4697bc7b778c`.
Scope: 15 file staged (`app/domain/*`, migrasi `0002_workflow.py`, tambahan
`models.py`, `tests/domain/*`, `docs/decisions/workflow.md`, README, backlog).
Seluruh `service.py`, `evidence.py`, `types.py`, migrasi, fixture dan test dibaca.
Implementasi dan staging tidak diubah selama review.

## Yang sudah baik

- Tidak ada status setter: setiap transisi punya command, aktor, fase, revision dan
  event dalam satu transaksi `BEGIN IMMEDIATE`; batch approval benar-benar atomik.
- Trigger storage DEV-002 dimanfaatkan sebagai pengaman kedua (verified/accepted/
  release butuh bukti dan approval target yang sama); domain menambah validasi isi
  receipt, checksum file, UAC coverage, smoke preview dan accepted base.
- Migrasi 0002 memakai native `ADD COLUMN` sehingga trigger 0001 tetap utuh.
- Fencing attempt (job/lease/generation/stage/locator di tiket) dan receipt builder
  yang terikat source attempt menolak hasil terlambat dengan benar.

## Temuan terkonfirmasi

### R003-01 — P1: batas repair (`needs_human`) bisa dilewati tanpa keputusan pengguna

Lokasi: `apps/backend/app/domain/service.py:512` (`contract_changed`) dan `:546`
(`revalidate_dependency`).
`contract_changed` menimpa `blocker` downstream dengan `dependency_revalidation`
tanpa memeriksa blocker yang sudah ada. `revalidate_dependency` lalu menghapus
blocker apa pun (`blocker=None`) begitu dependency terpenuhi. Blocker `needs_human`
dari batas repair hilang, padahal `repair_cycles` tetap 3 dan `repair_limit` tetap 3;
`authorize_repair` (satu-satunya jalur pengguna) tidak pernah dipanggil.

Reproduksi: upstream accepted; tiket downstream approved; tiga kali submit lalu
`request_changes` oleh lead sampai `blocker.reason == "needs_human"`; integrator
memanggil `contract_changed(upstream)`; verification mengirim receipt revalidasi
valid. Hasil: `blocker=None`, `repair_cycles=3`, `limit=3`, `eligible=True`, dan
scheduler berhasil `bind_attempt` development **ke-4**. Karena R003-02, pemicu
contract change juga bisa berasal dari agent technical-lead.

Perbaikan yang diperlukan: blocker harus multi-alasan (atau minimal: contract change
tidak menimpa `needs_human`, dan revalidasi hanya menghapus blocker
`dependency_revalidation`). `_eligible`/`bind_attempt` sebaiknya juga menolak
`repair_cycles >= repair_limit` secara langsung, bukan hanya lewat blocker.
Tambahkan regresi: repair limit + contract change + revalidasi tetap tidak eligible
sampai `authorize_repair`.

### R003-02 — P2: `contract_changed` oleh technical-lead tidak dibatasi ke attempt-nya

Lokasi: `service.py:488-490`.
`_permit` untuk role agent hanya memeriksa lease/generation/role job milik aktor,
bukan keterkaitan job itu dengan tiket upstream atau downstream. Lead mana pun
yang sedang punya job review aktif, untuk tiket apa pun dalam proyek, dapat
menginvalidasi seluruh downstream transitif: job dibatalkan, kandidat di-supersede
(termasuk yang sedang di **UAT** menunggu pengguna), fase dikembalikan ke development.

Reproduksi: upstream accepted; downstream sampai UAT; lead dengan job technical
review pada tiket lain yang tidak terkait memanggil `contract_changed(upstream)`.
Hasil: downstream `phase=development`, kandidat UAT `superseded`.

Ini bertentangan dengan AC5 ("Command memiliki izin/intensi spesifik") dan dengan
pola command lain yang mengikat agent ke attempt tiketnya (`_attempt`). Perbaikan:
untuk technical-lead wajibkan attempt yang terikat ke tiket downstream terkait
(atau batasi `contract_changed` ke capability integrator/verification yang membawa
bukti perubahan kontrak), dan catat aktor/attempt di payload revalidasi.

### R003-03 — P2: commit kandidat tidak terikat ke attempt/scope yang mengirimnya

Lokasi: `service.py:302-311` (`submit_candidate`).
Developer mengirim `commit_artifact_id` (dari tool call model). Domain hanya
memeriksa artifact `git_commit` milik proyek yang available; tidak ada pengecekan
provenance (producer broker, job/generation/scope version attempt). Commit dari
attempt lama, attempt yang sudah dicabut, scope version lama, atau milik tiket lain
dapat menjadi kandidat versi scope saat ini.

Reproduksi: tiket v1 dengan attempt developer A; broker mencatat commit untuk A
(`source_attempt.scope_version=1`); pengguna mengubah UAC (v2) dan menyetujuinya;
attempt developer baru B mengirim commit milik A. Hasil: kandidat
`scope_version=2` dibuat dari commit attempt v1.

AGENTS.md: "Hasil attempt lama tidak boleh mengubah state saat ini", dan AC4
"membatalkan otorisasi attempt lama". Pola yang sama sudah diterapkan benar untuk
target build (`attach_target` memeriksa `meta.source_attempt`). Perbaikan: wajibkan
`commit.meta.producer == "broker"` dan `source_attempt == attempt`
(job/generation/scope_version) saat submit, atau terima receipt broker yang terikat
ke attempt sebagai pengganti artifact ID bebas. Catatan: `put_git_commit` DEV-002
men-dedupe per SHA, sehingga provenance yang sah perlu disimpan per receipt/attempt.

### R003-04 — P3: contract change menandai edge dependency yang tidak terkait

Lokasi: `service.py:505-507`.
Untuk setiap downstream, semua dependency tiket itu ditandai `needs_revalidation`,
termasuk edge ke upstream lain yang kontraknya tidak berubah.

Reproduksi: downstream bergantung pada U1 dan U2 (keduanya accepted);
`contract_changed(U1)`. Hasil: edge ke U1 dan edge ke **U2** sama-sama
`needs_revalidation`; U2 butuh receipt revalidasi tanpa ada perubahan.

Perbaikan: untuk downstream langsung, tandai hanya edge ke upstream yang berubah;
untuk downstream transitif, tandai edge ke tiket di jalur invalidasi.

### R003-05 — P3: downstream yang sudah Accepted mendapat blocker yang tidak bisa diselesaikan

Lokasi: `service.py:509-512` dan `:519`.
`contract_changed` memasang blocker `dependency_revalidation` pada downstream berfase
`accepted` (fase dipertahankan), tetapi `revalidate_dependency` menolak fase
`accepted` (`Conflict: intent is invalid in phase accepted`). Tiket Accepted
tampil terblokir selamanya tanpa jalur penyelesaian. Arsitektur menyebut perubahan
pada kode Accepted lewat tiket baru, jadi tiket Accepted sebaiknya tidak diberi
blocker (cukup event/needs_revalidation pada edge untuk downstream berikutnya),
atau diberi jalur resolusi eksplisit.

### R003-06 — P3: input malformed menghasilkan error non-domain

Lokasi: `service.py:143` (`create_ticket`).
`document.get("title")` dievaluasi sebelum `_scope` memvalidasi tipe, sehingga
dokumen non-object menghasilkan `AttributeError` alih-alih `Invalid`; di API
DEV-008 ini menjadi HTTP 500. Terkait: `approve_review` sebelum target dipasang
memang gagal tertutup (`Invalid`), tetapi lewat `session.get(Artifact, None)` yang
memicu `SAWarning` "fully NULL primary key" (bisa menjadi error di versi SQLAlchemy
berikutnya); sebaiknya cek `target_artifact_id` eksplisit.

## Observasi (bukan temuan)

- Edit scope apa pun yang disetujui pengguna mengganti dependency (baris lama dihapus,
  dibuat ulang `waiting`) sehingga `needs_revalidation` + blocker hilang tanpa
  required checks. `test_uac_change_requires_new_scope_approval` hanya mengubah
  judul, bukan UAC. Bila maksud AC3 adalah required checks tetap wajib meskipun
  scope diedit, ini perlu diperketat; bila approval scope baru dianggap cukup,
  sebaiknya dinyatakan eksplisit di `workflow.md` dan nama test disesuaikan.
- Batas repair memblokir pada `request_changes` ke-3 (hanya dua siklus perbaikan
  dijalankan sebelum `needs_human`). Konsisten dengan `workflow.md` ("setelah tiga
  siklus"), tetapi ARCHITECTURE "maksimal tiga siklus review/QA/perbaikan" bisa
  dibaca sebagai tiga perbaikan. Perlu dikonfirmasi pengguna, bukan diubah diam-diam.

## Verifikasi

Dari `apps/backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/domain tests/persistence -q
```

Hasil aktual Windows: **213 passed / 1 skipped** (symlink tidak bisa dibuat host).
WSL Ubuntu (`/root/aiagent-dev002-venv`): **214 passed**. Suite lengkap WSL dengan
Docker (354 tests menurut implementer) tidak dijalankan ulang dalam review ini.
`git diff --cached --check` lulus.

Reproduksi memakai SQLite/migrasi/ArtifactStore asli lewat fixture `World` test
domain, database dan file sementara; tidak memakai provider, model, atau data
aplikasi existing. Probe disimpan di `data/dev003/review-probes-dev003.py`
(gitignored). Cara menjalankan: salin ke `apps/backend/tests/domain/` lalu
`python -m pytest tests/domain/review-probes-dev003.py -q -s` (pytest menerima path
file eksplisit), dan hapus kembali setelahnya. Tidak ada perubahan kode, commit/push,
atau wiring API/worker.

## Pemetaan AC

| AC DEV-003 | Penilaian review |
| --- | --- |
| 1 Approval pengguna/version dan eligibility | Terpenuhi untuk jalur yang diuji; R003-01 membuat eligibility bisa kembali tanpa keputusan pengguna |
| 2 Batch atomik, revision, DAG, dependency Accepted | Terpenuhi; batch rollback dan concurrent approvers lulus |
| 3 Dependency pins, revert, contract revalidation | Sebagian: over-invalidation (R003-04), Accepted terblokir permanen (R003-05), lihat observasi edit scope |
| 4 Proposal PO/edit pengguna, revoke attempt lama | Sebagian: commit attempt lama bisa menjadi kandidat versi baru (R003-03) |
| 5 Izin/intensi spesifik, tanpa status setter | **Belum terpenuhi**: lead tak terkait dapat menginvalidasi downstream (R003-02); provenance commit (R003-03) |
| 6 UAT/release target/evidence, rebuild SHA sama | Terpenuhi; target, suite, smoke, manual UAC, base dan release receipt terikat |
| 7 Waiver pengguna dengan fingerprint spesifik | Terpenuhi untuk kasus yang diuji |
| 8 Cancel, repair limit, Accepted baru, Integrating | **Belum terpenuhi**: repair limit dapat dilewati (R003-01); Integrating/cancel benar |

Perlu memperbaiki R003-01 sampai R003-03 dan menambah regresi sebelum DEV-003
kembali DONE atau dipakai sebagai fondasi DEV-004. R003-04 sampai R003-06 sebaiknya
ikut diperbaiki pada putaran yang sama. Dua observasi perlu keputusan pengguna.

## Perbaikan dan recheck implementer — Codex, 5 Oktober 2026

Pengguna menginstruksikan memperbaiki hasil review lalu commit/push. Perubahan
diperiksa terhadap ARCHITECTURE §4 dan MVP-BLUEPRINT bagian workflow.
Status di tabel berikut adalah hasil verifikasi implementer, bukan verdict baru
atas nama Claude. Diff perbaikan belum independent re-review; R4 tetap terbuka.

| Temuan | Perbaikan | Regresi di `tests/domain/test_review_regressions.py` |
| --- | --- | --- |
| R003-01 | Counter repair menolak eligibility/bind secara langsung; contract change/revalidation tidak memberi budget; authorize repair mempertahankan dependency gate. | `test_contract_revalidation_cannot_grant_repair_budget` (dua urutan), `test_counter_fences_repair_even_if_display_blocker_is_missing`, `test_revalidation_preserves_unrelated_blocker`. |
| R003-02 | `contract_changed` integrator-only. Lead dapat mengusulkan penyesuaian melalui pesan, tidak mencabut run/kandidat tiket lain. Payload revalidation mencatat reporter/ID perubahan. | `test_unrelated_lead_cannot_invalidate_uat_candidate`: UAT/candidate/event tidak berubah. |
| R003-03 | Receipt broker immutable per attempt wajib pada submit. Project/ticket/job/generation/scope/base/commit cocok persis. Artifact Git tetap deduplikasi per SHA; receipt terpin melalui pesan submission. | `test_commit_receipt_is_bound_to_attempt_despite_sha_deduplication` (scope/ticket/generation), `test_model_published_commit_receipt_cannot_authorize_submission`. |
| R003-04 | Hitung closure edge terdampak; hanya edge menuju upstream pada jalur perubahan yang diinvalidasi. | `test_contract_change_invalidates_only_edges_on_affected_paths` memeriksa state/pin/revision edge unrelated tetap sama. |
| R003-05 | Accepted/Cancelled mempertahankan phase/candidate/workflow/blocker. Event follow-up + state dependency mencatat dampak historis. Tiket baru memerlukan checks pada kontrak kini. | `test_accepted_dependency_history_remains_accepted_without_unresolvable_blocker`. |
| R003-06 | Validasi object sebelum membaca scope; initial draft memakai title aman sebelum validasi seluruh scope. Artifact ID kosong/None menjadi `Invalid` sebelum SQLAlchemy query. | `test_malformed_scope_is_domain_invalid_without_partial_ticket_or_event` (5 bentuk), `test_review_without_target_is_domain_error_without_null_primary_key_warning`. |

Observasi scope diselesaikan dengan aturan konservatif dari arsitektur: required
checks tetap wajib setelah edit judul/UAC dan approval scope baru. Pending pin
dipertahankan pada edge yang tetap dipakai, request ID dirotasi untuk scope baru.
Upstream mencatat contract change sehingga edge baru atau hapus-lalu-tambah tidak
melewati checks. Regresi: `test_title_edit_cannot_clear_contract_revalidation_or_reuse_old_scope_proof`
dan `test_known_contract_change_requires_checks_for_new_or_readded_dependency`.
`test_uac_change_requires_new_scope_approval` kini benar-benar mengganti UAC,
memerlukan approval version 2, lalu required checks version 2 sebelum eligible.

Observasi batas repair mempertahankan limit awal tanpa menambah otorisasi:
tiga putaran review/QA/UAT yang berakhir request-changes, termasuk putaran awal.
Dua perbaikan otomatis dapat berjalan, request-changes ketiga memblokir putaran
keempat hingga pengguna menambah budget terbatas. Keputusan ini dan kontrak
receipt broker ditulis eksplisit pada `docs/decisions/workflow.md`.

Mutation/reproduction check: **19 regresi baru gagal** pada tree yang direview
`6b134fc855fe5e21107b91427e9c4697bc7b778c`, memakai kode service/evidence asli dari
Git object tanpa mengubah file/index. Helper hanya membuang keyword baru
`commit_receipt_id` untuk kompatibilitas signature lama; pemeriksaan provenance
lama tetap asli. Script: `data/dev003/check-regressions-original.py` (gitignored),
jalankan dari backend dengan `python ../../data/dev003/check-regressions-original.py`.
Hasil kode perbaikan final:

- Windows `pytest tests/domain tests/persistence -q --tb=short`:
  **232 passed, 1 skipped** (symlink host); domain **103 tests**.
- WSL Ubuntu `pytest -q --tb=short`: **376 passed**, termasuk Docker nyata,
  tanpa skipped. Ini rerun suite lengkap setelah perubahan final.
- DB revision 0002 `app.persistence check`: **database is healthy**;
  whitespace/secret checks seluruh reviewable files/patch lulus.

R003-01..06: **FIX_VERIFIED oleh implementer**, AC5/AC8 kembali terpenuhi menurut
regresi dan DEV-003 kembali DONE. **Diff fix belum independent re-review**;
verdict awal Claude tidak diubah menjadi REVIEWED dan R4 belum ditutup.
Commit/push setelah fix/verifikasi mengikuti instruksi eksplisit pengguna.
