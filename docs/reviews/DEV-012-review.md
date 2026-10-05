# Review DEV-012 — Integrasi Git/DB

Reviewer: Codex, 2026-10-05. Baseline `cfdd16b`; implementasi awal berada di index.
Verdict awal: **NEEDS_FIX**. Pengguna meminta temuan langsung diperbaiki.
Temuan berikut sudah diperbaiki dan diuji reviewer; perbaikan masih menunggu re-review independen. R7 belum ditutup.

## Temuan

1. **R012-01 / P2 — crash dapat meninggalkan hasil integrasi tanpa bukti terpin.**
   `integrator.py` membuat artifact, memfinalisasi domain, lalu menambahkan pesan pin dalam tiga transaksi.
   Crash sebelum pesan membuat operasi done/blocked/diverged keluar dari pending, sehingga restart tidak memperbaiki
   bukti yang tidak terpasang. Done/diverged juga tidak menyimpan evidence ID pada DTO operasi sehingga GUI tidak
   menawarkan bukti integrasi. Kini artifact row, finalisasi domain, evidence ID kandidat, dan pesan pin ditulis dalam
   satu transaksi. Git tetap direkonsiliasi secara terpisah. Fault pada penulisan pesan me-rollback DB; retry memakai
   ref aktual dan menghasilkan satu report terpin. Diuji untuk updated, blocked, dan stale_base.
2. **R012-02 / P2 — urutan recovery berubah saat pengguna mengubah prioritas.**
   Pending diurutkan menggunakan ticket.updated_at. Setelah A memindahkan Git lalu crash, edit prioritas A membuat
   B diproses lebih dahulu. B menganggap ref A sebagai divergence asing dan diblokir permanen padahal A hanya perlu
   finalisasi. Pending kini memakai waktu approval immutable. Integrator juga mengenali ref yang dimiliki operasi
   pending lain pada base DB yang sama dan menunggu recovery-nya; ini melindungi pemanggilan yang tidak berurutan.
   Tidak ada approval yang dipindahkan: setelah A selesai, B kembali ke development untuk kandidat/QA/UAT baru.
3. **R012-03 / P2 — accepted ref yang hilang tidak menghasilkan blocker/evidence.**
   `accepted_sha()` melempar GitBrokerError sebelum jalur block. Pada maintenance thread exception hanya tersimpan
   dalam Future yang tidak dibaca; operasi tetap pending dan tick berhenti sebelum tiket berikutnya. Ref yang tidak
   dapat dibaca kini diblokir dengan report dan alasan, tanpa membuat atau me-reset ref. Regresi menghapus ref Git nyata.
4. **R012-04 / P2 — recovery melewati validasi base/fast-forward.**
   Cabang `observed == target` menganggap ref otomatis sah. Target orphan yang dipasang dari luar menjadi Accepted;
   base DB yang berbeda menghasilkan Conflict berulang tanpa blocker. Recovery kini memeriksa base DB dan ancestry
   sebelum finalisasi, dan mencatat block pada ketidaksesuaian. Regresi memakai orphan commit nyata dan base DB berbeda.
5. **R012-05 / P3 — provenance kandidat tampil sebagai operasi integrasi kosong.**
   Field candidate.integration juga menyimpan source_attempt sebelum UAT. DTO mengubahnya menjadi object dengan seluruh
   field null, meskipun kontrak TS mengharuskan status operasi. GUI menampilkan baris integrasi kosong sebelum accept.
   DTO kini mengembalikan null sampai operation_id tersedia; provenance internal tetap ada.

## Pemeriksaan dan hasil

Kode yang diperiksa: integrator, perubahan domain/contract dependency, workspace rebase, scheduler, wiring/worker,
query API, DTO TS, UI kandidat, tes integrasi, handoff dan keputusan integrasi; broker Git dan transaksi/pin dibaca
untuk memeriksa asumsi otoritas dan atomicity. Approval tetap immutable, ref update memakai CAS, accepted dicatat
setelah Git, dan dependency baru terbuka setelah finalisasi. Review tidak menemukan bypass approval pada alur normal.

- Baseline `tests/integration tests/domain`: 118 passed.
- Tujuh regresi awal: 7 failed sebelum fix; setelah fix suite integration/domain 125 passed.
- Dua regresi recovery tambahan: 2 failed sebelum fix.
- Regresi menggunakan SQLite, artifact dan Git nyata; QA merupakan contract fixture, bukan model/provider nyata.
- `npm run build`: lulus.
- `npx playwright test --config playwright.web.config.ts`: 31 passed.
- Integrasi final setelah guard recovery tambahan: 24 passed (sembilan regresi review termasuk di dalamnya).
- WSL `python -m pytest tests/integration tests/domain tests/persistence tests/http tests/pipeline tests/workers -q`:
  450 passed, 1 failed dalam 172.36 detik. Run ini dimulai sebelum penambahan dua regresi recovery terakhir;
  guard terakhir diverifikasi pada run integrasi final di atas.
- Kegagalan adalah flaky DEV-004 yang sudah tercatat sebelumnya:
  `tests/workers/test_processes.py::test_dead_worker_recovery_revokes_workspace_keeps_logs_and_preserves_other_run`.
  Fixture membunuh worker setelah process group tersimpan tetapi sebelum log `spawned process group` ditulis oleh
  FakeRuntime. Cleanup/revoke/archive berhasil; assertion isi log gagal. Rerun test tersebut terpisah: 1 passed.
  Tidak mengubah tes/modul worker di luar scope DEV-012. Tidak mengklaim suite terkait sepenuhnya hijau pada run pertama.
- `git diff --check`: lulus. Suite Windows tidak diulang.

## Review dokumen dan batas

Handoff dan keputusan diperbarui agar menjelaskan transaksi bukti/finalisasi dan urutan recovery yang sebenarnya.
Angka suite lengkap 768 pada handoff adalah hasil implementer sebelum patch review, bukan klaim pengujian reviewer.
Review ini menjalankan suite terkait integrasi/domain/persistence/HTTP/pipeline/workers, termasuk harness Docker nyata;
suite workspace, runtime spike, agents dan preview tidak diulang karena patch tidak mengubah modul tersebut.

Known issues implementer tetap berlaku: operasi blocked butuh operator (belum ada command pemulihan); revalidasi
dependency otomatis belum ada; contract change otomatis hanya revert; rebase memakai git apply dan belum diuji
melalui model/Hermes nyata. `rebase_onto` clean/conflict diuji dengan Git nyata. Eksekusi provider nyata tidak diulang.
Tidak memulai DEV-013/014. Perbaikan reviewer belum di-stage/commit/push.
