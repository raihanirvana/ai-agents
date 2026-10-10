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

### Tes feature/bug yang lulus pada base — 10 Oktober 2026

Jika eksekusi base lengkap menunjukkan tes feature/bug juga lulus, ini gap
coverage, bukan error infrastruktur atau bukti bug aplikasi. Verification tetap
`incomplete`; job selesai dengan `diagnosis_required` agar scheduler membuat
QA diagnosis persisten untuk target/evidence yang sama. Baseline unavailable,
skipped atau incomplete tetap ditangani sebagai infrastruktur, tanpa replan.

QA dapat mengusulkan `QaCoverageRepair` untuk mengganti hanya journey yang
terbukti tidak membedakan fitur baru. ID tes, purpose, mapping UAC dan kasus lain
tetap. Setiap pasangan test/UAC yang diubah wajib mempunyai witness action →
assertion dengan kutipan tepat source kandidat. Witness adalah usulan yang bisa
ditinjau, bukan bukti semantik otomatis atau QA pass. Expected berasal dari
UAC/fixture; model tidak boleh memakai hasil aplikasi untuk mengubah expected.
Skenario salah sasaran boleh diganti, termasuk assertion yang tidak menguji UAC;
UAC tidak boleh dikurangi/diganti. Source/build kandidat dan repair cycle
developer tetap; suite/target baru wajib menjalankan seluruh base dan kandidat.
Proposal dipin dan disimpan agar retry provider tidak mengulang browser maupun
mengganti proposal yang telah tervalidasi. Jika struktur valid namun witness/
coverage tidak valid, model mendapat satu koreksi dengan error spesifik,
proposal sebelumnya dan daftar pasangan test/UAC yang wajib. Kedua respons
di-checkpoint; proposal invalid tidak mengubah target. Batas suite repair yang sudah ada
tetap berlaku; jika tidak terselesaikan, intervensi diminta di QA.

Pada Mini Kasir #2, tes katalog menggantikan tes keranjang/pembayaran; #3
memakai edit stok katalog alih-alih fitur stok masuk dan riwayat. Kedua suite
lulus 3/3 pada base dan kandidat. Upgrade tidak mengubah bukti lama maupun
membuka retry legacy otomatis; operator dapat retry setelah koreksi platform.

Service verification dapat `reopen_qa` untuk koreksi bukti sebelum pengguna
menerima UAT. Command internal ini tidak diekspos sebagai tool agent: hanya
fase UAT, current candidate dan current passed verification diterima, dengan
alasan persisten. Source/build dan review tetap; QA attempt lama difence dan
preview receipt dilepas. Bukti lama immutable dan menjadi histori. Suite/target
baru serta eksekusi penuh wajib sebelum UAT kembali dibuka. Integrating/accepted
dan approval pengguna tidak dapat ditarik lewat command ini. Pada demo #2,
reopening melengkapi assertion stok setelah klik pembayaran ganda.

Scheduler tidak mengulang diagnosis permanen yang gagal, dan tidak membuat
retry baru untuk legacy failure hanya karena kode di-upgrade. Lease/generation,
cleanup, target drift, budget pool/usage kumulatif dan batas suite repair tetap.
Retry diagnosis yang sudah tersimpan membaca artefak authoritative pada target/
verification yang sama, termasuk setelah prompt/policy diperbarui. Diagnosis
tidak diminta ulang lalu dibandingkan dengan teks lama yang bisa berbeda.
Planning QA menerima source UI baseline sampai 20.000 karakter, dengan inspect_app
untuk file yang tidak termuat. Ruang snapshot minimum 32768 token mencakup
instruksi peran, schema DSL, source dan dependency; ini batas ukuran konteks,
bukan reset/cap usage demo. Kontrol prerequisite yang sudah diterima memakai
selector aktual dari source; ID baru hanya direncanakan untuk fitur baru.
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

## Pemulihan selector fill yang tidak ditemukan

Runner mencatat selector asli dengan nol matches dan alternatif input/textarea
yang visible, enabled, editable dan mempunyai selector ID/class unik. Observasi
dibatasi 80 kontrol/160 alternatif, tanpa nilai isi input, password, ekspresi JS
dari model atau akses di luar halaman target. Bukti kurang/ambigu tidak mengizinkan
koreksi. Absennya kontrol saja belum membuktikan kesalahan tes.

Setelah diagnosis `test`, supervisor menyaring alternatif yang token selector-nya
juga terdapat sebagai literal dalam source shipped. Model hanya memilih indeks
alternatif sesuai maksud field, fixture dan UAC, atau abstain. Seluruh failed test
harus memenuhi kontrak ini; campuran dengan kegagalan lain tidak dikoreksi lewat
jalur ini. Supervisor mengubah hanya selector pada failed fill; input, action lain,
assertions/expected, ID, UAC dan purpose tetap. Proposal disimpan sebagai evidence.

Target/suite baru menjalankan seluruh baseline dan kandidat ulang. Ini tidak
memberi QA pass, tidak mengganti approval, dan tidak memakai repair cycle aplikasi.
Runner baru pada target lama memerlukan refresh identitas runner dan execution
baru terlebih dahulu. Maksimal suite repair tetap berlaku. Jalur ini belum
mencakup selector assertion, tombol, custom widget atau kontrol yang ambigu.

## Concern handoff and early contract checks — 10 October 2026

Developer submission separates a short Git `message` from the public `handoff`.
Submission and technical review can include at most eight typed `test_concerns`:
test/step/original selector, shipped-source path/exact excerpt, and reason.
The trusted service qualifies only missing-fill concerns for the exact candidate,
scope, commit and suite. Obsolete, fabricated or unsupported concerns are logged
and ignored; they cannot reject an application or waive a required gate.

QA observes the entire original candidate suite once before preparing the baseline.
The early path is used only for source-qualified concerns. All failed cases must be
the specifically cited missing-fill steps and have source-declared, unique editable
controls observed by the isolated runner. QA proposes an existing candidate index
for the same intended input; no replacement assertion or input is accepted.
The existing narrow selector repair publishes a new suite/target. Complete fresh
candidate/baseline verification is still mandatory; preflight receipts cannot open UAT.

If no correction is justified, the same candidate execution proceeds to normal
baseline/gate/diagnosis checks, without an extra browser run. Browser proof and QA
proposal are persisted separately with exact pins and retry-stable keys; retries
can reuse them only for the same candidate/target/concerns. Diagnostics are retained
as artifacts. Fake provider labels remain enforced. Source/build/review remain pinned;
prior approval or QA evidence never transfers to a changed target.
