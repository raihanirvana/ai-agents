# QA ringan — 7 Oktober 2026

Policy default untuk rencana baru adalah `lightweight`. Untuk tiket kecil,
gunakan beberapa user journey yang koheren (biasanya 1–3), dengan assertion
eksplisit untuk semua UAC otomatis. Angka ini panduan, bukan cap; tiket besar
atau berisiko tetap mendapat cakupan yang memadai. Repo test/build, browser
health, target immutable, baseline feature/bug dan persetujuan pengguna tetap.

## Pembagian otomatis dan manual

PO menerima capability browser dan policy sebelum breakdown/revisi scope.
Perilaku fungsional yang didukung runner diajukan sebagai automated. Visual,
kenyamanan subjektif dan pemeriksaan external yang memang belum didukung bisa
diajukan manual, dengan alasan dan langkah pemeriksaan dalam scope/assumptions.
Pengguna menyetujui pembagian itu bersama scope. Manual tidak memberikan akses
network, provider secret, host atau runtime capability yang dilarang sandbox.

UAC uang, stok, izin akses dan kehilangan data memerlukan pemeriksaan negatif/
regresi sebanding dengan risikonya. Capability gap yang material dibahas sebelum
development; mode automated yang sudah disetujui tidak diganti diam-diam.
Accessibility diuji untuk perilaku yang disetujui/dibutuhkan dalam alur, tanpa
menambah persyaratan atribut atau kosmetik yang tidak berasal dari kebutuhan.

QA planning memakai Step schema/capability yang sudah disediakan. Supervisor
memastikan seluruh automated UAC tercakup, action/argument valid dan expected
CSV tidak keliru. Planning baru menolak mandatory case yang hanya menduplikasi
UAC manual. Scope seluruhnya manual tetap memerlukan smoke browser dengan
assertion, `purpose: smoke`, `uac: []`. Suite historis tidak dipangkas/diganti.

Receipt `qa-preflight.json` menyimpan versi scope, suite digest, capability
revision, jumlah tes dan pemetaan kriteria ke test IDs sebelum developer mulai.
Detail tiket menampilkan siapa memeriksa setiap kriteria. Receipt bukan QA pass.
Checklist UAT tetap berasal dari scope, tidak prechecked berdasarkan QA, dan
backend memvalidasi semua manual IDs serta identitas target/evidence sebelum
menerima keputusan pengguna.

## Diagnosis sebelum meminta perubahan kode

Browser tetap dijalankan runner terpisah. Lulus hanya berasal dari execution
evidence lengkap, bukan pernyataan model. Kegagalan kontrak DOM yang memenuhi
syarat sempit dapat dikoreksi supervisor dan diverifikasi pada target baru.

Kegagalan lainnya menghasilkan verification failed dan job QA diagnosis
persisten, dengan key verification/target. Retry provider hanya mengulang
diagnosis pada report yang sama. Kontrak `QaDiagnosis` memetakan setiap failed
test tepat satu kali, termasuk expected, observed, reason dan atribusi:

- `application`: bug berdasarkan UAC, input, source dan report; request-changes
  membawa diagnosis dan evidence ke developer serta memakai repair cycle biasa.
- `test`: kekeliruan suite. Koreksi sempit CSV hanya dari input fill asli dapat
  membuat suite/target baru dan menguji seluruh baseline/kandidat ulang. Selector
  legacy `assert_visible ...:contains("teks")` yang terbukti syntax error diganti
  menjadi visibility pada selector asal dan assertion substring case-sensitive
  dengan teks yang sama. Tidak memakai keluaran aplikasi sebagai expected.
  Untuk setup konteks terisolasi, model hanya memilih indeks tindakan dari prefix
  sebelum assertion/reload pertama pada tes yang lulus di target yang sama.
  Supervisor menyalin tindakan/input asli ke awal tes gagal; setelah reload,
  hanya click untuk membuka kembali UI diizinkan sebelum assertion yang gagal.
  Setup dengan action/selector yang sudah dilakukan sebelum failed_step ditolak;
  failure langsung setelah reload wajib memakai posisi failed_step, bukan awal tes.
  Semua langkah asli, ID, UAC, purpose, assertion dan expected tetap dipertahankan.
  Proposal disimpan sebagai artefak; target baru menjalankan seluruh baseline dan
  kandidat lagi. Tanpa fixture lulus/proposal valid, kegagalan tetap terlihat.
- `infrastructure`: runner/config belum dapat dipakai; tidak meminta repair kode.
- `unknown`: bukti tidak cukup atau kegagalan campuran; tetap failed di QA.

Planning baru menolak selector jQuery `:contains()` sebelum development;
gunakan selector CSS berscope dan `assert_contains_text`. Kontrak runner memakai
[locator Playwright](https://playwright.dev/python/docs/other-locators); API jQuery
tidak menjadi bahasa selector runner.

Model tidak mengirim kode pengujian atau suite pengganti bebas, tidak drop UAC,
tidak waive dan tidak approve. Actual download tidak menjadi acuan expected.
Mixed/unknown dan test fault di luar koreksi sempit masih memerlukan diagnosis/
intervensi lanjutan; platform tidak menjanjikan semua capability tersedia.

Scheduler tidak mengulang diagnosis permanen yang gagal, dan tidak membuat
retry baru untuk legacy failure hanya karena kode di-upgrade. Lease/generation,
cleanup, target drift, budget pool/usage kumulatif dan batas suite repair tetap.
Retry diagnosis yang sudah tersimpan membaca artefak authoritative pada target/
verification yang sama, termasuk setelah prompt/policy diperbarui. Diagnosis
tidak diminta ulang lalu dibandingkan dengan teks lama yang bisa berbeda.
Planning QA mempunyai ruang snapshot minimum 16384 token untuk instruksi peran,
schema DSL dan dependency; ini batas ukuran konteks, bukan reset/cap usage demo.
Policy tidak menambah cap budget/token; projection context tetap dibatasi ukuran.
Tidak ada migrasi DB, dependency baru, approval ulang otomatis, atau perubahan
mode/approval proyek lama. Pada demo Mini CRM 7 Oktober, fixture setup #3 lulus
browser 3/3 setelah koreksi, dan suite #2 dengan pembukaan detail setelah reload
lulus 2/2 pada kandidat yang memakai accepted base terbaru. Keduanya menjalankan
baseline yang gagal pada kontrol fitur yang belum tersedia. Hasil tersebut
membuktikan kedua alur konkret, bukan seluruh kemungkinan error/recovery.


## Bukti sebelum mengirim ke Developer

Application diagnosis harus menyertakan UAC otomatis yang disetujui, failed
step, source_path dan kutipan source shipped yang diverifikasi supervisor.
Kesalahan kontrak selector/action/fixture yang belum terselesaikan tetap di QA.
Fixture bernama diekspansi secara independen per tes; binding dropdown untuk
record dengan ID dinamis memakai label input asli. Koreksi tetap membuat target
baru dan memerlukan eksekusi penuh; hasil actual tidak boleh menjadi expected.
Opsi statis yang disebut eksplisit dan dideklarasikan dalam kutipan source
boleh didiagnosis sebagai bug aplikasi bila tidak tersedia di DOM.
