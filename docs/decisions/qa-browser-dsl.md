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

`select_by` default value. List selection maksimal 20 string unik dan cocok
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

## Diagnosis dan koreksi

Kesalahan native-control/action yang diamati runner adalah `action_contract`.
Strict locator ambiguity tetap `selector_contract`. Keduanya menjadi kategori
`test_contract`; aplikasi tidak otomatis diminta berubah atau dihitung repair.
Mismatch parsed CSV diberi `expectation_diagnosis` dan tetap di QA untuk diagnosis
terhadap UAC/input. Ini bukan klaim aplikasi benar atau expected salah. Tidak ada
repair otomatis expected dari hasil download. Defect aplikasi yang terkonfirmasi
tetap harus dikirim melalui request-changes dengan bukti/reproduksi.
Runner membaca fakta DOM dengan fungsi tetap milik runner; model tidak dapat
menyisipkan expression JavaScript dalam DSL.

Koreksi otomatis dibatasi pada dua kasus:

1. assert_visible ambigu dengan tepat satu visible alert dan ID unik yang aman.
2. fill pada tepat satu visible/enabled native select, dengan tepat satu enabled
   option bernilai persis value yang diminta. Operasi berubah menjadi
   select_option by value; selector/value dan seluruh assertion tidak berubah.

Semua kegagalan harus memenuhi syarat untuk koreksi. Mixed/unknown failure,
missing option, ambiguous option, salah control lain, atau batas dua koreksi
per rantai target tetap failed dan memerlukan diagnosis QA. Tidak ada waiver,
assertion dihapus, expected value diganti, aplikasi ditulis ulang, atau pass
yang diperoleh dari diagnosis. Verification lama tetap immutable. Suite/target
baru pada source/build/config/runner yang sama harus menjalankan ulang semua
candidate tests dan baseline bila applicable sebelum UAT pengguna.

## Kompatibilitas dan keterbatasan

Field opsional baru dihilangkan saat kosong agar serialization/digest suite
lama tidak berubah. Runner code digest tetap berubah dengan implementation
baru. Target lama tidak boleh dieksekusi dengan runner baru tanpa target/QA
baru. Runner identity mismatch tetap failure infrastruktur yang eksplisit,
bukan application repair. Restart tidak menghapus blocker repair yang sudah
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
tes regresi/browser/provider tidak ditambah/dijalankan karena belum diminta.
