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

## Intervensi platform: plafon konteks saat repair Developer

Developer pertama selesai dan membuat kandidat; TL meminta perubahan. Job
repair `15c392563513404e99d838651c57b180` kemudian gagal sebelum model call:
konteks wajib sekitar 9.606 token ditolak oleh ContextBuilder default 8.000.
Semua budget job sudah null dengan `unlimited_budgets=true`; seluruh role pada
konfigurasi model lokal juga memiliki `max_output_tokens=null`. Penyebabnya
adalah plafon konteks aplikasi yang belum mengikuti policy unlimited.

ContextBuilder sekarang membaca policy dari job DB yang cocok dengan identitas
proyek/tiket/scope. Pada job unlimited, plafon total konteks aplikasi dihapus dan
manifest mencatat `limit_tokens=null`; konfigurasi builder bersama tidak diubah.
Job finite tetap dibatasi, dan task/model tidak dapat mengaktifkan policy ini.
Approval, scope/version, pemilihan history opsional dan usage tetap berlaku.

Regresi: `pytest tests/agents tests/pipeline/test_review_context.py
tests/pipeline/test_ui_spec_stage.py -q --tb=short`: **271 passed, 57,86 s**.
Mencakup semua lima role, konteks wajib di atas 32.768 token, penolakan policy
palsu dari task dan scope yang belum di-approve. Worker dimuat ulang saat idle;
retry `8bdc1ba23629478ea10320201816fb69` berjalan. Snapshot nyata
`184096d6ebc24d7da580101dd11c26bf`: 11.655 estimated tokens, `limit_tokens=null`.
Cap job model/tool/time/input-output tetap null, approval dan histori dipertahankan.

## Tiket #1 accepted dan repair UI/UX tiket #2

Retry Developer #1 succeeded. Kandidat `e331df9c34f34e458dcef0cb29a1d743`
lulus QA pertama: verification `7a63cec5888c49b2a52657c3a8800906`,
3 discovered/executed/passed, 0 failed/skipped. Pengguna menerima lewat UAT
`7c2801760fa6478aa98fd8437aa4f1b1`; tiket #1 accepted. Approval ini tidak
berasal dari observer dan tidak menggantikan approval tiket berikutnya.

UI/UX #2 job `7cfcca6a6fb24131bf9d615994d32896` gagal dua calls: komponen
tanpa role/label/text, lalu feedback ganda untuk pasangan action/outcome.
Repair sebelumnya hanya menerima error dan schema, tanpa JSON yang ditolak.
Runtime terstruktur sekarang menyertakan jawaban sebelumnya sebagai data JSON
yang di-escape dan meminta koreksi field invalid dengan mempertahankan bagian
valid. Pada mode unlimited seluruh jawaban disertakan; untuk konteks finite,
jawaban besar tidak ditambahkan jika melewati plafon snapshot.

Validator menyebut testid komponen serta setiap pasangan feedback konflik,
indeks entri dan copy_key. Schema/instruksi UI/UX menjelaskan identitas minimum
komponen dan satu feedback per pasangan. Validator tidak meluluskan duplikat
atau menghapusnya diam-diam. Regresi `tests/agents` +
`tests/pipeline/test_review_context.py` + `tests/pipeline/test_ui_spec_stage.py`:
**276 passed, 61,70 s**, termasuk repair output lengkap di mode unlimited.
Worker dimuat ulang saat idle; retry `919088c5b1df402caf256a2f437143ba`
gagal karena batas waktu respons provider 120 detik, saat stream masih mengirim
jawaban. Satu model call tercatat; token/biaya tidak dilaporkan provider dan
tetap unknown. Ini berbeda dari batas token/context demo, yang tetap unlimited.
Konfigurasi lokal/gitignored UI/UX dinaikkan ke `timeout_s=600`, masih dalam
rentang timeout yang didukung registry; `max_output_tokens=null` dipertahankan.
Worker dimuat ulang saat idle dan retry `f4165e3f23c14ddead1a40567a5b8c19`
gagal: stream berakhir dengan `finish_reason=stop`, tetapi `answer_chars=0`
dan reasoning 20.649 karakter. Usage tercatat 20.574 total token (8.471 completion,
5.043 reasoning), reported cost USD 0,00519387, active 186,40 s. Tidak ada
JSON final untuk divalidasi; reasoning tidak dipakai sebagai spesifikasi.
Ini tidak membuktikan validator gagal atau membenarkan melonggarkannya.

UI/UX lokal kemudian memakai `stream=false`, tetap provider/model/routing,
reasoning effort dan batas token yang sama, agar mencoba respons JSON utuh
melalui parser non-streaming yang sudah tersedia. [Dokumentasi OpenRouter](https://github.com/OpenRouterTeam/docs/blob/main/guides/best-practices/reasoning-tokens.mdx)
membedakan reasoning dari final content pada respons non-streaming. Tidak ada
perubahan protokol adapter atau pemakaian reasoning sebagai output.
Worker dimuat ulang saat idle; retry `bfc19bcdf66d4d7da6101f9b51de9bea`
gagal dua calls: respons pertama bukan JSON, respons repair berupa UiSpec tetapi
summary melewati 1.000 karakter. Total 42.919 token, reported cost USD 0,008675406,
active 215,66 s. Instruksi UI/UX kini menjelaskan respons JSON langsung sesuai
output_schema (bukan membungkusnya dalam pemanggilan tool), dan summary satu–tiga
kalimat maksimal 1.000 karakter. Deskripsi field summary juga memuat arahan ini.
Batas panjang tidak dilonggarkan. Verifikasi setelah perubahan instruksi/metadata:
`pytest tests/agents/test_ui_spec.py tests/pipeline/test_ui_spec_stage.py
tests/agents/test_ui_ux_role.py -q --tb=short`: **102 passed, 37,83 s**.
Worker dimuat ulang saat idle; retry `de94140899d14f5bbc936cc77b223934`
**succeeded** dalam satu model call nyata (`fake_provider=false`): 21.297 token,
reported cost USD 0,005949, active 122,89 s. Message
`ad6baf72353a4e259d79f53237abacab` menyimpan UiSpec revisi 1, summary 511
karakter, 23 komponen dan 7 feedback; objek tersimpan divalidasi ulang berhasil.
TL `09f72cc808b24e06ae39853a0b77a0b8` kemudian succeeded. Tiket #2 masuk
Development: job Developer `27bdae322a204bee914f3acdde08e802` dan QA planning
`65f938ac787b4d9797b194f9a9485640` running. Tiket #3 menunggu dependency.
Belum ada klaim QA/UAT tiket #2 lulus; histori seluruh retry dan unknown usage
pada stream terputus tetap masuk benchmark.
