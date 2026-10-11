# Demo provider nyata Mini Perpustakaan — 11 Oktober 2026

Status demo: **SELESAI** — ketiga tiket accepted melalui UAT pengguna;
ketiganya lulus QA otomatis. Snapshot final 11 Oktober 2026, 13:52 WIB.
Bagian hasil sementara/intervensi di bawah adalah histori; hasil final ada di
bagian **Benchmark final**. Review perubahan platform tetap NOT_REVIEWED.
Proyek baru: `db758f7ce3ef43a697b7410b173d31bd`.
Kode saat mulai: `d2361aa`. Bukan benchmark sintetis atau provider fake.

## Workload dan kondisi

Tiga tiket mengikuti demo lama `b909df3151604b5a8315dead0d366042`:
katalog, peminjaman/pengembalian, pencarian/ringkasan. Dependency accepted
berurutan #1 → #2 → #3. Fungsi utama automated; visual katalog manual.
Tiket #2 menambah UAC eksplisit persistence status pinjaman, sehingga workload
mirip tetapi tidak identik sepenuhnya. Tidak ada fitur ekstra.

API dan worker di-restart dengan kode baru; DB 0005 → 0008 setelah SQLite
backup di data/backups. Web Vite lokal tetap aktif. Provider nyata dari
agents/models.json: PO/QA google/gemini-3.1-flash-lite; TL/Developer
DeepSeek V4.1 Flash via OpenRouter, fallback GLM 5.3 Flash sesuai konfigurasi.
Budget demo unlimited tersimpan pada proyek, usage tidak direset.

Pengguna memilih review dan approve scope di web, kemudian ketiga scope v1
sudah disetujui. Observer tidak melakukan approval, retry atau waiver otomatis.
UAT dan release tetap keputusan pengguna; tiket berikutnya menunggu accepted
dependency, bukan hanya QA/UAT tersedia.

Cache reference tarball/installation lokal sudah berisi run sebelumnya.
Ini bukan perbandingan cold cache. Efek kode, cache, provider dan perilaku model
belum dipisahkan; satu demo tidak membuktikan kecepatan umum.

## Hasil sementara tiket #1

| Ukuran Developer | Demo lama #1 | Demo baru #1 |
| --- | ---: | ---: |
| Model calls | 35 | 20 |
| Input + output tokens | 769.631 | 323.577 |
| Reported provider cost USD | 0,087454224 | 0,0054967 |
| Active seconds | 107,70 | 205,35 |

Token berkurang sekitar 58%, tetapi waktu Developer tiket pertama meningkat.
Belum boleh menyebut seluruh demo lebih cepat atau membandingkan total baru
satu tiket dengan 19,54 juta token seluruh proyek lama.

Fase Developer baru (inclusive; saling overlap, jangan dijumlahkan sebagai wall
time): model 106,54 s; tool 90,59 s; install 52,74 s (4 invocations, 3 cache hits);
repo tests 8,47 s; build 23,94 s; checks 66,59 s. Dua checks gagal diperbaiki
dalam job yang sama sebelum kandidat disubmit. TL review satu call / 8,31 s model;
QA browser 8,90 s. QA planning paralel sudah selesai sebelum submit.

- Tiket #1 lulus QA otomatis dan berada di UAT; verification
  `9b207cda775c4cb484001bff422513bc`, `fake_provider=false`.
- Tiket #2 dan #3 belum mulai: dependency accepted belum terpenuhi.
- Rollover Developer #1: **0**, dihitung dari archived transport events.
- QA keputusan manual: **0**; needs_human QA: **0**; dikembalikan sebagai bug
  aplikasi oleh QA: **0**. Ini hitungan sementara, bukan hasil tiga tiket.
- Scope approval waits sekitar 97–103 s per tiket yang berjalan paralel;
  UAT wait masih ongoing. Jangan menjumlahkannya sebagai latency model.

## Cara mengambil data

Dari apps/backend:

```sh
.venv/bin/python -m app.pipeline.metrics --project-id db758f7ce3ef43a697b7410b173d31bd
```

Data gitignored: `data/demo-library-2026-10-11/`.
`current-project-metrics.json` adalah output CLI asli. Observer read-only
`collect_metrics.py` memperbarui setiap 30 s (maksimal 6 jam, berhenti ketika
ketiga tiket accepted/cancelled): `current-demo-summary.json` mengagregasi usage
Developer per tiket, fase, rollover archived dan klasifikasi outcome QA dari
verification/waiver/job records. User approval waits ada pada output CLI asli.
`previous-project-metrics.json` menyimpan snapshot CLI proyek lama.

Rollover untuk attempt aktif yang belum mengarsipkan transport masih pending;
bukan klaim nol. Fase yang tidak direkam pada job lama adalah unavailable.
Input/prompt alias tidak dijumlahkan dua kali; biaya adalah reported usage,
bukan invoice independen. Laporan final menunggu seluruh demo selesai.

## Intervensi platform setelah tiket #1 accepted

Setelah pengguna menerima #1, planning #2 ditolak sebelum provider call karena
konteks wajib ~8.311 token melampaui ContextBuilder default 8.000. Job
`654ae31fc1ea4f019337b94398ab16a0` tetap failed dan tidak dihapus. Kapasitas
technical_plan dibuat selaras dengan review/diagnosis: minimal 32.768 token,
mempertahankan konfigurasi yang lebih besar. Budget total/output/model/tool/
active_s seluruh job demo tetap null, unlimited_budgets=true. Provider context
dan keselamatan relay tetap berlaku. Enam regresi contract amendment lulus.
Worker di-restart dan job planning di-retry melalui API tanpa reset usage.
Demo selanjutnya memakai patch ini di atas d2361aa; karena ada intervensi, bukan
perbandingan satu versi kode yang identik dari awal hingga akhir.

### Loop amendment QA yang ditemukan saat demo

QA planning #2 meminta kontrol borrow-name-input/borrow-book-button/return-book-
button/book-status yang sebenarnya sudah ada sejak UI contract revision 1.
Arsip percakapan menunjukkan ValueError pada locator
`testid=book-item >> testid=borrow-name-input`. Runner mendukung jalur ini,
tetapi whitelist UiContract hanya menyertakan root testid dan jalur dengan
has_text, belum pasangan parent/child langsung. Amendment additive TL tidak
dapat memperbaiki kekurangan validator tersebut, sehingga terjadi pengulangan.

UiContract sekarang mengizinkan pasangan scope_testid → testid yang persis
dideklarasikan. Parent salah, child undeclared dan akses root lain tetap ditolak.
12 regresi lulus (0,58 s), termasuk jalur langsung, nth dan tiga jalur terlarang.
Worker di-restart dengan source checkpoint Developer tetap disimpan dan budget
usage tetap kumulatif. Ini intervensi platform kedua, bukan bug aplikasi target;
biaya/waktu putaran sebelumnya tetap masuk laporan demo.

## Hasil sementara tiket #2 (QA perlu keputusan)

Developer selesai dengan 39 model calls, **824.425 total token**, USD 0,032867604,
180,30 s active. Demo lama Developer #2: 348 calls, 16.287.114 token,
USD 1,148858412. Waktu tunggu planning QA tercatat terpisah 50,32 s.
Fase Developer: model 59,44 s; tool 164,64 s; install 22,51 s (7 cache hits);
test 12,28 s; build 72,13 s. Banyak angka inclusive dan saling overlap.

QA browser: 3 discovered/executed, 2 passed, 1 failed. Verification
`dd4a805f1443450585ad0dbb87967535`. `return-book-journey` mengharapkan tombol
Pinjam enabled sesudah buku dikembalikan, sementara nama peminjam sudah kosong.
Diagnosis QA fault=test: tombol memang membutuhkan nama baru; UAC-4 mengharuskan
status kembali tersedia, bukan tombol enabled tanpa nama. Revisi otomatis
tertolak karena inserted action belum memiliki witness yang memenuhi validator.
Tidak ada request_changes dengan failure_kind=application.

Pilihan manual awalnya tidak tersedia: qualify() menuntut producer=verification
pada seluruh evidence_artifact_ids, tetapi runtime nyata juga melampirkan
execution-cache baseline-build/browser. Perbaikan menerima hanya receipt cache
yang ID/kind/key-nya dipin oleh acceptance proof authoritative. Receipt cache
tidak pernah menjadi sumber runner report; unsupported/unreferenced receipt
tetap ditolak. 25 regresi domain lulus (6,06 s), termasuk dua kasus baru.
API dan worker di-restart; HTTP detail sekarang qa_resolution.eligible=true
untuk **UAC-4 saja**. User diminta memilih koreksi otomatis atau ownership manual
di web. QA failed tidak diubah menjadi passed dan UAT tidak di-approve otomatis.

Demo masih berjalan; hasil #3 dan outcome manual final belum tersedia.

## QA dibatasi oleh UAC yang disetujui

User meminta QA hanya menguji UAC. Soul, instruksi, policy revision 4 dan task
planning/diagnosis diperketat: setiap ekspektasi harus memeriksa outcome atau
pembatasan approved UAC; selector contract tidak menambah kebutuhan produk.
Ekspektasi incidental seperti tombol enabled tanpa input wajib bukan dasar
request_changes aplikasi. Summary plan diminta memetakan UAC → outcome yang diuji.
Preflight plan baru menolak mapping manual/campuran dan non-smoke tanpa UAC.
Kode membuktikan referensi/mode, bukan implikasi semantik assertion dari UAC.
Tidak ada jalur PO baru atau perubahan approval/budget. Syntax AST dan diff
diperiksa; tes tidak ditambah/dijalankan pada perubahan ini. Suite lama tetap
immutable dan failed; validator repair belum mendukung mengganti expected/mode
semantik, sehingga perubahan instruksi tidak dianggap menyelesaikan failure lama.
Worker idle (tidak ada job running) di-restart tertib 96378 → 1320 agar policy
baru termuat. API/web/observer tetap berjalan; tidak memberi waiver, mereset
usage atau melanjutkan failed job otomatis.

## Retry koreksi QA #2 setelah user menanyakan stuck

Revisi ditolak memakai source_excerpt gabungan dengan `...` dan input `Alice`,
padahal input awal kasus return adalah `Bob`. Guard tetap: exact shipped source
dan input dari kasus yang sama. Feedback validator dipecah agar menjelaskan
penyebab spesifik; instruksi revision menegaskan contiguous excerpt dan same-test
input. Syntax AST dan diff diperiksa; tidak menambah/menjalankan unit tests.
Worker idle di-restart 1320 → 3174, lalu retry eksplisit melalui HTTP memakai
authorization `demo-library-t2-qa-exact-witness-retry-20261011-v1`.
Retry job `ab534b7492d34133b38cbc3f4d695403` running; limit null/unlimited,
root/histori/usage dipertahankan. Outcome masih menunggu koreksi dan runner.
Retry pertama mengungkap bug rejection receipt: key `root:rejected` bentrok dengan
pesan penolakan attempt sebelumnya (`IdempotencyConflict`). Rejection key sekarang
mem-pin job/generation, sementara proposal valid tetap retry-stable. Guard tidak
dilonggarkan. Task memberikan id/uac/purpose asli dan input permitted per test,
serta potongan diagnosis yang diverifikasi literal di source (hanya hint).
Worker 3174 → 5907; retry kedua `43eb4a98538b453c975aafe481dfa9a7` running,
limits null/unlimited dan seluruh penggunaan retry pertama tetap dihitung.
Koreksi suite berhasil. Verify baru `6c1e5baff9614f5798997325c27b72e8`
kemudian ditolak saat open_uat: guard evidence.verification masih mensyaratkan
producer verification untuk receipt baseline-cache. Guard support disatukan
dengan manual qualifier: hanya receipt cache exact yang direferensikan laporan
verifier, kind/key cocok; pass juga mensyaratkan satu laporan resmi dengan
invocation/target/suite/counts/commands/runner yang cocok. Cache bukan otoritas.
API/worker di-restart 7612/7633; retry verify melalui HTTP, tanpa perubahan
source/build/scope/approval atau reset usage. Outcome runner menunggu.
Outcome retry verify `3fa586a3ab6545bd86dabf6eb55df2e9`: succeeded,
verification `7bbbfe83d6614453878fc12c440682e6` passed (3 discovered/executed/
passed, 0 failed/skipped), fake_provider=false. UAT pengguna tersimpan oleh
`user:local` pada target `6f26da78da294fb7ab60c270ffdba286`; #2 accepted.
#3 kini development dan QA planning berjalan paralel, keduanya unlimited.
Tidak ada manual QA decision/waiver. Snapshot observer 05:51 WIB menghitung
2 tiket pernah lulus otomatis; qa_needs_human_tickets=1 adalah histori job gagal
#2, bukan blocker aktif. Outcome awal di bagian sebelumnya tetap histori.

## QA #3: selector berulang dan sintaks repair

Verification `0eff2ba7e7c548299892a591b3e5cc02`: 2 executed, 1 passed/1 failed.
Kasus summary memakai borrow-name-input global setelah membuat dua buku; strict
mode mendapati dua elemen. Diagnosis test; app tidak dikembalikan ke Developer.
Repair job `37bc78a7542f459b87eed62ae50ce09d` gagal: proposal memakai role=listitem
dan filter/has-text yang tidak menjadi vocabulary declared; juga mapping null
untuk changed original steps dan coverage witness tanpa coverage gap.
Runtime sebelumnya hanya menyuplai ui_locators tanpa original inputs, sehingga
scoped dynamic locators yang sebenarnya valid tidak terlihat sebagai contoh.
Task sekarang memberi valid_scoped_selector_choices per affected test, berasal
dari whitelist UiContract + input asli, bounded 96/truncation flag. Instruksi
menegaskan mapping changed selector memakai original index serta coverage witness
kosong tanpa gap. Tidak ada perubahan aplikasi, expected, UAC atau allowance DSL.
Syntax AST dan diff diperiksa; unit tests belum dijalankan untuk perubahan ini.
Retry `8b7339eaa8004ab497a11b65784d1b97` masih gagal. Pemeriksaan target
mengungkap kontrak TL #3 flat: book-item dynamic_text=false dan seluruh
scope_testid=null, sehingga valid_scoped_selector_choices memang kosong. Ini
bukan semata QA mengabaikan pilihan valid. Root metadata dari fitur accepted
tidak dipertahankan TL. Instruksi TL menegaskan container dynamic + scope tiap
kontrol berulang dan mempertahankan relasi prerequisite yang diwarisi.
Untuk target pinned saat ini, runtime menyediakan pilihan nth=0 dari locator
declared, hanya bila original fixture dan DOM resmi membuktikan intended first
record. Snapshot menunjukkan Bumi Manusia pertama lalu Laskar Pelangi, cocok
dengan urutan input asli. Exact source hints juga diambil dari literal kontrol
dalam shipped source. Selector allowance tidak diubah dan invariant tetap.
Worker restart 11521 → 13833; retry HTTP authorization
demo-library-t3-qa-pinned-position-retry-20261011-v1. Outcome menunggu.
Retry tersebut tetap memakai selector ilegal. Kontrak output partial revision
kini memakai enum locator exact (original + computed choices) dan purpose wajib;
revise_suite masih memvalidasi source/input/mapping/criteria/limits. Tidak ada QA
pass dari schema/model. Revision-contract revision dipin dalam task/checkpoint.
Retry `48b6fe9e7c8c40f488a9932cbea02e0d` memilih locator legal tetapi memberi
nth pada semua setup controls tanpa witness. Positional choices kemudian dibatasi
hanya locator declared yang muncul lebih dari sekali pada authoritative DOM
candidates. Unique setup selectors tetap asli; failed control siblings dapat
didisambiguasi. Error witness sekarang menyebut indeks/selector yang bermasalah.
Counter DOM resmi memastikan sembilan testids record berulang; tiga aksi pada
kasus ini adalah input Pinjam, tombol Pinjam dan Kembalikan. Hints tetap data
diagnostik, tidak mengubah expected atau UAC. Worker 15108 → 16958, verify retry
authorization demo-library-t3-qa-observed-ambiguity-retry-20261011-v1.
53 tes existing contracts/UI contract lulus (0,66 s). Tidak ada full-suite atau
independent review; hasil browser masih menunggu.
Outcome: retry `a19a3ac5957948fcb7f89db8593596b9` suite_repaired, verify job
`d76708a0b1e3480ca226c0e89c7cc2dc` succeeded. Verification
`9070e2973c9f41a68def69986257e627` passed: 2 discovered/executed/passed,
0 failed/skipped, non-fake. Tiket #3 UAT. Perbandingan suite lama/baru membuktikan
hanya selector original steps 9/10/13 ditambah nth=0; seluruh aksi/value/assertions,
jumlah/urutan steps, test IDs, UAC, purpose tetap. Candidate source SHA tetap
98d16e00d027a0aa1753a75aa3b31e2c1404b5f2. Target baru mem-pin suite baru dan
fresh candidate/browser/baseline evidence; tidak ada waiver atau approval UAT
dari agent. Demo seluruh fitur sudah lulus QA otomatis, acceptance #3 masih user.

## Benchmark final

Snapshot read-only: `2026-10-11T13:52:29.538390+07:00`. DB menunjukkan
#1/#2/#3 **accepted**, tidak ada job queued/running pada proyek demo. Approval
UAT #3 sudah berasal dari pengguna. Semua job demo tetap memakai limit
model/tool/active/output/total token null dan `unlimited_budgets=true`.
Histori penggunaan, kegagalan, dan retry tetap disertakan.

### Developer per tiket

| Tiket | Token lama → baru | Model calls lama → baru | Biaya tercatat USD lama → baru | Active seconds lama → baru | Rollover baru |
| --- | ---: | ---: | ---: | ---: | ---: |
| #1 katalog | 769.631 → 323.577 | 35 → 20 | 0,087454 → 0,005497 | 107,70 → 205,35 | 0 |
| #2 peminjaman/pengembalian | 16.287.114 → 824.425 | 348 → 39 | 1,148858 → 0,032868 | 796,56 → 180,30 | 1 |
| #3 pencarian/ringkasan | 2.228.967 → 383.874 | 69 → 20 | 0,133634 → 0,027118 | 248,55 → 114,04 | 0 |
| Total Developer | 19.285.712 → 1.531.876 | 452 → 79 | 1,369947 → 0,065482 | 1.152,82 → 499,68 | 1 |

Developer memakai **92,06% lebih sedikit token**, 82,52% lebih sedikit model
calls, dan 88,41% lebih sedikit tool calls (1.139 → 132). Waktu aktif agregat
turun **56,66%**: 19,21 → 8,33 menit. Semua job Developer memiliki usage yang
tidak ditandai unknown. Rollover dihitung dari transport artifacts archived
yang checksum-nya cocok; tidak ada artifact rollover baru yang unreadable.
Rollover lama tidak diukur ulang, sehingga tidak diklaim sebagai nol.

Tiket #1 justru lebih lambat meskipun hemat token. Install baru #1 memakan
52,74 detik dan model 106,54 detik. Hasil total terutama membaik karena putaran
Developer #2 berkurang jauh; jangan menyimpulkan semua fitur kini lebih cepat.

### Seluruh proyek, termasuk QA repair dan retry

| Ukuran kumulatif | Demo 7 Oktober | Demo 11 Oktober | Perubahan teramati |
| --- | ---: | ---: | ---: |
| Input + output tokens | 19.538.231 | 2.313.663 | turun 88,16% |
| Model calls | 470 | 128 | turun 72,77% |
| Tool calls | 1.175 | 160 | turun 86,38% |
| Biaya tercatat USD | 1,438371 | 0,268440 | turun 81,34% |
| Active time agregat job | 26,21 menit | 17,07 menit | turun 34,87% |

Total baru mencakup PO, TL, Developer, QA, setup/prefetch dan seluruh retry
yang tercatat. Ada **tiga job non-Developer dengan sebagian usage unknown**
(dua qa_plan dan satu technical_review), walaupun sebagian angkanya tersedia.
Total token/biaya adalah jumlah yang tercatat, bukan jaminan total tagihan
provider lengkap. Persentase biaya tidak mengisolasi perubahan model maupun
cache provider; biaya audit bukan invoice independen.

| Role | Token lama → baru | Biaya tercatat USD lama → baru | Active time lama → baru |
| --- | ---: | ---: | ---: |
| PO | 6.023 → 6.472 | 0,002730 → 0,002869 | 4,95 → 4,88 detik |
| TL | 85.968 → 178.866 | 0,029941 → 0,039271 | 155,81 → 229,00 detik |
| QA | 160.528 → 596.449 | 0,035754 → 0,160818 | 259,01 → 290,69 detik |

QA kini menyumbang sekitar **59,91% biaya tercatat** proyek, dibanding 2,49%
pada demo lama. Token QA sekitar 3,72 kali dan biayanya 4,50 kali angka lama.
Developer sudah jauh lebih hemat, tetapi planning/amendment/diagnosis/revisi
QA masih menimbulkan pengulangan. Angka ini tidak berarti browser harus dihapus.

### Waktu per fase dan tunggu

| Fase baru yang direkam | Durasi | Catatan |
| --- | ---: | --- |
| Model, semua role | 554,49 detik | 127 telemetry spans; usage mencatat 128 calls, tidak semua call memiliki span |
| Tool, semua role | 318,53 detik | 116 spans; termasuk operasi yang juga dicatat dalam fase checks/install/build |
| Install Developer | 90,99 detik | 15 invocations, 14 cache hits; restore/validasi cache tetap membutuhkan waktu |
| Repo test Developer | 28,41 detik | 14 invocations; 4 failed selama pengerjaan, sebelum kandidat akhir |
| Build Developer | 129,97 detik | 11 invocations |
| Checks Developer | 143,86 detik | 9 invocations; inclusive install/test/build |
| Baseline build, semua role | 41,06 detik | 10 spans; hit/miss build cache tidak dapat disimpulkan dari counter nol |
| Browser kandidat QA | 46,52 detik | 6 invocations, 2 failed; mencakup run ulang #2 sebelum open_uat diperbaiki |
| Baseline browser QA | 57,30 detik | 4 invocations; failure baseline untuk fitur baru dapat merupakan bukti yang diharapkan |
| Baseline browser restore | 0,01 detik | 1 cache hit |
| Developer menunggu QA plan | 50,32 detik | 1 span; terpisah dari waktu aktif Developer |
| Provider slot wait | 0,002 detik | Bukan bukti seluruh latensi jaringan/provider hanya sebesar ini |
| Antrean scheduler / dependency wait | Belum tersedia sebagai fase terpisah | Tidak dianggap nol atau diperkirakan dari active_s |

**Durasi fase bersifat inclusive dan saling overlap. Jangan menjumlahkan
baris tabel menjadi elapsed proyek.** Telemetry fase terstruktur pada proyek
lama tidak tersedia; perbandingan install/build/browser lama per fase tidak
dapat dibuat dari CLI yang sama. Peak memory sandbox juga belum diukur.

Tunggu pengguna dari phase events:

| Tiket | Scope approval | UAT |
| --- | ---: | ---: |
| #1 | 96,83 detik | 71,66 detik |
| #2 | 99,95 detik | 10,00 detik |
| #3 | 103,02 detik | 27.729,83 detik (7 jam 42 menit) |

Scope waits berlangsung paralel. QA #3 sudah passed pada **06:05:24 WIB**,
lalu accepted pengguna pada **13:47:34 WIB**. Penantian UAT tersebut bukan
worker lambat dan tidak menambah token Developer.

Elapsed dari proyek dibuat sampai QA terakhir passed: lama **112,84 menit**
(7 Oktober 19:21:45 → 21:14:35 WIB), baru **54,91 menit**
(11 Oktober 05:10:29 → 06:05:24 WIB). Keduanya mencakup jeda approval dependency,
operator, restart, dan perbaikan platform. Ini durasi teramati, bukan waktu
komputasi murni atau bukti kausal optimasi. Pada demo baru, active time agregat
hanya 17,07 menit; jangan mengartikan selisihnya sebagai satu fase tertentu.

### Outcome QA

| Outcome demo baru | Jumlah |
| --- | ---: |
| Tiket lulus QA otomatis dengan runner nyata | 3/3 |
| Tiket lulus langsung pada verification pertama | 1/3 |
| Tiket dengan keputusan/waiver QA manual | 0 |
| Tiket dikembalikan QA sebagai bug aplikasi | 0 |
| Tiket pernah memiliki job QA needs_human | 2 (#2 dan #3), sudah dipulihkan |
| Blocker QA aktif saat snapshot final | 0 |

`qa_needs_human_tickets=2` di JSON observer adalah hitungan **historis**,
bukan dua tiket masih stuck. #2 mengalami ekspektasi tambahan di luar UAC dan
guard cache evidence; #3 mengalami locator berulang serta kontrak TL tanpa
scope parent yang benar. Tidak ada failure ini yang memerlukan perubahan
fungsi aplikasi Developer. Penyelesaian memakai koreksi suite, target baru,
dan eksekusi browser nyata, tanpa mengubah failed evidence lama menjadi pass.

Verification final: #1 `9b207cda775c4cb484001bff422513bc`,
#2 `7bbbfe83d6614453878fc12c440682e6` (3/3),
#3 `9070e2973c9f41a68def69986257e627` (2/2). Detail source/target pin dan
invariant repair tercatat pada histori di atas.

### Kesimpulan dan pekerjaan berikutnya

1. **Pengulangan Developer sudah berkurang nyata pada workload ini.** Hindari
   mengubah banyak bagian sekaligus sebelum satu demo berikutnya membuktikan
   hasil serupa pada versi platform tetap dan konfigurasi provider yang dicatat.
2. **Prioritas berikutnya adalah kontrak dan repair QA.** TL perlu mewarisi
   relasi row/child dari dependency secara deterministik, bukan hanya lewat
   instruksi. Preflight QA harus menemukan aksi ambigu sebelum verify; enum
   pilihan locator legal sebaiknya juga membantu planning awal.
3. **Ekspektasi QA harus mengikuti UAC.** Validator mapping tidak membuktikan
   makna assertion sesuai UAC. Ekspektasi tombol enabled tanpa input adalah
   contoh yang harus dicegah melalui pemeriksaan plan terhadap outcome UAC.
4. **Repair perlu menerima witness yang jelas pada percobaan pertama.** Pilihan
   source excerpt, indeks original step, dan fixture input harus disuplai tepat;
   schema valid belum menjamin mapping/witness diterima guard terakhir.
5. **Install/build tetap memiliki biaya.** Cache hit 14/15 membantu, tetapi
   restore/install 91 detik dan build 130 detik tetap terukur. Pengukuran cold/
   warm identik, queue/dependency time, dan peak memory belum selesai.

Perbandingan ini satu demo dengan warm cache, workload mirip namun #2 menambah
persistence, provider/config yang mungkin berbeda, serta perubahan platform
di tengah run. Demo lama juga memuat overflow dan pemulihan besar. Tidak ada
klaim full-suite atau independent review. Benchmark demo selesai; pekerjaan
platform yang masih membutuhkan bukti cold/warm/memory/review tetap IN_PROGRESS.

Verifikasi benchmark: CLI read-only `app.pipeline.metrics` untuk kedua project,
query status/timestamp/limits DB, dan archived rollover checksums. Tidak ada
benchmark sintetis, provider call baru, approval, retry, atau tes tambahan yang
dijalankan saat pengambilan snapshot final ini.

## Tindak lanjut review QA demo perpustakaan — 11 Oktober 2026

Status IN_PROGRESS / NOT_REVIEWED. Dikerjakan implementer; belum commit/push,
worker belum di-restart, belum ada demo ulang. Bukan independent review.

| Rekomendasi | Perubahan | Bukti |
| --- | --- | --- |
| 3. Model QA | `agents/models.json` lokal (gitignored): role qa memakai `deepseek/deepseek-v4.1-flash` via OpenRouter, fallback `z-ai/glm-5.3-flash`, stream, reasoning low, timeout 120 dtk, sama dengan TL. Berlaku setelah worker restart; kualitas/biaya belum diukur. | `ModelRegistry.from_dict` memuat konfigurasi |
| 4. Satu repair otomatis lalu keputusan user | `suite_repair_allowed`: 1 revisi otomatis per kandidat; tiap revisi tambahan hanya lewat retry run eksplisit user, batas keras 3. Diagnosis `unknown` juga mendapat satu revisi. Gagal → job non-retryable `user_decision_required`; panel menampilkan "Ambil alih" dan "Coba revisi lagi" (retry run QA terakhir yang gagal test_contract). | `test_one_automatic_revision_then_user_authorized_retries_up_to_the_cap`, `test_panel_offers_one_more_revision_only_for_a_failed_test_contract_run` |
| 2a. Aksi parent/child pada baris berulang | Kontrak kebijakan baru (`action_locators`): aksi di dalam kontrol `dynamic_text` wajib `has_text` dari input asli tes itu atau `nth=` terbatas; pasangan parent/child saja ditolak dengan pesan perbaikan. Assertion dan kontrak historis tidak berubah. | `test_action_in_repeated_row_needs_original_input_or_bounded_position` |
| 1. Kutipan UAC per assertion | `Step.uac_id`/`uac_quote` (hanya assert_*, tidak diserialisasi bila kosong → digest suite lama tetap). `QaPlan.check_uac_quotes`: uac_id di test.uac, kutipan substring persis ≥ 8 karakter, setiap UAC otomatis dikutip. Wajib untuk plan QA nyata baru; revisi suite yang sudah berkutipan wajib tetap berkutipan. Fake driver tetap kontrak lama kecuali memakai kutipan. Bonus "hapus assertion lama tanpa kutipan" belum dikerjakan. | 6 test di `test_qa_library_demo_followups.py` (termasuk kasus #2 "tombol Pinjam enabled") |
| 2b. Pewarisan kontrak oleh supervisor | `_inherited_ui_controls`: kontrol `ui_contract` dari target kandidat accepted tiket dependency (scope/dynamic_text utuh). `merge_inherited_contract` setelah jawaban TL: kontrol warisan verbatim + kontrol baru TL; redefinisi TL atas testid warisan diabaikan dan dicatat; merge yang tidak valid kembali ke TL di putaran repair. | 3 test merge/warisan |
| Temuan saat verifikasi | Kosakata selector terbatas revisi (6183c1d) tidak memuat kontrol kontrak yang belum dipakai suite, sehingga revisi coverage gap tidak bisa menambah aksi fitur (`test_coverage_repair` gagal di HEAD). Kini kosakata menyertakan `contract.selectors(input tes terdampak)`. | `tests/pipeline/test_coverage_repair.py` 20 passed |

Keterbatasan: kutipan membuktikan rujukan teks, bukan makna assertion; kutipan
generik masih mungkin. Warisan hanya dari dependency eksplisit yang accepted,
bukan semua tiket accepted proyek. Perlu demo ulang terukur (biaya QA, jumlah
retry, keputusan user) sebelum menilai apakah profil manual sebaiknya default.

Verifikasi: `tests/pipeline tests/domain tests/agents tests/workers tests/persistence`
762 passed (termasuk 2 test coverage repair yang gagal di HEAD 6183c1d);
`tsc --noEmit` web lulus. tests/http tidak dijalankan (venv tanpa httpx2).

## Perbaikan review cd47ce8 — kutipan smoke, batas kontrak, baris berulang

Status IN_PROGRESS / NOT_REVIEWED; belum commit/push/restart/demo ulang.

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| 🔴 Smoke tidak lolos kutipan UAC (profil manual macet) | Test `purpose: smoke` dengan `uac: []` dikecualikan dari kutipan; hanya boleh assert_visible/assert_url/assert_text/assert_contains_text dan tanpa uac_id, sehingga tidak bisa membawa syarat tambahan. | `test_manual_profile_smoke_only_suite_is_valid`, `test_smoke_cannot_carry_requirements` (2) |
| 🟡 Batas 96 kontrol karena warisan menumpuk | Kontrak tiket hanya memuat kontrol warisan yang disebut TL, ditambah parent scope-nya; kontrol warisan lain tetap ada di aplikasi tetapi tidak dikirim ulang. Konteks TL memuat maks 300 kontrol warisan. | `test_only_referenced_inherited_controls_and_their_scopes_are_kept` (152 kontrol warisan) |
| Warisan hanya dependency langsung | Warisan = gabungan kontrak semua kandidat accepted proyek (kecuali tiket sendiri), urut updated_at; penerimaan terakhir menimpa testid yang sama; kontrol dengan scope menggantung dibuang. | `test_inherited_ui_controls_are_the_union_of_accepted_tickets` (jalur accept nyata) |
| Kontrol warisan tidak bisa diubah sama sekali | name/label/text warisan boleh berubah bila kata baru muncul di teks UAC tiket ini; role/scope/dynamic_text tetap dari warisan. | `test_inherited_wording_changes_only_when_this_ticket_uac_requires_it` |
| Celah baris berulang | `check_suite(strict_rows=True)` untuk plan baru dan revisi suite yang sudah memenuhi aturan: setiap locator yang diawali kontainer berulang atau kontrol di dalamnya wajib has_text/text dari input asli atau nth=, termasuk assertion; assert_count dikecualikan. Suite yang sudah dipin pada target aktif tidak diperketat saat submit. | `test_strict_rows_reject_every_ambiguous_row_locator` (3), `test_strict_rows_accept_one_selected_row_and_counts` |
| Kutipan generik | Belum diperketat (tidak mendesak); tetap keterbatasan. | — |

## Perbaikan review fce7c25 — warisan pada amandemen dan kosakata QA

Status IN_PROGRESS / NOT_REVIEWED; belum commit/push/restart/demo ulang.

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| 1. Amandemen tidak menerapkan warisan | Jalur amandemen juga menjalankan `merge_inherited_contract` dengan `pinned` = kontrak tiket saat ini: kontrol lama tetap verbatim (aturan aditif), kontrol warisan yang baru ditambahkan dipulihkan strukturnya (scope/dynamic_text) beserta parent-nya. | `test_amendment_restores_inherited_structure_and_keeps_its_own_controls_verbatim` |
| 2. Setup warisan untuk QA | Kontrak tiket tetap kecil. Kosakata QA = kontrak tiket + kontrol accepted-app (`with_vocabulary`, tanpa batas 96 karena bukan output TL). Kontrol warisan yang dipakai suite (beserta parent) dipin di pesan qa_plan dan target (`ui_vocabulary_controls`), lalu dipakai submit, revisi dan repair. Static UI check tetap hanya kontrak tiket. Task QA menyertakan `accepted_app_controls` maks 300. | `test_qa_vocabulary_includes_pinned_accepted_setup_controls` |
| 3. Kontrol warisan skema lama | `_inherited_ui_controls` memvalidasi tiap kontrol; yang tidak valid dilewati dan dicatat ke log job. | `test_inherited_controls_skip_invalid_legacy_controls` |
| 4. Urutan "acceptance terakhir" | Urutan memakai waktu approval UAT terakhir kandidat (fallback updated_at). | `test_inherited_ui_controls_are_the_union_of_accepted_tickets` |
| 5. Kontrol yang sudah dihapus | Tidak diubah: hanya kontrol yang disebut TL/QA yang masuk kontrak atau dipin; static check/browser menangkap kontrol yang hilang. | — |

Keterbatasan: revisi suite hanya dapat memakai kosakata yang dipin pada target;
kontrol accepted baru yang tidak dipin saat planning memerlukan plan ulang.

## Perbaikan review 5446242 — pin kosakata warisan

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| 🔴 Kontrol warisan yang dipakai via text/role/label tidak dipin (submit gagal) | `UiContract.control_selectors` memecah kosakata per kontrol; `used_accepted_controls` mempin kontrol warisan bila salah satu locator-nya (dengan input test) dipakai suite: testid, `[data-testid=]`, role/name, label, text, atau has_text baris berulang, beserta parent scope. Saat planning, suite dicek ulang terhadap kosakata yang dipin; bila gagal, semua kontrol accepted (maks 300) dipin, sehingga planning dan submit memakai kosakata yang sama. | `test_inherited_controls_used_through_any_locator_form_are_pinned_and_pass_submit` (5 bentuk locator, termasuk `text="Katalog Buku"`) |
| Validasi gabungan kosakata | `with_vocabulary` memeriksa testid unik, scope terdeklarasi dan tanpa siklus. | `test_vocabulary_union_is_checked_for_scope_integrity` (2) |
| Static check hanya kontrak tiket | Tidak diubah; tetap keterbatasan (kontrol setup warisan yang dihapus tiket ini baru ketahuan di browser). | — |
