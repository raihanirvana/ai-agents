# Review DEV-014 — Release beku dan sinkronisasi gabungan

Tanggal: 2026-10-05. Reviewer: Codex; implementer awal: Claude (Sonnet 5.5).
Baseline HEAD: `ef30905c7151f5d592834d65fa3710adf0033563` (DEV-013).
Snapshot awal: index staged, 35 file, +3.718/−25 baris. Perbaikan reviewer berada pada working tree,
termasuk file baru `tests/release/test_review_regressions.py` dan laporan ini. Commit/push diotorisasi pengguna setelah checks; diff lengkap tersedia pada commit DEV-014.

Verdict awal: **NEEDS_FIX**. Tujuh temuan diperbaiki atas instruksi pengguna, dengan regresi.
Review awal independen dari implementer; checks sesudah perubahan adalah self-check reviewer terhadap fix sendiri,
bukan re-review independen fix. Status akhir dan angka verifikasi dicatat pada bagian verifikasi di bawah.
Implementasi DEV-014 kembali DONE setelah fix dan verifikasi. Diff fix menunggu re-review independen; R8 keseluruhan belum ditutup. DEV-015 dilanjutkan pada assignment berikutnya sesuai instruksi pengguna.

Scope: spesifikasi blueprint/arsitektur/backlog/workflow, seluruh `app/release`, diff domain/integrator/broker/
supervisor/onboarding/HTTP/worker, kontrak API, GUI Release, tests release/domain/HTTP/browser, keputusan dan handoff.
Tes release memakai Git, Docker build, dan runner browser terpisah nyata. QA tiket pada fixture adalah kontrak sintetis;
hasil ini tidak membuktikan QA tiket oleh provider, review model, UAT manual, atau deployment.

## Temuan dan perbaikan

### R014-01 — P1: regression release berikutnya melewatkan fitur yang sudah dirilis

Lokasi: `release/requests.py:request_freeze`, `release/runtime.py:verify_commit`.
Freeze menghapus tiket yang sudah masuk release approved dari milestone, lalu menyusun regression hanya dari daftar
itu. Setelah Menu dirilis, tiket Cart yang menghapus Menu tetap menghasilkan draf release lulus karena hanya Cart diuji.
Reproduksi Git/Docker/browser nyata: `test_second_release_regresses_features_from_the_first_release_too` gagal pada
kode awal (status `draft`, seharusnya `failed`).

Fix: scope milestone tetap tiket baru, tetapi request mem-pin `regression_scope` seluruh fitur accepted saat freeze.
Target mencatat snapshot/digest itu dan menjalankan seluruh suite/UAC otomatis yang dipin. Sinkronisasi memakai snapshot
lengkap tersebut, termasuk checklist manual fitur lama yang terdampak. Tes tambahan
`test_sync_includes_the_affected_manual_uac_of_an_already_released_feature` memastikan UAC fitur release sebelumnya
tidak hilang. Tiket accepted sesudah freeze tetap tidak masuk regression/scope beku.

### R014-02 — P1: ekspor membuang patch onboarding dan bundle membutuhkan commit yang tidak dimiliki sumber

Lokasi: `release/export.py:export_base`.
Basis ekspor memakai `baseline_sha` managed. Jika onboarding menerapkan patch eksplisit, baseline berbeda dari HEAD
repo pengguna. Diff baseline→release membuang patch pengguna; thin bundle juga dapat memerlukan baseline yang tidak
pernah ada di repo sumber. Reproduksi `test_export_includes_the_explicit_onboarding_patch_and_bundle_needs_only_source_head`
gagal karena file patch pengguna tidak ada setelah patch ekspor diterapkan.

Fix: basis repo existing adalah `source_sha` saat onboarding, atau HEAD sumber pada target sinkronisasi. Tes menerapkan
patch dan mengambil bundle pada dua clone sumber yang independen, memastikan patch onboarding ikut serta dan tip
release identik. Sidik jari repo asli tidak berubah.

### R014-03 — P1: crash setelah kandidat sinkronisasi dibuat membuat retry selalu diblokir

Lokasi: `release/sync.py:sync_release`, `onboarding/source.py:fetch_source_head`.
Ref `<release>-sync` dibuat sebelum verifikasi/publikasi, tetapi keberadaannya selalu berarti "sudah disinkronkan".
Crash pada titik itu tidak menghasilkan replacement dan retry tidak pernah dapat menyelesaikannya. Reproduksi
`test_sync_retry_after_candidate_creation_publishes_exactly_one_replacement` gagal pada kode awal dengan blocker tersebut.

Fix: source/candidate refs terikat root job. Retry memakai pin sumber dan kandidat yang sama, menjalankan verifikasi lagi
dengan budget lintas retry; operasi baru memakai refs lain. Lease dicek sebelum efek Git, dan ref source baru hanya
dipublikasikan setelah tree serta sumber sebelum/sesudah valid. Tes crash sebelum verifikasi lalu retry menghasilkan
tepat satu replacement, source utuh, dan tidak menggeser accepted ref.

### R014-04 — P1: hasil sinkronisasi bisa disetujui tanpa review teknis kode gabungan

Lokasi: `domain/service.py:approve_release`, `release/sync.py`, `Releases.tsx`.
Kode hanya menjalankan regression lalu meminta checklist UAC manual tiket terdampak. Tidak ada aksi atau bukti review
teknis terhadap perubahan sumber dan kode gabungan; kedua patch pun tidak tersedia sebagai pilihan review di GUI.
Reproduksi `test_sync_approval_requires_explicit_technical_review_of_its_pinned_diffs` gagal karena approval tanpa review
tetap diterima, bertentangan dengan AC sinkronisasi yang memerlukan technical review baru.

Fix: review kode gabungan dilakukan pengguna. Target mem-pin dua artefak diff, GUI menyediakan unduh keduanya dan
konfirmasi review yang terpisah dari checklist UAC. API `reviewed_diff_ids` harus persis cocok, tanpa ID asing/duplikat/
hilang; approval menyimpan reviewer, diff IDs, dan target digest. Domain, HTTP, serta browser menguji penolakan dan
binding tersebut. Fixture test-user memberi attestation eksplisit; tidak diklaim sebagai review manusia sungguhan atau
review technical-lead/model otomatis. Review manusia ini menjadi prasyarat approval setelah regression, bukan bukti
bahwa QA/model telah membaca diff.

### R014-05 — P2: sumber yang bergerak saat bundle dibuat masih menghasilkan ekspor berstatus sukses

Lokasi: `release/export.py:export_release`.
HEAD hanya diperiksa sebelum membuat patch/bundle. Drift sesudah pemeriksaan awal masih dapat dipublikasikan sebagai
`exported`, meskipun basis tujuan sudah berubah. Fix: periksa HEAD kembali setelah bundle dibuat sebelum publikasi.
`test_source_drift_during_bundle_creation_prevents_export_publication` menyisipkan commit sumber saat `bundle create`;
job gagal dengan alasan drift dan release tetap approved tanpa hasil ekspor. Tes memodifikasi fixture sumber sebagai
pengguna, bukan melalui worker. Pemeriksaan ini mendeteksi drift yang diamati selama operasi; bukan lock atas repo
pengguna dan tidak dapat mencegah pengguna membuat commit setelah pemeriksaan terakhir.

### R014-06 — P2: membatalkan draf saat sync berjalan tidak mencegah publikasi replacement

Lokasi: `domain/service.py:draft_release`, `release/runtime.py:run`.
Status release asal dibaca hanya saat sync dimulai. Jika pengguna membatalkan draf sebelum `_publish`, replacement
tetap dibuat. Reproduksi `test_discard_during_sync_prevents_a_replacement_from_being_published` gagal pada kode awal
(job `succeeded`). Fix: validasi status release asal kembali dalam transaksi publikasi. Konflik menjadi blocker
permanen yang terlihat. Tes memastikan hanya release asal yang dibatalkan tersimpan, tanpa replacement.

### R014-07 — P2: batas riwayat tip mencabut validitas release beku yang sah

Lokasi: `domain/service.py:tip_known`.
`tip_history` hanya memuat 2.000 tip terakhir. Draf lama yang masih menunggu pengguna ditolak setelah tipnya keluar
dari daftar, walaupun record kandidat accepted/integrasi tetap tersedia. Reproduksi domain
`test_frozen_tip_remains_approvable_when_bounded_tip_history_has_evicted_it` mengosongkan riwayat setelah integrasi tip
baru (ekuivalen eviction); approval gagal pada kode awal. Fix: fallback ke kandidat accepted dengan `integrated_sha`
dan project yang sama. Riwayat tampilan tetap dibatasi; SHA yang tidak pernah diterima tetap ditolak.

## Pemetaan acceptance criteria

| AC | Penilaian sesudah fix |
| --- | --- |
| Scope/tip beku; tiket sesudah freeze masuk berikutnya; tidak memilih subset commit | Guard integrator dan freeze transaction tersedia; regression scope beku lengkap (R014-01), tip lama tetap valid (R014-07). |
| Regression/integration dan manual UAC memakai satu target/build/config/toolchain/fixture/evidence | Build/gate/browser terpisah, coverage/counts dan scope digest terikat target; regresi lintas release dan UAC terdampak lama kini tercakup. |
| Approval release terpisah; rebuild/config membutuhkan target/approval baru | Domain menolak digest/evidence/checklist berbeda; review teknis sinkronisasi mem-pin diff serta target baru (R014-04). |
| Ekspor eksplisit; tidak ada push/PR/deploy; approved bukan deployed | Patch/bundle dihasilkan lokal, cocok source sebenarnya termasuk patch onboarding (R014-02); status/DTO/GUI tidak mengasumsikan deployment. |
| Basis tujuan berubah: revalidasi/approval baru, sumber tidak dimutasi | Drift sebelum dan selama ekspor ditolak (R014-05); replacement harus disetujui tersendiri; fingerprint fixture source utuh. |
| Sinkronisasi gabungan dengan diff, review teknis/QA/regression, checklist terdampak dan approval baru | Review kode manusia eksplisit + regression harness; UAC lama histori, scope tidak diubah; retry idempotent (R014-03), pembatalan dihormati (R014-06). |

## Verifikasi reviewer

Hasil aktual reviewer (batas waktu perubahan dicatat agar suite luas tidak diklaim mencakup edit yang lebih baru):

- Tiga reproduksi awal: **3 gagal** sebelum fix; setelah R014-01/02/03: **3 lulus** (34,51s).
- Reproduksi review teknis, discard selama sync, dan eviction tip: masing-masing gagal sebelum fix.
- WSL + Docker, backend lengkap: **841 passed** (767,07s), sebelum guard discard dan fallback riwayat tip terakhir.
- WSL + Docker, release/domain/HTTP/integration/onboarding source: **241 passed** (282,81s), sesudah guard discard, sebelum fallback riwayat tip.
- Windows suite: **573 passed, 21 skipped** (77,98s), sebelum dua guard domain terakhir.
- Windows domain/HTTP sesudah guard discard: **174 passed, 2 skipped** (39,40s).
- Domain release + HTTP final, sesudah fallback riwayat tip: **17 passed** di Windows (4,95s) dan WSL (4,70s).
- `npm run build`: lulus; `playwright.web.config.ts`: **36 passed** (25,9s), termasuk review diff sinkronisasi.
- OpenAPI/TS requests diregenerasi melalui `python -m app.http.contract`.
- `git diff HEAD --check` dan whitespace file baru lulus. Scan perubahan tidak menemukan credential tak diharapkan atau file lebih dari 1 MiB.

## Batas pemeriksaan dan follow-up

- Hermes/OpenRouter/model berbayar, QA tiket nyata, review technical-lead/model dan UAT manusia tidak dijalankan.
  Build, repo gate dan runner browser release di tes adalah eksekusi nyata dengan label fixture QA tiket yang jelas.
- Process-kill pada tiap instruksi Git dan semua interleaving concurrency tidak diuji exhaustively. Tes recovery menyuntik
  crash pada boundary kandidat/verifikasi/publikasi; bukan klaim tidak ada kemungkinan scratch Git tersisa setelah SIGKILL.
- Alasan failure masih berada dalam receipt, belum diringkas pada DTO. Statik React/Vite stateless; baseline waiver tiket
  tidak menutup required release gates; tidak ada tag Git otomatis/deployment/hosting.
- Review pengguna memakai diff file dan keputusan target; preview release gabungan interaktif belum tersedia.
- Repo source/original hanya dibaca oleh worker. Mutasi sumber dalam tes drift merupakan aksi test-user yang disengaja.
- Diff fix reviewer memerlukan re-review independen; independent review awal tidak membuktikan perubahan reviewer sendiri.

File fix: `app/release/{requests,runtime,export,sync}.py`, `domain/service.py`, `onboarding/source.py`,
HTTP/schema/contracts, `Releases.tsx`, tests release/domain/HTTP/browser, keputusan release/API, README dan backlog.
