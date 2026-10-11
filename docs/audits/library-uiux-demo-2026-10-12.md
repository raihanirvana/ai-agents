# Demo Mini Perpustakaan dengan UI/UX — 12 Oktober 2026

Status: **BERJALAN**. Proyek baru `7947f5bdc1c44c7c85556d94b06f9a54`.
Kode awal `0493e224fa7deb961d61f003478d7f4d909e7f1f`. DEV-027 belum DONE.

## Kondisi dan workload

Brief: katalog buku (tambah, cegah duplikat, hapus), pinjam/kembali dengan nama
wajib dan perlindungan buku dipinjam, pencarian judul serta statistik
total/tersedia/dipinjam. Data/status disimpan di browser dan bertahan setelah
refresh. Tepat tiga tiket berurutan #1 → #2 → #3; tampilan katalog manual
pada UAT, perilaku lainnya automated. Scope v1 diusulkan PO, lalu ketiga scope
disetujui pengguna di web pada 12 Oktober. Observer tidak melakukan approval.

API dan worker dimuat ulang setelah memastikan tidak ada job running/queued.
SQLite disalin melalui backup API sebelum restart; proyek/evidence lama
dipertahankan. Web lokal tetap berjalan di `127.0.0.1:5173`, API `127.0.0.1:8000`.
Project setup selesai dan `workflow.pipeline.ui_ux=true` sudah diverifikasi.

Provider nyata dari konfigurasi lokal: PO `google/gemini-3.1-flash-lite`, UI/UX,
TL, Developer, dan QA `deepseek/deepseek-v4.1-flash`, melalui OpenRouter.
Ini berbeda dari QA Gemini pada benchmark 11 Oktober; perubahan hasil tidak
dapat seluruhnya diatribusikan kepada penambahan UI/UX. Konfigurasi lokal
`DEMO_UNLIMITED_BUDGETS=1` sudah ada sebelum demo; kebijakan itu tersimpan pada
proyek baru, usage tidak direset. Cache hangat: prefetch 115 package cache hits,
0 byte download. Ini bukan benchmark cold cache.

## Bukti awal dan pengamatan

- Browser nyata Chromium: login, create project, dan kirim breakdown berhasil;
  tidak ada `pageerror` pada alur ini. Board menunjukkan tiga proposal, dan
  kantor menampilkan UI/UX; kedua alur browser juga tanpa `pageerror`.
- `project_setup` dan `reference_prefetch` succeeded.
- PO succeeded dalam satu model call, `fake_provider=false`: 6.236 token,
  reported provider cost USD 0,00280275. Tiga tiket dibuat pada Scope Review.
- Belum ada klaim QA/UAT/release lulus. Approval scope, UAT, dan release tetap
  keputusan pengguna; observer tidak menyetujui atau melakukan waiver.

Data privat/gitignored: `data/demo-library-uiux-2026-10-12/`. `run.json` memuat
identitas proyek dan waktu mulai UTC; `current-project-metrics.json` memakai
report CLI produk, `current-demo-summary.json` memuat phase/job terkini.
`collect_metrics.py` read-only, interval 30 detik, maksimal enam jam dan berhenti
ketika semua tiket accepted/cancelled. Tidak memanggil provider atau mutation.

## Hasil akhir yang masih ditunggu

QA pertama per tiket, biaya QA + UI/UX, repair, amandemen, keputusan pengguna,
waktu aktif versus tunggu approval, dan penilaian tampilan saat UAT. Bandingkan
dengan [benchmark 11 Oktober](library-live-efficiency-2026-10-11.md) setelah
hasil lengkap tersedia. Fase telemetry saling overlap; jangan dijumlahkan
sebagai wall time. Usage unknown tetap unknown, biaya reported bukan invoice.

## Intervensi platform: repair output UI/UX

Job `317ccf803c674e8c8e407995b9ad2083` gagal setelah dua model calls. Output
pertama memakai placeholder pada accessible name tombol; output repair memakai
role komponen di luar enum. Kedua output tetap ditolak. Union tanpa discriminator
mencampur masalah UiSpec dengan error NoUiChange/Clarification, menghabiskan
slot pesan repair dan tidak memberi arahan role yang cukup jelas.

UiSpec dan kontrak output stage sekarang memilih branch lewat `kind`.
Validator role memberi arahan ARIA dan instruksi role membedakannya dari tag
HTML/nama komponen. Aturan role/copy tidak dilonggarkan. Regresi terkait:
`pytest tests/agents/test_ui_spec.py tests/pipeline/test_ui_spec_stage.py
tests/agents/test_ui_ux_role.py -q --tb=short`: **99 passed, 36,63 s**.
Worker dimuat ulang saat idle; retry operator dilakukan setelah fix tanpa
reset usage atau approval. Histori gagal (21.501 token, USD 0,007501635)
tetap menjadi bagian hasil demo. Demo ini memuat intervensi platform sehingga
bukan perbandingan satu versi kode yang identik dari awal hingga akhir.

Retry `a02a312c9001436aa748195877d5740e` **succeeded**, UiSpec revisi 1
tersimpan. Output pertama retry memiliki feedback action/outcome ganda;
repair menerima tepat satu masalah UiSpec dan output kedua valid. Dua calls
retry memakai 21.484 token, USD 0,007349778. Total UI/UX termasuk job gagal
42.985 token dan USD 0,014851413. TL kemudian succeeded dalam satu call,
12.052 token, USD 0,002964225. Tiket #1 masuk Development dan QA planning
berjalan paralel. Tiket #2/#3 sudah Ready, menunggu dependency accepted.
