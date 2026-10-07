# Kekurangan runtime: konteks Developer membesar — 7 Oktober 2026

Status: IMPLEMENTED; recovery demo berhasil ke UAT. Review: NOT_REVIEWED.
Bagian awal adalah kronologi diagnosis; konfirmasi dan implementasi di bagian
akhir menggantikan dugaan awal tentang context-window provider.

## Kejadian dan bukti

Proyek Mini Perpustakaan `b909df3151604b5a8315dead0d366042`, tiket #2
Peminjaman dan pengembalian (`336edfca2a424007b0c994354510bd22`).
Attempt Developer `a2e6131d76784aaf9c201fba766d3d38` gagal pada
7 Oktober 2026 pukul 19.31 WIB: Hermes menyatakan conversation terlalu panjang
untuk `deepseek/deepseek-v4.1-flash`, dengan `compression.enabled` dimatikan.

Usage attempt gagal dari database produk:

| Metrik | Nilai |
| --- | ---: |
| Model calls | 96 |
| Tool calls | 344 |
| Input tokens kumulatif | 5.680.200 |
| Output tokens kumulatif | 39.288 |
| Total tokens kumulatif | 5.719.488 |
| Cached tokens | 4.646.528 |
| Biaya provider tercatat | USD 0,385126368 |
| Active time | 212,31 detik |

Total token kumulatif adalah jumlah lintas panggilan, bukan ukuran context
window satu request. Budget demo unlimited tidak memperbesar context window
model/runtime. Ini kegagalan Developer/Hermes, bukan kegagalan QA.

Retry otomatis `d3becec9a561495c9cf0b785891d5d2e` sudah berjalan saat pengecekan
pukul 19.32 WIB. Log menunjukkan proyeksi konteks tetap aktif:
234.400 karakter asli → 98.149 karakter projected, 47 exchanges dipadatkan,
active_read_chars 15.866. Jadi mekanisme proyeksi yang ada belum cukup mencegah
kegagalan ini. Ukuran konteks tepat pada request yang gagal dan distribusi
pembacaan file sepanjang attempt belum diaudit; akar penyebab detail masih
perlu dibuktikan. Outcome akhir retry belum dicatat dalam audit ini.

## Kekurangan yang perlu diperbaiki

1. Pengendalian konteks masih terlambat/tidak menyeluruh: runtime dapat mencapai
   batas conversation walaupun relay sudah memproyeksikan histori.
2. Ada aktivitas `read_file` berulang; 344 tool calls untuk tiket kecil perlu
   diurai per path/digest agar pembacaan yang tidak menghasilkan progres dikenali.
   Belum ada pengukuran yang membuktikan seluruh calls tersebut redundan.
3. Retry memulihkan pekerjaan, tetapi belum membuktikan kondisi pemicu overflow
   diselesaikan. Mengulang jalur yang sama berpotensi menambah waktu dan biaya.
4. Pesan error masih teknis dan tidak menjelaskan perbedaan budget kumulatif
   dengan context window, serta progres yang sudah berhasil dipulihkan.

## Arah perbaikan berikutnya

- Audit titik pemeriksaan panjang conversation Hermes versus proyeksi relay:
  apakah runtime menolak histori asli sebelum versi projected dikirim.
- Ukur ukuran request yang benar-benar dikirim, overhead tools/system, dan
  headroom untuk output. Tambahkan pengendalian sebelum batas model tercapai.
- Pertahankan scope, feedback, status pekerjaan, file/digest terbaru dan hasil
  checks; singkirkan salinan file/log lama dari konteks aktif. Transcript asli
  tetap disimpan sebagai bukti, dengan fencing dan pin artefak tetap berlaku.
- Ukur pembacaan berulang per path/digest dan arahkan agent memakai hasil
  terbaru yang masih valid; perubahan file harus menginvalidasi referensinya.
- Recovery context overflow harus memakai workspace/checkpoint terverifikasi
  dan konteks aktif yang lebih kecil, tanpa reset usage atau efek tool ganda.
- Tampilkan diagnosis context-window dan status retry yang mudah dipahami.

Jangan langsung mengaktifkan compression umum Hermes tanpa meninjau kontrak
worker yang melarang memory/profile/compression. Catatan ini tidak mengubah
konfigurasi, budget, model, kode runtime, atau approval proyek.

## Bukti keberhasilan yang dibutuhkan

Bandingkan tiket/proyek dengan scope sebanding sebelum dan setelah perubahan:
overflow, calls per file/digest, input per request, token kumulatif, biaya,
durasi, efek recovery dan kelulusan review/browser. Penghematan dan recovery
baru tidak boleh dianggap terbukti hanya karena retry berhasil.


## Update: retry gagal lagi dan antrean berhenti

Retry `d3becec9a561495c9cf0b785891d5d2e` berakhir failed pukul 19.34 WIB,
kemudian `result.needs_human=true`. Scheduler tidak meneruskan attempt gagal
tersebut tanpa recovery; tiket #2 tetap development, tiket #3 menunggu dependency.
Worker masih hidup. Usage retry: 87 model calls, 261 tool calls, total 4.455.723
kumulatif tokens, USD 0,318880008, active_s 173,74. Kedua attempt memakai
10.175.211 token kumulatif dan USD 0,704006376 tercatat.

Indikasi penyebab yang lebih spesifik: `runtime_spike/relay.py` menolak
Content-Length > 1 MiB dengan HTTP 413 sebelum JSON/context audit/projection.
Pada pukul 19.34.24, proyeksi terakhir mencatat histori asli 1.043.050 karakter
menjadi 284.359 karakter, dengan 252 exchanges dipadatkan. Karakter bukan bytes;
tools/schema dan JSON envelope menambah ukuran. Pertambahan setelah request
terakhir dapat melampaui 1 MiB meskipun payload projected masih lebih kecil.
Hermes `agent/turn_recovery.py` memetakan overflow ketika compression dimatikan
ke pesan conversation-too-long yang terlihat pengguna. HTTP 413 relay bisa
muncul dengan pesan itu; error ini belum membuktikan context window model habis.

Diagnosis limit relay ini merupakan inferensi kuat dari jalur kode dan log,
belum pembuktian byte/HTTP response request terakhir. Langkah pertama perbaikan:
ukur request asli dan projected secara terpisah, tangani ingress sebelum
projection dengan batas memori yang tetap bounded, pertahankan batas payload
provider setelah projection, serta bedakan relay rejection dari provider
context overflow dalam status. Jangan sekadar retry tanpa perbaikan penyebab.


## Implementasi dan konfirmasi penyebab

Arsip transport retry berisi `Attempt 1/1 failed: HTTP 413: {"error": "request too large"}`.
Ini mengonfirmasi ingress relay lokal, bukan context-window provider. Analisis
conversation-result mencatat read src/catalog.js dan src/app.js masing-masing
37 kali, tests/helpers/dom.js 32 kali, index.html dan tests/catalog.test.js
masing-masing 29 kali, serta halaman kedua helper DOM 28 kali.

Implementasi: ingress projected relay 8 MiB, provider payload tetap 1 MiB,
observability byte size dan failure_kind relay_context; validated unchanged
read receipts/refresh, penghapusan pasangan receipt redundant pada proyeksi,
working set 64.000 karakter; recovery state dan endpoint retry user di web.
Compression Hermes tidak diaktifkan. Transcript/budget historis tetap.

Recovery nyata melalui HTTP memakai job `20e86aef266649ed9d55949387872432`.
Replay request dengan key yang sama mengembalikan ID yang sama; parent recovery
berubah can_retry=false/needs_action=false karena child sudah ada. Worker
restart untuk penyesuaian working set menghasilkan generation baru, usage
sebelumnya tetap terakumulasi. Pada generation terbaru sudah terlihat operasi
edit, bukan hanya pembacaan; hasil akhir demo dicatat setelah run selesai.

## Hambatan kedua: alasan gate terpotong

Job recovery berhasil submit kandidat `5d7a68fe14da45dea8a5a4b23b6e9512`.
Gate mengeksekusi 30 tes dan semuanya pass, tetapi baseline ID
`node:UAC-4: parseBooks round-trips a persisted catalog` hilang karena nama tes
diubah untuk menyebut loan fields. Gate tetap incomplete, sesuai kontrak.
Feedback lama memakai json gate[:1800]; command dan daftar passing IDs mengisi
batas tersebut sebelum missing_baseline_tests, sehingga Developer melihat
"gagal" tanpa penyebab. Dua repair cepat (8/9 model calls, sekitar 49/48 detik)
submit lagi tanpa memulihkan nama tes. Ini kegagalan feedback platform.

Fix: ringkasan menaruh missing/failed IDs di awal, mempertahankan bukti penuh
lewat gate_artifact_id. Task repair memuat ringkasan dari artefak immutable
kandidat feedback dengan validasi project/ticket/scope, termasuk feedback lama.
Instruksi melarang rename/remove tes baseline untuk memenuhi coverage. Gate,
waiver, QA/UAT/release dan histori feedback tidak diubah.

Tiga siklus lama habis; berdasarkan instruksi pengguna untuk memperbaiki dan
melanjutkan demo, satu siklus tambahan diotorisasi lewat endpoint domain dengan
key `library-actionable-gate-feedback-20261007`. Repair baru
`fcd5af7a188243419cc79bed87160879` menerima missing_baseline_tests dalam snapshot;
tidak mereset usage atau histori. Limit repair menjadi 4, budget tetap unlimited.

## Verifikasi dan batas hasil

- AST 10 file Python dan import PipelineRuntime/RelayContextError/API lulus.
- TypeScript --noEmit dan git diff --check lulus.
- HTTP retry nyata mereplay key yang sama ke satu child; parent tidak lagi
  menunggu tindakan. Authorizations tetap session/Origin/CSRF/revision guarded.
- Perbaikan mempertahankan jalur extend_budget job stopped, terpisah dari retry
  job failed. Local rejected provider payload mencatat nol token/biaya karena
  tidak ada request provider; reservation attempt tetap tercatat.
- Tidak menambah/menjalankan tes regresi, tidak diminta pada assignment ini.
  UI browser, seluruh cabang error, Linux/Windows dan review independen belum
  diverifikasi. Run demo nyata bukan pengganti suite regresi platform.
- Restart worker selesai tertib. API baru melayani port 8000; API lama tidak
  berhenti setelah SIGTERM berulang walau tidak lagi listen dan tidak memiliki
  child proses. PID lama saja dihentikan dengan SIGKILL. Penyebab shutdown API
  yang tertahan belum diaudit; backend/worker/web baru tetap berjalan.
- Total job recovery mencakup generation yang masih membaca berulang sebelum
  working set 64k diterapkan. Jangan memakai total tersebut sebagai angka
  after yang bersih atau mengklaim penghematan universal dari satu recovery.

## Hasil recovery dan handoff review

Repair terakhir `fcd5af7a188243419cc79bed87160879` succeeded: 13 model calls,
37 tool calls, 336.605 total tokens kumulatif, USD 0,022130028, active_s 60,57.
Kandidat `025b931b0ade48ff985d73262a46e7e0`, SHA
`300b481b399b015c2f3d2740beb9185fc6abc0a1`, gate 31/31 passed tanpa missing
baseline IDs. Developer memulihkan nama tes semula dan menambah tes loan terpisah.
Review `8232c99068a446168fadc4ae8b0805f9` passed, satu call, active_s 13,12.
QA `dc73a82ae08a43a39419590d95f253f0` succeeded, qa_status passed, active_s 40,90;
verification `eac04eb039f34672bf32317af4b53e11`, fake_provider=false.
Tiket #2 masuk UAT; #1 accepted, #3 ready menunggu dependency #2 diterima.
Tidak ada approval UAT/release yang dilakukan otomatis.

| Perubahan | File utama | Bukti/hasil |
| --- | --- | --- |
| Relay ingress/projection dan diagnosis | runtime_spike/relay.py; pipeline/relay.py, hermes.py, runtime.py | Arsip HTTP 413 mengonfirmasi akar; retry berhasil tanpa overflow baru |
| Observasi source yang berulang | pipeline/source_tools.py, transcript.py; instruksi developer | Receipt/refresh tervalidasi, working set 64k, run berprogres sampai submit |
| Alasan gate yang actionable | pipeline/review_context.py, runtime.py | Snapshot repair memuat missing ID; kandidat memulihkan baseline, 31/31 pass |
| Recovery di web | workers/queue.py; http/application.py, queries.py; contracts/api; Activity.tsx | Retry HTTP/replay satu child, parent tidak lagi needs_action; TypeScript lulus |

Review diff dengan `git diff` dan file audit baru ini; semua file implementasi
masih belum di-commit pada checkpoint ini. Jalankan API/worker dari apps/backend
dengan `.venv/bin/python -m app` dan `.venv/bin/python -m app.worker --runtime pipeline`,
frontend dari root dengan `npm run dev:web`. Konfigurasi lokal/provider tetap memakai
file gitignored yang sudah tersedia; jangan masukkan key/DB/runtime artefak ke Git.
Kandidat/verification IDs di atas mengacu ke data demo lokal, bukan artefak repo.
Langkah pengguna berikutnya: buka tiket #2 dan lakukan UAT; acceptance baru akan
membuka dependency #3. Known issues/verifikasi yang belum dilakukan ada di bagian
sebelumnya; independent review belum dilakukan.
