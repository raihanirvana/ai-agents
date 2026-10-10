# Mini Kasir — perbaikan coverage QA, 10 Oktober 2026

## Penyebab terkonfirmasi

Proyek `35ea33a32fde4ec9a378c9285a680eb7`. Kandidat #2
`2429a1d0f7344faf8bbf800e858d0bf7` dan #3
`e8400c8f31624bf49a0a09798d744551` berhenti di QA.
Verification #2 `62c8a274051c4b449904213390f57db1` dan #3
`e659b1f0e136446da266208946b80bbe` menunjukkan browser kandidat
**3/3 passed** dan accepted base **3/3 passed**, tanpa error infrastruktur.

#2 menguji pembuatan produk, tanpa tindakan keranjang/pembayaran. #3 memakai
edit stok katalog, tanpa alur stok masuk/riwayat. Mapping ID UAC ada, namun
perilaku yang dituntut tidak diuji. Penolakan baseline benar. Runtime salah
mengklasifikasikan seluruh incomplete sebagai infrastructure, sehingga retry
mengulang suite identik dan akhirnya meminta intervensi.

## Perubahan dan batas otoritas

- Eksekusi baseline lengkap yang lulus untuk feature/bug menjadi test_contract;
  verification tetap incomplete, bukan pass dan bukan bug aplikasi.
- Scheduler memakai mekanisme diagnosis persisten yang sama, tanpa browser
  ulang untuk retry model pada tahap diagnosis.
- Proposal `QaCoverageRepair` hanya dapat mengganti journey dengan coverage
  gap terkonfirmasi. ID/purpose/UAC tetap; kasus lain tidak berubah. Witness
  menghubungkan setiap test/UAC ke action, assertion dan source excerpt asli.
- Model menentukan skenario dari UAC dan source. Validasi witness tidak bisa
  membuktikan semua makna UAC secara otomatis; harness tetap wajib.
- Proposal dipin candidate/target/suite/verification/fake dan disimpan sebelum
  repin. Retry memakainya kembali; target/approval usang ditolak.
- Source, commit, build dan repair cycle developer tidak berubah. Suite/target
  baru wajib full baseline/candidate execution. Tidak ada waiver/drop UAC.
- Runner/base incomplete tetap infrastructure. Batas suite repair tetap
  berlaku untuk mencegah pengulangan replan yang tak terselesaikan.
- QA planning dipertegas: prerequisite baseline adalah setup, bukan pengganti
  fitur baru. Aktivitas web menjelaskan gap coverage dan tahap perbaikan QA.

## Verifikasi

Dari `apps/backend`, PATH lokal menyertakan Homebrew dan Docker:

```sh
./.venv/bin/python -m pytest tests/pipeline/test_coverage_repair.py tests/pipeline/test_contracts.py tests/pipeline/test_scheduler.py -q
./.venv/bin/python -m pytest tests/pipeline/test_product_regressions.py::test_baseline_green_is_allowed_only_for_regression_not_a_new_feature tests/pipeline/test_selector_repair.py tests/pipeline/test_test_concerns.py -q
```

Hasil **63 passed** (39,66 detik) dan **39 passed** (89,67 detik).
Termasuk Git/DB/Docker/browser nyata dengan provider FAKE: surrogate suite
memicu QA replan, source/build utuh, target baru, kode lama gagal dan kandidat
lulus. FAKE tetap tidak dapat membuka UAT. Interupsi sebelum repin membaca
proposal persisten tanpa model call ulang. Scope/purpose/test drop, perubahan
kasus lain, source palsu dan baseline tidak lengkap ditolak.

Setelah demo mengungkap proposal dengan witness/UAC hilang dan indeks salah,
ditambahkan satu kesempatan koreksi validasi dengan pesan spesifik. Rerun
`test_coverage_repair.py`: **20 passed** (74,01 detik), termasuk satu kasus baru
yang menolak proposal awal dan menerima koreksi tanpa browser ulang. Rerun
helper murni: 18 passed. Total pipeline relevan: 103 tes berbeda lulus.

`tests/domain/test_qa_reopen.py tests/domain/test_evidence.py
tests/domain/test_workflow.py`: **72 passed** (9,92 detik). Setelah fencing
disempitkan ke attempt QA, `test_qa_reopen.py`: **8 passed** (1,37 detik).
QA reopening oleh trusted verification menolak agent/user, reason kosong,
verification usang dan fase integrating; source generation tetap, QA lama
difence dan approval UAT lama tidak dapat dipakai. Total 175 tes berbeda lulus.

`npm run build` lulus (TypeScript dan Vite); warning bundle Office >500 kB masih
ada dan tidak terkait perubahan QA. `git diff --check` bersih.
Independent review: NOT_REVIEWED. Full suite dan lintas OS belum dijalankan.

## Demo nyata

Worker lama berhenti tertib; worker pengganti PID 61822 aktif. Retry resmi
#2 `c3525e8251114e988dd0bf105a51497a` dan
#3 `f76089433084452f8e85408b9d1664ad` mempertahankan histori, caps dan usage.
Proposal awal ditolak karena witness tidak lengkap/indeks assertion keliru;
tidak ada perubahan target dari proposal invalid. Worker diperbarui dan retry
diagnosis #2 `ec7eb502dc5c4befb7c09f62e89b3eb6` serta
#3 `91397c013e7a45f68916beec4a321028` membuat suite baru tanpa browser ulang
pada diagnosis. #3 lulus real QA, job `04138b66e2f244c889b705e169807727`.

Self-review menemukan tes pembayaran #2 belum memeriksa sisa stok walaupun
mapping UAC-4/UAC-5 tercantum. Ini menunjukkan witness struktural/source bukan
jaminan kelengkapan semantik. Trusted verification membuka QA kembali sebelum
approval UAT pengguna; suite dilengkapi stok awal 3, jumlah beli 1, double click
Bayar, keranjang kosong dan assertion `Stok: 2`. Kandidat/commit/build tetap.
Target baru melalui jalur repair resmi kemudian menjalankan full harness;
job `2fda9dc8f4f844eba07d1054713446bc` lulus.

Hasil akhir #2/#3: kandidat **3/3 passed**, accepted base **3/3 failed**, provider
nyata (`fake_provider: false`), fase **UAT**. #4 ready menunggu keduanya diterima
pengguna. Tidak ada approval pengguna, source edit atau application repair cycle
yang dibuat oleh koreksi ini. Worker aktif PID 72771; backend/frontend tetap.
Perubahan repository belum commit/push pada assignment ini.

Pekerjaan berikutnya yang masih relevan: review kelengkapan setiap klausa UAC
pada tahap planning/repair, di luar validasi mapping/witness struktural; baseline
discrimination saja tidak membuktikan semua outcome telah diperiksa. Perubahan
ini menangani kasus konkret dan recovery-nya, bukan menjamin QA model selalu benar.
