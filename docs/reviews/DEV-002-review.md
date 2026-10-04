# DEV-002 — code review

Tanggal: 5 Oktober 2026. Reviewer: Codex. Implementer: Claude.
Verdict akhir: **REVIEWED**, DEV-002 **DONE** setelah perbaikan dan recheck.
Review awal NEEDS_FIX dan seluruh temuan di bawah dipertahankan sebagai riwayat;
hasil akhir ada pada bagian Re-review. Ini review DEV-002, bukan penutupan
checkpoint R4 yang juga mencakup DEV-003/004.

Baseline: `5b5ec1d52f0b28032d38678e3185a14e338ed0bb`.
Staged tree yang diperiksa: `600bcdcfb0941760b6cb473e91407294afd204a5`.
Scope: 34 file staged, termasuk file baru, migrasi, helper, test, konfigurasi
dan dokumentasi. Implementasi dan staging tidak diubah selama review.

## Temuan terkonfirmasi

### R002-01 — P1: approval kehilangan pin build/context kandidat superseded

Lokasi: `apps/backend/app/persistence/pins.py:38` dan `:46`.
Approval/verification hanya mem-pin target dan evidence langsung. Build, commit,
context dan evidence tambahan kandidat hanya dipin ketika status kandidat aktif.
Saat kandidat yang sudah mendapat approval UAT di-supersede, build/context-nya
menjadi eligible untuk cleanup meskipun approval serta verification historis
masih dipertahankan. Manifest target yang masih tersedia tidak menggantikan
artefak yang dihapus. Ini bertentangan dengan AC perlindungan commit/build/
evidence/context yang dirujuk approval dan histori bukti di arsitektur §7.

Reproduksi: buat kandidat dengan build/context, target serta passed verification;
tambahkan UAT approval; ubah status menjadi superseded; jalankan cleanup real
dengan `now` dua hari kemudian. Hasil: `build_removed=True`,
`context_removed=True`, `target_pinned=True`.
Test `test_pins_cleanup.py:62` justru mengharapkan build tidak dipin lagi;
ekspektasi itu perlu dikoreksi, bukan dipakai sebagai bukti keamanan cleanup.

Perbaikan yang diperlukan: pertahankan closure referensi artefak dari target/
approval/verification/release, termasuk build/commit/context yang tepat untuk
target historis. Jangan hanya membaca referensi mutable kandidat terbaru.
Tambahkan regresi cleanup setelah supersede dengan approval tetap ada.

### R002-02 — P1: rollback savepoint menghapus file transaksi luar

Lokasi: `apps/backend/app/persistence/artifacts.py:225`.
`_PENDING` satu daftar per Session, sedangkan `after_rollback` juga berjalan
ketika savepoint di-rollback. Callback menghapus seluruh daftar, termasuk
artefak yang ditulis sebelum savepoint. Transaksi luar masih bisa commit baris
artefaknya sebagai available walaupun file sudah dihapus.

Reproduksi: dalam `db.write()`, tulis artefak A; buka `session.begin_nested()`;
tulis B lalu lempar exception; tangkap exception di transaksi luar dan commit.
Hasil A: `outer_row=True`, `availability=available`, `file_exists=False`.
Tidak ada event unavailable karena penghapusan terjadi di hook rollback.

Perbaikan yang diperlukan: ikat pending files ke lifecycle transaksi/savepoint
yang membuatnya. Rollback savepoint hanya boleh membuang file miliknya;
commit savepoint tidak boleh melepas bookkeeping sebelum transaksi luar commit.
Alternatifnya, tolak penggunaan ArtifactStore dalam transaksi nested secara
eksplisit sebelum menulis. Uji rollback inner/commit outer serta commit inner/
rollback outer.

### R002-03 — P2: retry pesan mengabaikan bagian identitas dan payload

Lokasi: `apps/backend/app/persistence/messages.py:38`; kasus sejenis pada `:62`.
`append_message` membandingkan thread/sender/kind/body/reply_to, tetapi
mengabaikan recipient, ticket_id, metadata dan attachment_ids. Metadata input
request menyimpan job/generation/scope; perubahan field itu tetap diterima
sebagai retry identik dan mengembalikan request lama. `answer_input_request`
juga hanya membandingkan key/body saat menemukan jawaban yang sudah ada,
sehingga sender atau metadata berbeda tidak dianggap konflik.

Reproduksi: simpan input_request key K dengan recipient PO/generation 1;
kirim ulang key K, body sama, recipient QA/generation 2. Hasil:
`created=False`, `returned_generation=1`, `returned_recipient=po`.
Ini bukan retry dengan payload identik; caller memperoleh sukses/no-op untuk
pesan yang berbeda dan konteks attempt lama.

Perbaikan yang diperlukan: bandingkan semua field semantik setelah normalisasi
default. Key sama dengan identitas/payload berbeda harus ditolak; validasi
resume generation aktif tetap merupakan tanggung jawab domain/scheduler.
Tambahkan regresi recipient/ticket/generation/attachments dan sender jawaban.

## Verifikasi

Dari `apps/backend`, Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/persistence -q
```

Hasil aktual: **109 passed / 1 skipped**, 7.07 detik. Skip karena host tidak
mengizinkan symlink. Suite yang lulus belum mencakup tiga kasus di atas.
Reproduksi review memakai SQLite/migrasi/ArtifactStore asli, database dan file
sementara; tidak memakai provider, model, atau data aplikasi existing.
Helper lokal gitignored: `data/dev002/review-probes.py`, dijalankan dari backend
dengan `PYTHONPATH=.`. `git diff --cached --check` lulus.

Suite WSL 110 tests yang dicatat implementer tidak dijalankan ulang dalam review
ini. Tidak ada perubahan kode, commit/push, atau wiring API/worker.

## Pemetaan AC

| AC DEV-002 | Penilaian review |
| --- | --- |
| Migrasi baru, FK/revision/unique | Diuji oleh suite yang lulus; batas domain/scheduler tetap DEV-003/004 |
| State dan event atomik | Test perubahan, rollback, stale revision dan writer concurrency lulus |
| Cursor persisten, checksum artefak | Cursor/checksum dasar lulus; lifecycle file nested transaction bermasalah (R002-02) |
| Manifest/evidence/input/usage/waiver | Struktur dan constraints diuji; dedupe payload input belum benar (R002-03) |
| Pin produk, cleanup, unavailable | Belum terpenuhi: build/context approval historis dapat terhapus (R002-01) |
| Data tersedia setelah restart | Reopen/process crash tests lulus untuk kasus yang diuji; tidak memperbaiki kehilangan file R002-02 |
| Transaksi pendek dan writer conflicts | Concurrency tests lulus; rollback savepoint file perlu diperbaiki |

Perlu memperbaiki tiga temuan dan menambah regresi sebelum DEV-002 kembali
DONE atau dipakai sebagai fondasi berikutnya. Known issues implementer seperti
orphan file saat crash dan wiring leases/slots yang belum tersedia tetap berlaku.

## Re-review perbaikan — 5 Oktober 2026

Reviewer: Codex. Verdict: **REVIEWED untuk DEV-002**. Tidak ada temuan blocking
tambahan pada scope perbaikan yang diperiksa. Scope: kode/migrasi/tests versi
working tree terbaru, termasuk perbaikan release setelah recheck pertama.
Staged tree sebelum finalisasi catatan: `f664a5b618d6a0b55946911b5bb40a83cccc6245`,
ditambah diff unstaged snapshot/pin release dan tests terkait. Index tidak diubah
oleh reviewer; belum ada commit implementasi DEV-002.

| Temuan | Status dan bukti recheck |
| --- | --- |
| R002-01 pin approval historis | FIXED: verification menyimpan snapshot immutable commit/build/context; trigger menolak snapshot yang tidak cocok. Supersede serta rebuild menjaga build target lama, cleanup hanya membuang artefak tak dirujuk. |
| R002-02 rollback savepoint | FIXED: pending files menyimpan rantai transaksi pembuat; rollback hanya membuang file pemiliknya. Regresi rollback inner/commit outer, commit inner/rollback outer, nested savepoints dan failed flush lulus. |
| R002-03 dedupe payload | FIXED: seluruh field semantik dinormalisasi/dibandingkan; recipient/ticket/generation/attachments/sender jawaban berbeda ditolak sebagai conflict. Retry identik tetap no-op. |
| R002-04 pin release, temuan recheck | FIXED: Release memiliki build wajib serta optional commit/context, FK + guard available/satu proyek, frozen snapshot. Pin langsung menjaga seluruh referensi release. |

R002-04 direproduksi pada perbaikan pertama: release berstatus approved hanya
mem-pin target/evidence; build/context dalam manifest target tetap terhapus.
Reproduksi DB sementara menghasilkan `build_removed=True`,
`context_removed=True`, `target_pinned=True`, `evidence_pinned=True`.
Perbaikan terakhir menambahkan kolom snapshot release di model/migrasi dan
memasukkannya ke query pin. Regression test sekarang menjalankan cleanup nyata
`now` +10 tahun: hanya log tak dirujuk dihapus, bytes build/context tetap terbaca.
Referensi snapshot tidak bisa diubah; build NULL, unavailable atau milik proyek
lain ditolak storage.

Verifikasi akhir aktual dari `apps/backend`:

- Windows: `.venv/Scripts/python.exe -m pytest tests/persistence -q`:
  **129 passed / 1 skipped**, 8.66 detik (skip izin symlink).
- WSL: `/root/aiagent-dev002-venv/bin/python -m pytest tests/persistence -q`:
  **130 passed**, 7.95 detik; termasuk symlink.
- `git diff --check`: lulus.

Ketujuh AC DEV-002 telah tercakup fondasi dan verifikasi yang tersedia: migrasi/
constraints, state+event, cursor/checksum, target/input/usage/waiver, pin/cleanup,
restart dan writer concurrency. Gap tiga temuan awal dan pin release sudah
ditutup. Domain workflow, QA release nyata, scheduler/lease dan API wiring tetap
berada pada tiket berikutnya; hasil ini tidak membuktikan R4 penuh selesai.

Migrasi 0001 diubah sebelum rilis. Database percobaan yang sudah memakai bentuk
0001 lama tidak mendapatkan kolom baru dari upgrade no-op; gunakan database
baru untuk reproduksi, dan lakukan migrasi eksplisit jika data lama perlu
dipertahankan. Reviewer tidak menghapus/mengubah database existing. Tidak ada
perubahan kode implementasi, staging, commit atau push oleh reviewer.
