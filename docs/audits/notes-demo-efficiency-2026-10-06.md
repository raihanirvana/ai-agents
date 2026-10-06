# Audit efisiensi demo catatan — 6 Oktober 2026

Status: audit selesai; belum ada implementasi optimasi atau review independen.
Scope: proyek `25f7b5e9019646e0b13908fa59fa8b59` (nama `test`), satu tiket
catatan dengan tambah, tampilkan, hapus, dan browser storage. Data dibaca setelah
release berhasil diekspor. Runtime/provider tidak diubah selama audit.

## Ringkasan pengukuran

| Peran | Model calls | Input | Output | Total tercatat | Biaya tercatat USD |
|---|---:|---:|---:|---:|---:|
| PO | 1 | 3.485 | 368 | 3.853 | 0,00142325 |
| TL | 7 | 97.453 | 13.324 | 110.777 | 0,03318966 |
| Developer | 46 | 921.622 | 21.736 | 943.358 | 0,05493127 |
| QA | 1 | 6.141 | 404 | 6.545 | 0,00214125 |
| **Total** | **55** | **1.028.701** | **35.832** | **1.064.533** | **0,09168543** |

Angka merupakan jumlah `Job.usage` per job proyek, bukan penjumlahan pool
budget yang akan menghitung peer berkali-kali. `prompt_tokens` adalah alias
input dan tidak dijumlahkan lagi. Tiga review TL gagal mempunyai usage/biaya
unknown; total di atas adalah minimum tercatat, bukan tagihan final provider.
Sekitar 96,6% token tercatat adalah input dan 88,6% total milik developer.
Ini pemakaian alur aktual dengan gangguan/retry, bukan kebutuhan minimum fitur.

## Developer: pembagian attempt dan generation

| Job / generation | Calls | Input | Output | Total | Sumber |
|---|---:|---:|---:|---:|---|
| `b00b5079…` / 1, dihentikan budget | 13 | 191.981 | 8.597 | 200.578 | log relay, usage DB |
| `de004a38…` / 1, meminta keputusan | 5 | 49.741 | 616 | 50.357 | log relay, conversation |
| `de004a38…` / 2, submit berhasil | 28 | 679.900 | 12.523 | 692.423 | log relay, conversation |

Retry tidak mereset usage. Dua generation terakhir dijumlahkan dalam job
`de004a38e4b34aa6b953d2626d04fca2`. Field `input_tokens` pada JSON hasil Hermes
bernilai 41.948 untuk generation 2, sedangkan relay mencatat 679.900 kumulatif;
auditor menggunakan relay dan JobQueue, bukan memperlakukan field runtime itu
sebagai total authoritative.

## Temuan dan urutan perbaikan

### 1. Histori tool dan versi file lama ikut dikirim ulang

Pada generation 2, input naik dari **7.967** menjadi **40.670** token per call
(5,1 kali). 28 calls menghabiskan 679.900 input tokens. Conversation akhirnya
memuat 65 messages, 69.399 karakter hasil tool, dan sekitar 39.933 karakter
konten file yang dikirim melalui write tools. Isi write dan read sama-sama
menjadi histori; versi yang kemudian diganti tetap berada di conversation.

`HermesDriver` dan worker child secara eksplisit menonaktifkan compression.
`ContextBuilder` membatasi snapshot awal, tetapi tidak merangkum transcript
Hermes yang bertambah selama satu generation. Snapshot developer generation 2
sendiri hanya 7.156 karakter system dan 10.941 karakter user; pembengkakan
selanjutnya terlihat pada usage provider, bukan hanya ukuran snapshot awal.
Tool schemas dan instruksi runtime juga berkontribusi; payload request mentah
per call tidak tersimpan sehingga kontribusinya tidak bisa dipisahkan persis.

**Usulan:** compaction yang dimiliki platform: simpan goal, scope/version,
base/candidate, keputusan/feedback terbaru, daftar file+digest saat ini,
check yang sudah dijalankan dan outstanding failures. Ganti salinan file lama
serta log sukses panjang dengan referensi/digest dan ringkasan, sambil menjaga
transcript asli sebagai bukti. Jangan sekadar mengaktifkan compaction Hermes
umum yang saat ini dilarang kontrak runtime. Ukur kembali pada demo yang sama;
penghematan belum dibuktikan.

### 2. Tool bernama patch sebenarnya menimpa seluruh file

`pipeline/runtime.py` menerima `patch_file(path, content)` dan memanggil
`sup.write_file`: seluruh isi diganti. Schema dan instruksi menyatakan full
content, tetapi model tetap dua kali mengirim potongan: `src/notes.js` hanya
447 karakter (sebelumnya sekitar 4.889), dan `tests/app.test.js` hanya 131
karakter (sebelumnya sekitar 6.421). Setelah itu file dibaca/ditulis penuh lagi.

Generation 2 melakukan **9 writes**, **15 reads**, **8 run_command**, dan **2
submit**. `tests/app.test.js` dibaca 5 kali dan ditulis 6 kali (29.713 karakter
konten writes); `src/notes.js` dibaca 3 kali dan ditulis 3 kali. Empat eksekusi
repo test gagal, lalu yang kelima lulus 10/10. Error meliputi syntax dan bug
fake DOM buatan test. Audit tidak menyimpulkan bahwa setiap error tes disebabkan
partial write; bukti hanya memastikan partial replacement dan loop perbaikan.

**Usulan prioritas tinggi:** bedakan `write_file` (full replacement) dari
`edit_file` (old/new exact match, expected digest, unique occurrence). Return
metadata path/digest/change summary. Partial edit ditolak jika precondition
berbeda. Batasi perubahan ke snapshot dan pertahankan fencing/izin. Pertimbangkan
reference test utilities untuk stack baru agar agent tidak berulang membuat fake
DOM besar; unit tests tetap menguji kode aplikasi dan QA browser tetap independen.

### 3. Batas tool menghasilkan error tanpa jalur untuk mengambil bagian relevan

Pada generation 2, `inspect_diff` dan pembacaan `package-lock.json` mengembalikan
`tool output exceeded 65536 bytes`. Read/diff hanya menawarkan seluruh hasil,
tanpa pagination atau pemilihan file; pembatas relay menghilangkan hasil total.
JSON escaping dapat membuat payload melewati batas walaupun source <64 KiB.
Batas transport ini berbeda dari budget token dan diperlukan untuk resource
safety. Menaikkan budget token tidak menyelesaikan masalah tersebut.

**Usulan:** read dengan range/cursor, diff stat/per-file/per-hunk, hasil memiliki
size/digest/truncated/next_cursor. Jangan hilangkan bukti atau diam-diam menerima
hasil terpotong. Tool schemas memberi batas message submit (`maxLength: 2000`):
submit pertama ditolak karena pesan terlalu panjang, kedua berhasil. Bulk read
bukan temuan utama: model sudah menggabungkan 4–7 read tools dalam satu turn.

### 4. TL dikirimi lockfile mentah dan bukti verbose

Snapshot review TL berisi 6.563 karakter system dan **112.467 karakter user**.
Diff asli 83.268 karakter, termasuk **61.779 karakter package-lock.json (74,2%)**.
Ukuran JSON-escaped diff 90.282 karakter, repo gate 13.658 karakter, dan schema
output 424 karakter disertakan dua kali (`schema` dan `output_schema`).
Kelima percobaan review membawa diff/gate yang sama dan ukuran snapshot sama;
identitas run berbeda. Provider mengukur sekitar
45.962 input tokens pada dua percobaan yang usage-nya tersedia. Empat review
gagal sebelum review kelima berhasil. Snapshot yang besar bukan satu-satunya
penyebab retry: output reasoning terpotong di call cap 4.096 merupakan kegagalan
terpisah yang sudah didiagnosis pada log streaming.

**Usulan:** review source/test diff penuh yang relevan, dependency summary
terstruktur (name/version/resolved/integrity dan perubahan graph), ringkasan
repo gate dengan failed IDs, serta akses ke bukti/diff lengkap yang disimpan.
Validasi lockfile deterministik tetap wajib; jangan sekadar membuang lockfile
dari peninjauan. Hilangkan schema duplikat. Jika review memerlukan inspeksi
lanjutan, sediakan read-only tool untuk artefak yang dipin; structured reviewer
saat ini tidak dapat mengambilnya sendiri.

### 5. Sebagian besar waktu developer menunggu tool/infrastruktur

Usage DB mencatat developer aktif sekitar **936 detik (15,6 menit)** untuk dua
jobs. Pasangan event logs dengan resolusi detik menunjukkan sekitar **190 detik
model** dan **727 detik tool**. Ini jumlah interval log, bukan profiler CPU;
sebagian call tidak memiliki pasangan lengkap dan tidak seluruh overhead tercakup.
Generation 2: model sekitar 96 detik; tool sekitar 325 detik, termasuk install
240 detik dan submit 69 detik. Attempt awal memiliki dua tool waits 247 dan
152 detik; command evidence mencatat `docker create timed out` pada install.
Generation 2 mencoba build sebelum install (`vite: not found`).

`submit` membangun snapshot commit immutable dan menjalankan gate lagi walaupun
agent sudah build/test. Kebutuhan target immutable sah; pengulangan download
bisa dihemat tanpa menerima hasil dari source yang berbeda.

**Usulan:** health/progress infrastruktur terlihat di UI; download progress
tersanitasi; supervisor cache tarball berdasarkan SHA-512/integrity dengan
validasi bytes saat reuse, tanpa memberi target egress. Jangan cache hasil QA
untuk target berbeda. Tool menyebut prasyarat install pada workspace baru/resume.
Bedakan transient infra failure dari code/test failure; jangan mengirim agent
untuk menulis ulang kode ketika Docker/jaringan bermasalah.

### 6. Observabilitas cache dan atribusi per call masih kurang

Relay produk menyimpan prompt/completion/total/cost, tetapi tidak mengakumulasi
`prompt_tokens_details.cached_tokens` dari Hermes ke Job.usage. Structured TL
mencatat 45.568 cached tokens pada satu call; cache Hermes belum bisa dibandingkan
secara konsisten. Event tool persisten menyimpan hash payload, bukan nama/argumen
ringkas. Transcript attempt awal yang dihentikan tidak tersedia; audit 13 calls
awalnya hanya bisa memakai log usage/timing dan command evidence. Transcript dua
generation retry tersedia dan mencakup 33 calls.

**Usulan:** metric per call: job/generation/ordinal, tool name, path/hash/size,
latency, status/exit, provider token/cache/cost dan perubahan snapshot. Jangan
menyimpan secret atau raw reasoning untuk dashboard. Pertahankan unknown bila
provider tidak melaporkan angka.

## Rencana implementasi yang disarankan

1. Tool contract: write/edit terpisah, read/diff bertahap, schema submit yang lengkap.
2. Projection konteks runtime: ringkas versi file/log lama dengan pin keputusan dan bukti.
3. Review context: dependency/gate summary deterministik, satu output schema.
4. Cache dependency terverifikasi dan progress/recovery infrastruktur.
5. Ulangi workload identik, bandingkan calls/tokens/cache/time/QA pada target baru.

Tidak mengubah scope/UAT/release, generation, finite defaults proyek lain, dan
isolasi sandbox. Belum ada angka target penghematan yang terbukti. Demo ini satu
sampel dengan gangguan Docker/DNS dan perubahan konfigurasi; tidak dapat menjadi
estimasi langsung biaya aplikasi POS.

## Bukti yang dibaca

- `Job.usage`, per-role model calls dan `_unknown`, 15 jobs proyek.
- Context artefak developer: `768c9f2830cf4252bbe3d1112558675b`,
  `6034e05e95f34709b8d6c5ac6464061c`, `56d0c7a8a5cd4aeab91b3b50b2e7e6e5`.
- Context review: `773000fdda4f439186437fc127cd6368`, empat snapshot review
  pendahulunya mempunyai ukuran sama; run identity berbeda.
- Conversation retry: `7b236062074b41c0b413251b0593c7be`,
  `8bd061e9f97e4c759d9b75c2ea40daba`. Raw conversation/last_reasoning tidak
  disalin ke laporan.
- Job logs: `de98d7c3dae542238235bd80abd24c15`,
  `2039eeb9d2a24aa5a86aa416a3bba931`, `bc553798822f42b1a18b6f1003d0dbc6`.
- Command evidence archive `run-4172f078942c` dan `run-4b2b9d290ea7`.
- Implementasi: `agents/context.py`, `agents/runtime.py`, `pipeline/runtime.py`,
  `pipeline/hermes.py`, `pipeline/relay.py`, `runtime_spike/hermes_worker.py`,
  `runtime_spike/relay.py`, `workspace/sandbox.py`, `workspace/dependencies.py`.

Tidak menjalankan tes, model call berbayar, rebuild, atau proyek baru saat audit.
Artefak/database runtime tetap lokal dan tidak disalin ke file tracked.

## Lampiran: seluruh 46 panggilan developer yang tercatat

Waktu sesuai log lokal; input/output merupakan nilai provider, bukan estimasi chars/4.

| Job / generation | Call | Waktu | Input | Output |
|---|---:|---|---:|---:|
| b00b5079 / 1 | 1 | 13:28:29 | 7020 | 66 |
| b00b5079 / 1 | 2 | 13:28:43 | 7113 | 2587 |
| b00b5079 / 1 | 3 | 13:28:56 | 9799 | 2396 |
| b00b5079 / 1 | 4 | 13:29:03 | 12233 | 1443 |
| b00b5079 / 1 | 5 | 13:29:10 | 13696 | 1430 |
| b00b5079 / 1 | 6 | 13:29:13 | 15146 | 61 |
| b00b5079 / 1 | 7 | 13:29:15 | 16862 | 71 |
| b00b5079 / 1 | 8 | 13:29:17 | 17029 | 40 |
| b00b5079 / 1 | 9 | 13:33:28 | 17102 | 50 |
| b00b5079 / 1 | 10 | 13:36:03 | 17191 | 40 |
| b00b5079 / 1 | 11 | 13:36:07 | 19393 | 181 |
| b00b5079 / 1 | 12 | 13:36:09 | 19594 | 189 |
| b00b5079 / 1 | 13 | 13:36:12 | 19803 | 43 |
| de004a38 / 1 | 1 | 13:38:49 | 7108 | 78 |
| de004a38 / 1 | 2 | 13:38:52 | 7443 | 238 |
| de004a38 / 1 | 3 | 13:39:01 | 11536 | 63 |
| de004a38 / 1 | 4 | 13:39:07 | 11770 | 43 |
| de004a38 / 1 | 5 | 13:39:17 | 11884 | 194 |
| de004a38 / 2 | 1 | 13:40:02 | 7967 | 147 |
| de004a38 / 2 | 2 | 13:40:04 | 9888 | 140 |
| de004a38 / 2 | 3 | 13:40:06 | 11586 | 25 |
| de004a38 / 2 | 4 | 13:40:07 | 11634 | 40 |
| de004a38 / 2 | 5 | 13:40:11 | 12798 | 43 |
| de004a38 / 2 | 6 | 13:40:18 | 12864 | 1634 |
| de004a38 / 2 | 7 | 13:40:24 | 14518 | 1219 |
| de004a38 / 2 | 8 | 13:40:25 | 15757 | 66 |
| de004a38 / 2 | 9 | 13:40:28 | 17692 | 197 |
| de004a38 / 2 | 10 | 13:40:30 | 17909 | 43 |
| de004a38 / 2 | 11 | 13:40:36 | 18113 | 1238 |
| de004a38 / 2 | 12 | 13:40:38 | 19371 | 43 |
| de004a38 / 2 | 13 | 13:40:40 | 20835 | 55 |
| de004a38 / 2 | 14 | 13:40:47 | 22028 | 1644 |
| de004a38 / 2 | 15 | 13:40:50 | 23692 | 65 |
| de004a38 / 2 | 16 | 13:40:51 | 25640 | 51 |
| de004a38 / 2 | 17 | 13:40:55 | 27577 | 107 |
| de004a38 / 2 | 18 | 13:41:02 | 27704 | 1645 |
| de004a38 / 2 | 19 | 13:41:05 | 29369 | 74 |
| de004a38 / 2 | 20 | 13:41:07 | 31334 | 85 |
| de004a38 / 2 | 21 | 13:41:14 | 33291 | 726 |
| de004a38 / 2 | 22 | 13:41:23 | 34037 | 2019 |
| de004a38 / 2 | 23 | 13:41:25 | 36076 | 94 |
| de004a38 / 2 | 24 | 13:41:28 | 38471 | 68 |
| de004a38 / 2 | 25 | 13:41:32 | 39461 | 61 |
| de004a38 / 2 | 26 | 13:45:35 | 39561 | 40 |
| de004a38 / 2 | 27 | 13:45:47 | 40057 | 569 |
| de004a38 / 2 | 28 | 13:45:52 | 40670 | 385 |
