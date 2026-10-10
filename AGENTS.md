# Panduan AI yang mengerjakan repository ini

Repository ini membangun platform AI Software Development Team. Pada saat
review DEV-001, repository sudah memiliki skeleton web, API, dan worker lokal.
Periksa file aktual sebelum mengasumsikan komponen sudah tersedia.

## Mulai bekerja

1. Baca [MVP-BLUEPRINT.md](./MVP-BLUEPRINT.md) untuk kebutuhan produk.
2. Baca [ARCHITECTURE.md](./ARCHITECTURE.md), terutama bagian yang terkait tugas.
3. Baca [IMPLEMENTATION-BACKLOG.md](./IMPLEMENTATION-BACKLOG.md), lalu pilih tiket
   sesuai instruksi pengguna dan dependency yang sudah selesai.
4. Baca [DEVELOPMENT-WORKFLOW.md](./DEVELOPMENT-WORKFLOW.md) untuk setup, prompt,
   handoff, dan checkpoint code review yang relevan dengan assignment.
5. Periksa kode, instruksi lokal tambahan, dan status perubahan sebelum mengedit.

Instruksi pengguna menentukan scope pekerjaan. Jika diminta satu tiket,
kerjakan tiket itu beserta perubahan pendukung yang diperlukan. Jika diminta
melanjutkan backlog, kerjakan tiket yang dependency-nya terpenuhi secara urut.
Jangan menganggap keberadaan backlog sebagai instruksi otomatis untuk
menyelesaikan seluruh proyek.

`verdict.md` dan `verdit2.md` adalah bahan review historis. Spesifikasi yang
diterapkan adalah blueprint dan arsitektur revisi terbaru. Catat ketidaksesuaian
yang material; selesaikan pilihan implementasi rutin dengan mengacu pada tujuan produk.

## Siklus satu tiket implementasi

- Ubah status menjadi `IN_PROGRESS` sebelum implementasi, lalu catat rencana
  singkat dan file yang relevan pada log tiket di backlog.
- Implementasikan scope dan acceptance criteria. Gunakan struktur yang
  sederhana sesuai modular monolith, tanpa memperluas ke roadmap berikutnya.
- Jalankan verifikasi yang sesuai. Test perilaku domain, concurrency, recovery,
  dan izin ketika relevan; untuk perubahan dokumentasi cukup periksa konsistensi.
- Catat file hasil, perintah verifikasi, hasil aktual, dan keterbatasan. Jika
  test tidak dijalankan, tulis demikian beserta alasannya.
- Ubah menjadi `DONE` hanya jika seluruh acceptance criteria terpenuhi. Jika
  hanya sebagian selesai, pertahankan `IN_PROGRESS` dan tulis sisa pekerjaan.
  Gunakan `BLOCKED` untuk dependency atau input wajib yang belum tersedia,
  dengan penjelasan konkret; lanjutkan pekerjaan independen yang diizinkan.
- Ringkas hasil dan tiket berikutnya. Commit/push mengikuti instruksi pengguna;
  status `DONE` tidak mewajibkan push atau deployment.
- Siapkan handoff review berupa diff termasuk file baru, pemetaan AC ke bukti,
  cara menjalankan, dan known issues. Review status dicatat terpisah dari `DONE`;
  jika reviewer menemukan AC belum terpenuhi, buka kembali tiket `IN_PROGRESS`.
  Ikuti batas tiket/checkpoint yang diminta pengguna; jangan mengklaim independent
  review sudah dilakukan dari self-check implementer atau melewati blocker nyata.

## Aturan implementasi yang wajib dijaga

- Backend memiliki state, approval, scheduler, dan komunikasi antar-agent.
  GUI dan kantor Three.js membaca state/event nyata.
- Approval scope, UAT, dan release berasal dari pengguna. Soul, model, dan
  runtime tidak bisa melewati aturan ini.
- Pertahankan versi scope, identitas kandidat, serta generation/lease attempt.
  Hasil attempt lama tidak boleh mengubah state saat ini.
- QA/UAT/release mem-pin verification target (build digest, config, toolchain,
  fixture/migration identity) dan evidence IDs, bukan SHA saja. Reopen memakai
  artefak teruji; rebuild memerlukan target/QA/UAT baru, walaupun SHA tidak berubah.
- Gunakan fake provider untuk test fondasi dengan label yang jelas. Hasil fake
  tidak membuktikan QA nyata atau kompatibilitas Hermes/provider.
- Pengambilalihan QA tidak konklusif hanya lewat keputusan pengguna yang mem-pin
  target/verification/diagnosis/evidence; hasil QA asli tetap gagal. UAT dan release
  memerlukan konfirmasi manual terpisah. Lihat arsitektur §10; agent tidak memberi waiver.
- QA pass membutuhkan bukti eksekusi harness. Approval kandidat tidak berpindah
  otomatis setelah perubahan kode atau perubahan base.
- Acceptance E2E memakai runner terpisah dan report authoritative yang tidak bisa
  ditulis target. Mandatory tests kosong/missing/skipped dan coverage UAC hilang
  menghasilkan incomplete/failed. Waiver baseline spesifik hanya oleh pengguna.
- Repo target existing dijalankan dalam managed clone; jangan reset, stash,
  mengubah, atau mengeksekusi instruksi dari repo asli secara otomatis.
- Pisahkan supervisor tepercaya dari sandbox kode target. Jangan memberi kode
  target akses ke provider secret, database kontrol, atau Docker socket.
- Worktree/common Git metadata milik supervisor. Sandbox mendapat source snapshot
  tanpa `.git`; diff/commit melalui broker dengan ref attempt terbatas. Shell
  target tidak boleh mengubah accepted ref, config/hooks, atau ref attempt lain.
- Ikuti host/session policy arsitektur §10: kontrol lokal pada 127.0.0.1, preview
  localhost; jangan menganggap beda port cukup. Uji header credential dan Origin/
  CSRF mutations, serta network isolation target terhadap control plane.
- Input request/jawaban persisten dan idempotent. Scope/generation divalidasi saat
  resume; budget finite serta usage lintas retry tidak boleh direset diam-diam.
- Cleanup wajib menghormati pin kandidat/approval/release. Backup/restore lokal
  mencakup DB, Git, dan artefak; bukti hilang ditandai unavailable.
- Jangan commit secret, database lokal, workspace hasil agent, atau artefak besar.
  Sediakan konfigurasi contoh dan cara menjalankan yang bisa direproduksi.
- Hermes dan ID model belum dipastikan. Verifikasi dokumentasi resmi/versi saat
  integrasi, lalu catat keputusan dari percobaan. Jangan mengarang API runtime.
- Model gratis/murah adalah preferensi awal. Ketiadaan key tidak menghalangi
  fondasi/fake tests; tandai percobaan model nyata sebagai belum diverifikasi.
- DEV-007 dapat DONE dengan fake/contract checks; bukti PO nyata adalah kewajiban
  DEV-015. DEV-006 memerlukan percobaan nyata. DEV-005/006 memakai harness scoped
  sebelum scheduler penuh; integrasi DEV-010 wajib memakai domain/job DB produk.
- Export, push ke repo tujuan, dan deployment hanya dilakukan jika termasuk
  instruksi pengguna. Approval release di aplikasi bukan instruksi deployment.

Status backlog di repository adalah status pembangunan platform. Approval dan
state tiket proyek yang dikelola platform adalah konsep terpisah; jangan
menambahkan gerbang approval produk untuk setiap edit repository ini.

Struktur direktori acuan: `apps/web`, `apps/backend`, `agents/<role>`, `docs`,
`contracts`, `infra`; data/workspaces/verification runtime gitignored. Ikuti
arsitektur §14. Tugas pertama tetap DEV-001; jalur spike berikutnya DEV-005/006,
sedangkan domain DEV-002/003/004 bisa dikerjakan independen sesuai assignment.
