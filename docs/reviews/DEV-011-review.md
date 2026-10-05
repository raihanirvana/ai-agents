# Review DEV-011 — Preview UAT

Tanggal: 2026-10-05. Reviewer: Codex. Baseline HEAD `6cd5f8e`; implementasi awal di index.
Verdict awal **NEEDS_FIX**; temuan di bawah diperbaiki langsung atas instruksi pengguna.
Perbaikan memiliki regresi dan self-check reviewer; re-review independen atas perbaikan ini masih diperlukan.
Checkpoint R6 belum ditutup. Status pembangunan tetap DONE setelah verifikasi perbaikan.

## Temuan dan perbaikan

1. **R011-01 / P1 — batas host/credential hanya ada di link GUI.** `preview/proxy.py` meneruskan seluruh byte
   HTTP tanpa pemeriksaan Host atau credential. Permintaan ke `127.0.0.1:<preview-port>` mencapai socket container,
   termasuk cookie host-only kontrol yang dapat dikirim browser pada navigasi ke alias itu. Server statis tidak
   menolak header tersebut. Ini melanggar AC isolasi header; tidak ada klaim bahwa session telah dicuri dalam pengujian.
   Proxy kini memeriksa Host localhost dan Origin, menolak credential kontrol, serta hanya meneruskan satu GET/HEAD
   tanpa body per koneksi. Pipelining tidak meneruskan request kedua. Validasi terjadi sebelum koneksi ke target.
2. **R011-02 / P2 — gagal start/recovery dengan cleanup gagal tidak masuk antrean retry.** Exception `_teardown`
   di handler kegagalan start meninggalkan `starting`; recovery yang gagal membersihkan juga meninggalkan `starting`.
   Tick berikutnya hanya memproses requested/stopping sehingga resource bisa tertinggal sampai restart/manual stop.
   Kegagalan kini disimpan pada `stopping`, tetap terpin, dan cleanup dicoba kembali. Shutdown juga memproses stopping.
3. **R011-03 / P2 — switch menjalankan preview baru meski container lama belum berhasil dibersihkan.** `_stop`
   menyimpan error tetapi `run_once` tetap menjalankan requested. Dengan Docker error, dua container dapat hidup.
   Start sekarang menunggu seluruh cleanup pending selesai; permintaan baru tetap requested sampai itu terbukti.
4. **R011-04 / P2 — inspect setelah remove menganggap error transport sebagai bukti container hilang.** Semua
   exit nonzero dianggap berhasil, termasuk connection reset. Pemeriksaan kini mewajibkan engine dapat diakses
   dan pesan eksplisit no-such-container/object; ketidakpastian mempertahankan stopping dan pin.

Tes baru: `apps/backend/tests/preview/test_review_regressions.py` (10 kasus). Semua 10 gagal terhadap modul asli
yang diambil dari index tanpa mengganti working tree; kasus switch juga membuktikan container tertinggal pada
teardown kode asli. Container probe itu diperiksa labelnya dan dibersihkan. Script reproduksi lokal berada di
`data/dev011/check_original.py` (gitignored). Tes browser ditambah navigasi alias dengan cookie sesi nyata → 403.

## Verifikasi

- Baseline preview + HTTP preview: 20 passed.
- Setelah perbaikan: preview + HTTP + persistence: 215 passed di WSL dengan Docker nyata.
- Windows HTTP + persistence: 184 passed, 2 skipped.
- Build frontend: lulus.
- Browser preview (API/supervisor/Docker nyata): 4 passed, termasuk alias host.
- GUI: 30 passed.
- Suite backend lengkap WSL dengan Docker nyata: 753 passed, tanpa skip (365.56 detik).
- Setelah penyesuaian shutdown terakhir: regresi review + lifecycle dijalankan ulang, 20 passed.

Run browser pertama berbarengan dengan pytest melihat container milik suite lain karena endpoint fixture
`/containers` memakai filter global. Filter fixture kini juga membatasi label supervisor miliknya; run ulang lulus.
Tidak ada provider/model berbayar yang dipanggil. Fix belum di-stage/commit/push.

## Batas review dan observasi

API mutation memakai receipt/transaction dan guard yang sama, preview tidak membuat job atau memegang slot execution,
dan eligible target membaca bukti domain serta checksum bundle. Reopen memakai artefak yang sama, target berubah
tetap memerlukan QA/UAT baru, dan approval tidak bergantung pada preview hidup. Pin aktif mencakup target/build/bundle/bukti.
Static server memeriksa symlink pada setiap komponen path; container network none tetap diuji nyata.

Batas stack statis tanpa DB/migrasi, satu worker per host, dan server preview yang disalin dari harness tetap berlaku.
Worker restart **menghentikan** preview lama; pengguna menekan Buka ulang. Tidak ada reopen otomatis saat restart.
Revisi ini tidak mengulang kualifikasi model DEV-010 atau memulai DEV-012/013. Smoke DEV-001 dan browser DEV-008
tidak diulang karena patch tidak mengubah aplikasi/API kontrol atau kontraknya; suite HTTP dan GUI tetap dijalankan.
