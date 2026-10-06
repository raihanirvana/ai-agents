# GUI board, chat PO, dan review scope — DEV-009

Implementasi: `apps/web/src` (React + Vite). GUI hanya membaca dan mengubah state
lewat `ApiClient` DEV-008; tidak ada state domain, approval, atau jadwal di browser.
Kantor Three.js tidak termasuk.

## Struktur

| Berkas | Isi |
| --- | --- |
| `App.tsx` | Gerbang sesi (`GET /auth/session`), login, daftar proyek, route di URL hash `#/p/<project>/t/<ticket>` |
| `workspace.tsx` | Konteks per proyek: snapshot board/pesan/detail, SSE `watch`, command idempotent |
| `components/Board.tsx` | Kolom per phase, drag/drop prioritas, approval batch |
| `components/Ticket.tsx` | Scope version/UAC, edit scope, diff usulan PO, dependency, kandidat/bukti, UAT, waiver, pesan |
| `components/Chat.tsx` | Chat PO (`breakdown` / `revise` / `note`), jawaban pertanyaan user |
| `components/Activity.tsx` | Run, blocker, stop, jawaban input run, log |
| `diff.ts`, `format.ts` | Diff scope, label, helper tampilan |

## Keputusan

- **Sumber kebenaran.** Setiap event SSE (atau respons command) memicu refetch board,
  pesan, dan detail tiket yang terbuka. Respons lama tidak boleh menimpa yang lebih baru
  (nomor urut respons yang sudah diterapkan). Permintaan lebih baru yang masih berjalan
  tidak menghalangi respons selesai untuk tampil. Epoch proyek dan pilihan tiket
  menjaga respons lama saat pengguna berpindah. Tidak ada update optimistis;
  yang tampil selalu hasil backend.
- **Command.** Setiap tindakan memakai `Idempotency-Key` baru; key yang sama dipakai ulang
  hanya untuk pengulangan permintaan identik setelah kegagalan jaringan atau HTTP 5xx.
  Key dibuang setelah sukses atau penolakan 4xx; respons 5xx tidak membuktikan transaksi belum
  dijalankan. Body berbeda mendapat key berbeda. Key retry hanya bertahan selama komponen
  proyek terpasang, belum persisten lintas reload. `expected_revision` diambil dari data yang sedang tampil: revisi proyek
  untuk brief, revisi tiket untuk edit/priority/approval/UAT/repair, revisi run untuk
  jawaban input. Conflict (409 `revision_conflict`) ditampilkan dan data dimuat ulang.
  Append chat tidak membawa precondition revision proyek; idempotency key tetap wajib.
  Editor scope/brief menyimpan revision saat dibuka, bukan mengikuti revision dari SSE;
  draf lama ditolak dan tetap bisa disalin. Deskripsi disimpan terpisah dari metadata scope
  di DTO; editor mempertahankan keduanya, termasuk `reverts_candidate_id`.
- **Stop.** `POST /runs/{id}/stop` dengan body `{}`. Tidak ada `expected_revision`
  (heartbeat supervisor menaikkan revision run). Run yang sudah selesai menghasilkan error
  409 yang ditampilkan, bukan sukses palsu. Tombol hanya muncul untuk run aktif dan
  memerlukan konfirmasi kedua.
- **Drag/drop.** Hanya mengubah prioritas dalam satu kolom lewat `POST /tickets/{id}/priority`.
  Memindahkan kartu antar-kolom ditolak dengan catatan; phase berubah hanya lewat tindakan
  eksplisit (setujui scope, UAT, batalkan). Konvensi GUI: angka prioritas lebih besar tampil
  lebih atas; satu pergeseran mengirim ulang hanya tiket yang prioritasnya berubah. Backend
  belum memakai prioritas untuk scheduler; konvensi ini harus disamakan saat scheduler memakainya.
  Tombol ▲/▼ tersedia sebagai alternatif keyboard.
- **Approval eksplisit.** Approval scope (satuan atau batch) menampilkan versi scope yang
  disetujui dan memerlukan konfirmasi kedua. Menerima usulan revisi PO hanya membuat versi
  baru; ia tidak menyetujui scope. UAT hanya tersedia bila ada kandidat scope saat ini dengan
  verifikasi `passed` yang dipin pada `candidate.preview.verification_id` untuk target
  yang sama; UAC manual harus dikonfirmasi satu per satu, dan
  payload membawa `target_artifact_id`, `target_digest`, `verification_id`, `evidence_ids`.
  Konfirmasi gugur jika identitas/revision scope, pilihan batch, atau target berubah.
  Checklist manual terikat candidate/scope/target/verifikasi/evidence; tidak berpindah ke
  build baru. Payload UAT memakai seluruh evidence kandidat, termasuk receipt preview smoke,
  dan bukti tambahan itu ikut ditampilkan.
  Onboarding existing (DEV-013) menyediakan form manifest/pilihan patch dengan source SHA,
  status dirty/baseline/blocker dan report. Release DEV-014 belum dikerjakan; GUI tidak
  menyediakan tombol release.
- **Label fake.** Run dengan `fake: true`, atau pesan dengan `metadata.fake`, diberi badge
  "FAKE · bukan QA nyata" di kartu, topbar, run, chat, dan usulan. Tiket dengan run fake
  menampilkan catatan bahwa hasil verifikasinya tidak membuktikan QA nyata. Status `incomplete`
  tidak ditampilkan sebagai lulus.
- **Artefak.** Bukti diperiksa lewat `GET /artifacts/{id}`; yang `unavailable` atau tidak
  ditemukan ditandai "tidak tersedia" beserta alasan, tanpa tautan unduh.
  Perubahan ID, event `artifact.*`, reconnect, dan snapshot reset memicu pemeriksaan ulang.
  Heartbeat run tidak memicu hash ulang semua artefak.
- **Reload dan reconnect.** Sesi dipulihkan dengan `GET /auth/session`, pilihan proyek/tiket ada
  di URL hash, draf chat di `sessionStorage` (hanya konvenien; dapat kosong). Terputusnya
  stream menampilkan "Menyambung ulang…"; `snapshot_required` ditangani client DEV-008.
  `CrashGuard` menampilkan pesan, bukan halaman kosong, bila ada kesalahan render.
  Cursor stream dibaca sebelum refetch pesan/detail; perubahan selama pembacaan di-replay.
  Event `open` memulihkan indikator dan memicu refresh meski tidak ada event state baru.
- **Input dan histori.** Pertanyaan ke pengguna tampil meskipun thread bukan `chat:*`.
  Pertanyaan blocking dijawab ke `/runs/{id}/input` dengan identitas request/scope/generation;
  endpoint proyek hanya untuk eskalasi nonblocking. Balasan handoff tetap tampil. Diff
  proposal memakai versi basis proposal, sehingga tetap bermakna setelah revisi diterima.

## Batasan yang diketahui

- Streaming token tidak ada: API menyimpan pesan final, jadi GUI menampilkan indikator
  "sedang menyusun jawaban" selama run PO aktif dan pesan final setelah SSE.
- Chat memuat pesan terbaru (batas API) tanpa paging mundur (observasi DEV-008).
- Status `waiting_quota` dan jawaban input run diuji dengan respons API tiruan yang sesuai
  kontrak, bukan dengan backend yang menghasilkan state itu; backend DEV-004/008 diuji terpisah.
- Pengalaman dengan model/PO nyata belum diverifikasi (DEV-015). Proses worker nyata dijalankan
  terpisah: `python -m app.worker --runtime structured`.
- Kontrol UI memuat font dari Google Fonts (warisan DEV-001); tanpa jaringan, font sistem dipakai.
