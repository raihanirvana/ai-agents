# Release yang dibekukan dan verifikasi gabungan — DEV-014

Status implementasi dan hasil review terbaru: lihat backlog dan `docs/reviews/DEV-014-review.md`.
Sumber kebutuhan: ARCHITECTURE §10 (integrasi dan release), MVP-BLUEPRINT (Preview dan release), AC DEV-014.

## Alur

1. **Freeze (pengguna).** `POST /projects/{id}/releases` (dengan `expected_revision` proyek) mencatat intent sebagai job
   `release` (`task: verify`) berisi accepted tip dan scope beku: tiket berstatus Accepted yang belum masuk release
   `approved/exported/deployed` sebelumnya, masing-masing dengan versi scope, kandidat, `integrated_sha`, approval UAT,
   UAC (otomatis/manual) dan checklist manualnya. Ditolak bila tidak ada tiket accepted, ada integrasi yang masih
   `integrating`, sudah ada draf, atau sudah ada operasi release aktif. Selama job release proyek itu aktif,
   **integrator tidak menggeser accepted tip**: accept baru tetap tercatat `pending` dan baru diintegrasikan sesudahnya,
   sehingga masuk release berikutnya. Release tidak memilih subset commit: yang dibekukan adalah tip, bukan daftar commit.
2. **Verifikasi gabungan (worker, `app/release/runtime.py`).** Commit tip dibangun bersih dalam sandbox (install/build),
   repo tests (flat Node TAP) dijalankan, lalu **satu** regression browser gabungan dijalankan oleh runner terpisah
   (DEV-010). Suite = gabungan suite QA seluruh fitur accepted saat freeze, termasuk fitur pada release sebelumnya
   (id `t<nomor>-<id>`, UAC `tiket:UAC`). Scope milestone tetap tiket yang belum dirilis; `regression_scope` dan
   digest-nya dipin terpisah pada target. Setiap UAC
   otomatis dari seluruh tiket harus tercakup; repo tests harus lulus penuh (waiver baseline per tiket tidak berlaku pada
   release). Hasil: target release (`target_manifest`) dengan accepted tip, digest build, digest scope, suite, toolchain,
   config, fixture, migrasi, identitas runner, manifest eksekusi, serta receipt `release_verification` (producer
   `verification`) dan bukti pendukung (laporan acceptance, gate, suite, bundle build, screenshot/trace, log).
3. **Draf.** `draft_release` (service verification) menyimpan release `draft` dengan scope beku, tip, target, build, dan
   daftar bukti; `evidence_ids[0]` adalah receipt. Regression/gate gagal tetap tercatat dengan bukti tetapi sebagai
   `failed` dan tidak pernah dapat disetujui. Freeze baru tidak diblokir oleh release `failed`.
4. **Approval (pengguna).** `POST /releases/{id}/decisions` mem-pin target, digest, seluruh evidence, dan
   **checklist UAC manual** release (daftar tepat; kurang/lebih/duplikat ditolak). Approval release terpisah dari
   scope/UAT tiket. Receipt harus lulus, bukan fake, tanpa infrastructure failure, dengan counts/commands lengkap, dan
   target harus mengidentifikasi tip, build, serta digest scope yang sama. Rebuild atau perubahan build/config
   menghasilkan target dan digest baru: approval lama tidak berpindah, dan approval pada digest lain ditolak (409).
5. **Tip beku tetap sah.** Karena tiket lain boleh diterima sesudah freeze, tip proyek maju. Domain mencatat
   `tip_history` saat integrasi menggeser tip; tip release valid bila tip saat ini, riwayat tip, atau kandidat sinkronisasi
   yang terverifikasi. Riwayat tampilan yang dibatasi bukan sumber otoritatif: kandidat accepted dengan identitas
   integrasi persisten tetap membuktikan tip walaupun sudah keluar dari 2.000 entri riwayat.
6. **Ekspor (aksi eksplisit).** `POST /releases/{id}/export` (hanya `approved`) membuat job `export`: patch
   `git diff --binary` dari basis ekspor ke tip dan Git bundle (ref `refs/releases/<id>`, tipis dengan prerequisite basis
   bila repo pengguna sudah memilikinya). Basis = HEAD sumber saat onboarding untuk repo
   existing, commit akar untuk proyek baru, atau HEAD sumber hasil sinkronisasi. Receipt `export_result` (artefak oleh
   integrator, `pushed: false`, `deployed: false`) membuat status `exported`. Tidak ada push, PR, atau deployment;
   `deployed` tidak pernah diturunkan dari status lain. Commit baseline managed dapat memuat patch onboarding yang
   belum dimiliki sumber; patch itu ikut ekspor dan bukan prerequisite bundle. Ekspor ditolak bila HEAD repo sumber
   berubah dari basis release, termasuk bila drift terdeteksi sesudah patch/bundle dibuat sebelum publikasi.
7. **Sinkronisasi gabungan.** `POST /releases/{id}/sync` (draf/approved yang belum diekspor, proyek existing): HEAD
   sumber baru dibaca (hanya baca, tanpa hook/filter, validasi tree seperti onboarding) ke `refs/releases/`, patch release
   (basis → tip) diterapkan atomik ke HEAD baru di worktree sementara managed. Konflik = diblokir dengan alasan (tanpa
   release baru; selesaikan lewat tiket baru). Bila bersih: satu **kandidat pengganti** dibangun, diuji dengan regression
   gabungan, dan didraf sebagai release baru (tip = kandidat sinkronisasi, dicatat di `release_tips`); draf lama menjadi
   `failed` (superseded), release approved lama tetap sebagai histori. Evidence memuat patch drift sumber dan patch gabungan.
   Tiket dari regression scope beku (termasuk fitur release sebelumnya) yang file-nya beririsan dengan drift ditandai
   `affected_by_sync`; **checklist release pengganti hanya UAC manual
   tiket terdampak**. UAC tidak pernah diubah oleh sinkronisasi; perubahan UAC adalah revisi/tiket baru dengan approval scope.
   Ref `accepted` managed tidak digeser.
8. **Review teknis gabungan oleh pengguna.** Draf sinkronisasi menyediakan diff drift sumber dan diff kode gabungan.
   Sebelum approval, pengguna meninjau keduanya dan mengirim `reviewed_diff_ids` yang persis cocok dengan
   `technical_review_evidence_ids` pada target. Backend menolak ID hilang, salah, atau duplikat; approval menyimpan
   reviewer, ID diff dan digest target. Checklist UAC manual tetap terpisah. Ini review kode manusia dan regression
   harness; tidak mengklaim review technical-lead/model otomatis atau UAT manusia dari fixture tes.

## Keputusan dan batas

- Release job memakai lane execution (satu slot) dan membuktikan restart lewat state DB: draf, target, approval, dan bukti
  tetap terbaca; job yang mati ditangani recovery supervisor yang sama (`reconcile` membersihkan workspace/container).
- Sinkronisasi mem-pin source/candidate pada refs milik root job. Retry memakai pin yang sama dan memverifikasi lagi
  dalam budget kumulatif; operasi baru mempunyai refs sendiri. Crash setelah kandidat dibuat tidak mengunci release
  lama. Ref sumber dipin setelah validasi tree, pemeriksaan sumber sebelum/sesudah, dan pemeriksaan lease.
- Status release asal diperiksa kembali dalam transaksi publikasi replacement. Draf yang dibatalkan pengguna
  selama verifikasi berjalan tidak dapat menghasilkan release pengganti; job berakhir blocked dengan alasan.
- Pemblokiran integrator diturunkan dari job release aktif, bukan marker, agar kegagalan permanen tidak menahan integrasi.
- Repo tests harus lulus penuh pada release; proyek dengan baseline merah (`ready_with_baseline_failures`) tidak dapat
  mencapai release approved sampai diperbaiki lewat tiket.
- Hanya stack statis React/Vite stateless dan migrasi `none` (batas DEV-010/011). Regression memakai suite QA tiket yang
  tersimpan; tidak ada pembuatan tes baru di release.
- Setelah sinkronisasi, basis sumber baru hanya berlaku untuk release pengganti itu: release berikutnya pada repo yang
  bergerak lagi membutuhkan sinkronisasi baru, sampai proyek di-onboard ulang.
- Ekspor membaca HEAD repo sumber untuk deteksi drift tetapi tidak pernah menulis ke sana. Tidak ada tag Git otomatis
  (tag dibuat pengguna dari bundle). Deployment dan hosting adalah tahap tersendiri.
- Batas ukuran patch 64 MiB (ekspor) dan 16 MiB (sinkronisasi). Satu operasi release per proyek pada satu waktu.
