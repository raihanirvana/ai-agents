# DSL browser QA — revisi 3, 7 Oktober 2026

DSL adalah daftar operasi browser yang dijalankan runner tepercaya terpisah.
Model tidak mengirim JavaScript, shell, path host, atau kode assertion bebas.
Schema canonical: `apps/backend/app/pipeline/contracts.py` (`Step`, `QaPlan`).
Dispatcher: `contracts/verification/acceptance.py`. Capability diberikan kepada
TL dan QA saat planning, dan TL saat review. Semua tes wajib mempunyai assertion
dan seluruh UAC otomatis tetap wajib tercakup.

## Operasi yang tersedia

| Kebutuhan | Action | Argumen |
| --- | --- | --- |
| Klik, double click, hover, fokus | `click`, `double_click`, `hover`, `focus` | selector, tanpa value |
| Teks, tanggal, angka, textarea, contenteditable | `fill` | selector, string value |
| Tombol keyboard | `press` | selector, key dari enum schema |
| Dropdown native | `select_option` | selector, string/list value, select_by value/label |
| Checkbox/radio | `check`, `uncheck` | selector; radio hanya check |
| Reload, storage tetap dalam tes | `reload` | tanpa selector/value |
| Navigasi internal dan URL | `navigate`, `assert_url` | value berupa path dari /; tanpa selector |
| Teks seluruh elemen / teks sebagian | `assert_text`, `assert_contains_text` | selector, string value |
| Jumlah elemen | `assert_count` | selector, integer 0..100 |
| Visible/hidden, enabled/disabled | `assert_visible`, `assert_hidden`, `assert_enabled`, `assert_disabled` | selector |
| Nilai kontrol / multiple select | `assert_value`, `assert_values` | selector, string / list string value |
| Checked/unchecked | `assert_checked`, `assert_unchecked` | selector |
| Atribut DOM | `assert_attribute` | selector, attribute, string value persis |
| Native alert/confirm/prompt | `click_dialog` | selector, dialog object type/message/accept/prompt_text |
| Upload fixture teks | `upload_file` | selector, file object name/mime_type/content |
| Download | `download` | selector yang diklik untuk memicu satu download |
| Verifikasi download | `assert_download` | download object; tanpa selector/value |

`select_by` wajib eksplisit pada plan baru; default value hanya untuk kompatibilitas suite lama. List selection maksimal 20 string unik dan cocok
untuk multiple select. Custom dropdown memakai click pada trigger dan opsi;
select_option tidak dipaksakan pada elemen selain select. Fill tidak menekan
Enter dan tidak boleh dipakai pada select, checkbox/radio atau file input.
Missing/disabled control dan nilai opsi yang tidak tersedia tidak otomatis
dianggap kesalahan tes: bisa merupakan bug aplikasi dan tetap failed.

## Contoh operasi baru

```json
{"action":"select_option","selector":"[data-testid=\"input-jenis\"]","value":"pemasukan","select_by":"value"}
{"action":"check","selector":"#selesai"}
{"action":"assert_checked","selector":"#selesai"}
{"action":"assert_disabled","selector":"#export"}
{"action":"assert_attribute","selector":"#jumlah","attribute":"min","value":"1"}
{"action":"click_dialog","selector":"#hapus","dialog":{"type":"confirm","message":"Hapus transaksi?","accept":true}}
{"action":"upload_file","selector":"#import","file":{"name":"kas.csv","mime_type":"text/csv","content":"tanggal,nominal\n2026-10-07,100\n"}}
{"action":"download","selector":"#export"}
{"action":"assert_download","download":{"filename":"kas.csv","csv_rows":[["tanggal","keterangan","jenis","nominal"],["2026-10-07","Kopi, \"susu\"","pengeluaran","10000"]]}}
```

Setiap baris adalah contoh Step, bukan suite siap jalan. Suite tetap menyatakan
test ID, UAC, purpose dan steps dalam test yang sama. Assertions sebelum/selepas
mutasi harus menjelaskan perilaku sebenarnya; browser context tiap test baru.

## Download, fixture dan batas akses

Fixture hanya teks/CSV/JSON inline, nama file satu komponen dan maksimal 10000
karakter; tidak membaca file pengguna, secret, database kontrol, atau host path.
Download hanya file yang diterima browser setelah klik pada target. File dibaca
di direktori sementara runner, maksimal 1 MiB, bukan dari mount data kontrol.
Assertion dapat membandingkan filename, UTF-8 text persis, substring nonempty,
atau seluruh parsed CSV rows termasuk header. CSV menangani koma, quote,
newline dalam quoted field, CRLF dan BOM UTF-8. Mismatch tetap gagal. Bukti
download mencatat filename, ukuran dan SHA256; isi lengkap tidak disimpan di report.
Mismatch CSV mencatat jumlah rows/cells berbeda dan paling banyak delapan diff
expected/actual, masing-masing nilai maksimal 200 karakter dengan tanda truncation.
Indeks row/column dimulai dari nol. Diff bukan otoritas untuk mengganti expected.
`csv_rows` memakai nilai sel asli: input `Kopi, Susu` tetap `Kopi, Susu`, dan
`Gaji "Bulanan"` tetap memiliki satu pasangan kutip literal. Kutip pembungkus
dan doubling pada serialisasi CSV tidak termasuk nilai sel. Pakai `text` untuk
memeriksa format CSV mentah. Planning menolak token CSV berbungkus yang terbukti
mewakili input fill sebelumnya, tanpa mengubah expected otomatis. Input literal
yang memang berisi kutip pembungkus tidak ditolak oleh aturan ini.
DownloadExpectation perlu paling sedikit satu ekspektasi; klik Export saja
bukan verifikasi file. Expected CSV maksimal 101 rows, 32 cells/row dan 1000
karakter/cell, total expectation 64 KiB. Suite maksimum 512 KiB. Timeout harness,
memory/tmpfs/network isolation dan batas trace tetap berlaku.

Navigasi hanya origin target yang dipin. Hash routing diterima sebagai navigasi
same-document. Context tetap memblokir jaringan origin lain dan service workers.
Tidak menambahkan credentials ataupun shared writable mount dengan target.

## Kontrak UI dan locator — 10 Oktober 2026

Plan baru memakai vocabulary TL ui_contract. Runner memetakan semantic locator
ke get_by_role/get_by_label/get_by_text/get_by_test_id dengan exact name/text.
Suite legacy tetap dapat memakai CSS/engine syntax sebelumnya. Contoh:

```json
{"action":"click","selector":"testid=customer-save"}
{"action":"fill","selector":"testid=customer-name-input","value":"Alice"}
{"action":"assert_text","selector":"testid=customer-name","value":"Alice"}
{"action":"click","selector":"testid=customer-row >> has_text=\"Alice\" >> testid=customer-delete"}
```

Kontrol Hapus di contoh terakhir mempunyai scope_testid customer-row, dan parent
mempunyai dynamic_text=true. Alice harus original fill/select input pada test/fixture itu.
Selector declared boleh memakai suffix ` >> nth=0` (0..99) untuk disambiguasi.
Role/label bukan CSS tebakan. Hidden state sebaiknya memakai testid karena role
locator normal tidak mencari hidden controls. Scope data/fixtures tetap terisolasi.
Supervisor memvalidasi vocabulary sebelum candidate dan pada target revision.

## Diagnosis dan koreksi

Failure kandidat menyertakan bounded aria snapshot/hash/truncation dan candidate
locators observed runner. Snapshot/log dipin pada evidence, tanpa expression dari
model. Kategori action_contract, selector_contract dan expectation_diagnosis tetap
membantu atribusi; tidak otomatis membuktikan bug aplikasi atau tes salah.

Seluruh koreksi sekarang memakai QaSuiteRevision, dengan explicit origin-step
mapping/source witnesses dan bounded diff. Identity, UAC, purpose, inputs dan
original assertions/expected dipertahankan. Penambahan UI action memakai original
input atau exact proven fixture; no new expected dari actual. Binding label dari
original fixture dan normalisasi parsed CSV dari input adalah satu-satunya koreksi
representasi data. Tidak ada jalur otomatis per alert/fill/CSV di runtime lagi.
Rincian invariant dan coverage witnesses: [policy QA](qa-policy.md).

Setiap revision menciptakan target/suite baru, mempertahankan histori dan menuntut
full candidate plus exact baseline verification. Diagnosis/DOM/concern tidak
approve QA, waive, drop test, downgrade manual atau membuka UAT tanpa execution.
Unsupported/unknown/infra tetap explicit; hanya qualified application defect
meminta source repair. Batas existing suite repair tidak direset.

## Kompatibilitas dan keterbatasan

Field opsional baru dihilangkan saat kosong agar serialization/digest suite
lama tidak berubah. Runner code digest tetap berubah dengan implementation
baru. Target lama tidak boleh dieksekusi dengan runner baru tanpa target/QA
baru. Runner identity mismatch memerlukan target baru dan eksekusi baru. Target
review_approved yang masih di QA dapat di-refresh oleh supervisor; mismatch
lain tetap failure infrastruktur yang eksplisit. Restart tidak menghapus blocker repair yang sudah
ada atau mengubah kandidat/approval lama.

Belum didukung: arbitrary scripts/regex/code, iframe/popup, drag/drop, clipboard,
binary upload, multi-file fixture, external auth/API, backend DB, payment asli,
perangkat mobile/native, dan snapshot visual sebagai otoritas acceptance.
Jika UAC membutuhkan capability tersebut, planning wajib menyatakan gap runner
dan kebutuhan dukungan/keputusan. Jangan mengarang operasi, membuang UAC,
menjadikan manual tanpa keputusan pengguna, atau mengubah produk agar sesuai
dengan keterbatasan metode tes. DSL yang lebih lengkap tidak menjamin tidak
ada kegagalan: bug aplikasi, kesalahan test data dan infrastruktur tetap mungkin.

API Playwright dipetakan ke dokumentasi resmi [actions](https://playwright.dev/python/docs/input),
[downloads](https://playwright.dev/python/docs/downloads) dan
[assertions](https://playwright.dev/python/docs/test-assertions).
Verifikasi implementasi: AST/import/schema Python dan TypeScript statis;
tes regresi tidak ditambah/dijalankan. Browser demo yang sudah diizinkan
dijalankan kembali setelah perbaikan; lihat bukti terbaru di backlog.


## Fixture UI dan binding relasi

Plan dapat memuat `fixtures: [{id, steps}]`; setiap BrowserTest menyebut
`fixture_ids`. Supervisor menambahkan setup ke awal setiap tes pada context
terisolasi sebelum hashing/pinning. Fixture hanya berisi navigate/fill/click/
select_option/press/check/uncheck; assertion tetap wajib pada tes. Referensi
harus valid, unik, dan semua fixture dipakai; total hasil ekspansi maksimal 30
steps per tes. Plan authored dan canonical suite disimpan sebagai artefak
terpisah; canonical suite tetap attachment terakhir pesan qa_plan.

Record buatan fixture dipilih dengan `select_by: label` dan nama unik yang
benar-benar diinput. Nilai ID acak tidak boleh ditebak dari nomor urut. Runner
merekam paling banyak 200 opsi dari satu select visible/enabled ketika value
permintaan tidak ditemukan. Setelah diagnosis test fault, model hanya memilih
indeks input asal dan langkah select; supervisor menerima binding jika label
input asli cocok dengan tepat satu opsi enabled yang diamati. Semua assertion,
UAC dan input tetap, lalu baseline/kandidat dijalankan ulang pada target baru.
Opsi hilang/ambigu tanpa bukti input tidak diperbaiki otomatis dan belum
membuktikan apakah aplikasi atau tes yang salah.

Diagnosis application wajib memetakan setiap failed test ke UAC otomatis yang
disetujui dan kutipan source kandidat yang cocok persis (12..400 karakter).
Guard menahan attribution tanpa bukti, selector/action contract yang belum
terselesaikan, mode select yang tidak jelas, atau expected CSV yang bertentangan
dengan input. Tidak ada repair cycle aplikasi yang dikonsumsi dari kasus ini.
Kutipan source adalah syarat bukti minimum, bukan pembuktian formal semantik;
diagnosis ambiguous tetap di QA dan tidak memperoleh pass.

Perubahan identitas trusted browser runner dapat me-refresh target yang belum
diterima dan masih review_approved di QA. Source/build/config/base dan suite
harus tetap dipin; target lama immutable, target baru memakai runner baru,
seluruh baseline/kandidat wajib dijalankan ulang. Refresh runner tidak memakai
jatah koreksi suite dan tidak memindahkan approval UAT/release lama.


Value statis yang disebut eksplisit oleh suite dan dideklarasikan dalam kutipan
source dapat menghasilkan application diagnosis saat opsi nyata hilang. Guard
mengharuskan literal value pada kutipan source untuk membedakan kasus tersebut
dari ID record yang ditebak. Mode selection, UAC dan bukti source tetap wajib;
hasil model bukan jaminan semantik, dan kontrak yang ambigu tetap di QA.

## Diagnostik runner — 10 Oktober 2026

Runner tetap mengeluarkan report setiap test, termasuk bounded aria/locator observations
pada failure kandidat. Snapshot disimpan pada report dan artefak log. Kandidat menyimpan screenshot dan
zip action trace hanya untuk test gagal; test lulus membuang trace tanpa export.
Trace tidak merekam snapshot DOM atau screenshot per langkah. Eksekusi baseline
mematikan trace/screenshot karena kegagalan fitur pada base memang diharapkan.
Laporan baseline input identik dapat dipakai kembali dengan provenance asal;
QA kandidat tetap dieksekusi baru. Detail: [audit performa](../audits/performance-caches-context-2026-10-10.md).

Kontrak baru memakai action_locators=testid: kontrol akhir untuk aksi harus testid.
Role/name/label/text tetap tersedia untuk assertion. QA dapat meminta additive
contract amendment ke TL sebelum development melalui request_contract_amendment.
