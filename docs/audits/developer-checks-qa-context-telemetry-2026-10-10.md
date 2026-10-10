# Efisiensi Developer, konteks QA dan telemetry — 10 Oktober 2026

Status: IN_PROGRESS / NOT_REVIEWED. Worker tetap mati. Tidak menjalankan demo,
provider, migrasi, commit/push atau tes perilaku. Tidak ada angka penghematan
yang diklaim. Scope dan approval produk tetap mengikuti aturan sebelumnya.

## Pemetaan rekomendasi

| Rekomendasi | Implementasi |
| --- | --- |
| Redactor | Sudah diperbaiki pada batch sebelumnya: source memakai exact masking credential yang dikenal. |
| Baseline cache | Sudah tersedia pada batch sebelumnya; key build sekarang juga mengikat runner aktual. Accepted SHA mengikat byte lockfile. Browser cache mengikat suite/target/runner; kandidat diuji baru. |
| `run_checks` | Tool tanpa argumen: install bila perlu, test, build; berhenti pada fase gagal/incomplete. Status, failed IDs dan error excerpt ringkas; report/command evidence tersimpan. |
| Repo map | Index awal berisi path, digest, ukuran dan simbol utama secara heuristik, maksimum 48 file/6k karakter. Map dikurangi jika context tidak muat, tanpa menghapus scope/feedback/suite. |
| Prefix dan sub-sesi | Receipt disegel per delapan exchange. Ambang 40 tool completion atau sekitar 512 KiB payload tool memicu pergantian turn Hermes dengan checkpoint file/checks; tetap satu attempt dan budget kumulatif. |
| Prompt QA/konteks | Instruksi QA dari 16.188 menjadi 5.240 byte. Task QA-plan/Developer tidak mengulang panduan panjang tools/DSL. Developer tidak menerima capabilities yang tidak dipakai. Schema QaPlan hanya di tool, capabilities satu dalam policy. |
| Lane ringan | Job baru TL dan QA payload `diagnose` menggunakan interactive; browser/build tetap execution. Stage QA dan domain binding tetap dipertahankan. Default menyisakan satu slot chat PO. |
| Trace/screenshot | Sudah diperbaiki pada batch sebelumnya: binary diagnostics hanya saat gagal; baseline tidak merekam trace/screenshot. |
| Telemetry | Durasi model/tool/provider-slot, install/test/build/browser/baseline/checks, antrean/input/kuota; scope/UAT wait dari event tiket; CLI read-only per role/job dan usage. |

Audit terkait: [redaksi/scheduler](source-redaction-and-scheduler-2026-10-10.md),
[cache/polling](performance-caches-context-2026-10-10.md).

## Checks dan repo map

File: `pipeline/{checks,repo_map,runtime}.py`, `agents/tools.py` dan instruksi
Developer. Operation lock menahan command/file mutation sepanjang batch.
Receipt instalasi berada di directory supervisor, di luar source target;
identitas mencakup package/lock, manifest/env dan image. Reuse memverifikasi
tree node_modules. Perubahan/deletion/tree yang rusak memerlukan install lagi;
tree yang tidak bisa dicache tidak menghalangi test/build nyata.

Gate test memakai parser yang sama: exit 0 dengan zero/incomplete tests belum
lulus. Report checks bersifat informasi, bukan approval/gate kandidat. Submit
tetap membangun/menguji export source independen dan memeriksa baseline test IDs.
Snapshot instalasi lintas workspace berasal dari fixed offline installer pada
batch sebelumnya, bukan tree Developer setelah build/test.

Missing reference lock meminta Developer menggunakan bootstrap resmi; bukan
keputusan manual user. Command/argv/path/identitas checks tidak diambil dari model.
Full stdout/stderr tetap di command evidence; output tool maksimum 58 KiB JSON,
truncation excerpt/failed IDs ditandai. Report dipin melalui attachment runtime
log yang dikecualikan dari percakapan. HTTP tool run_checks/submit_candidate
memakai 1.020 detik untuk batch tiga command; command timeout, cancellation,
fencing, accounting dan resource limits tetap berlaku per fase.

Map adalah navigasi, bukan file content atau read_handle. JS/TS memakai heuristik,
Python memakai AST tanpa import/eksekusi target. Digest tetap byte source asli;
file/simbol yang tidak terindeks dilaporkan omitted, tanpa klaim parser lengkap.

## Rollover konteks

File: `pipeline/{transcript,hermes,files}.py` dan `runtime_spike/hermes_worker.py`.
Checkpoint deterministik membawa observasi digest file dan checks terakhir.
Scope/user/system/decision/feedback asli serta tool group pending dipertahankan.
Ringkasan tidak memakai model call tambahan, tidak memberi izin overwrite, dan
checks lama tidak membuktikan mutasi berikutnya. Source harus dibaca ulang bila
detail sudah diarsipkan. Sealed prefix mempunyai hash untuk dibandingkan di dalam
blok; prefix dapat berubah pada batas compaction/rollover. Prompt cache provider
tetap harus diukur melalui cached_tokens nyata.

Hermes meminta soft interrupt setelah tool selesai, memfinalisasi transcript,
lalu melanjutkan `run_conversation(..., conversation_history=...)` dengan
proyeksi yang dibatasi. Submission, input wait, admission refusal, provider/runtime
failure dan cleanup error menghentikan rollover. Handler, source handles, lease,
job/generation, reservations dan cumulative budget produk tetap sama. Ambang
rotasi bukan batas total pekerjaan/token; tidak membuat retry atau approval baru.
QA tidak memakai rotasi ini.

API dan finalizer yang menutup interrupted tool sequence/membersihkan interrupt
dibaca dari checkout Hermes lokal yang dipin; checkout itu tidak diubah.
Kompatibilitas nyata, khususnya tool batch paralel, belum dikualifikasi.
Original turn results disimpan dalam conversation-segments.jsonl dan diarsipkan
bersama final conversation/transport sebelum cleanup. Segment diagnostics
dibatasi 64 MiB: bila penuh, histori diagnosis baru berhenti disimpan dan ditandai
truncated, tetapi pekerjaan berlanjut. Current source checkpoints dan bukti
command/build/QA tetap terpisah. Protected messages/pending groups yang sangat
panjang masih bisa mencapai transport bound; rotasi bukan konteks tanpa batas.

## Telemetry

File: `workers/{telemetry,runtime,queue}.py` dan
`pipeline/{relay,workspace,harness,execution_cache,metrics}.py`.
Log phase.metric sekaligus memperbarui aggregate bounded per job. Wait timers
diperbarui dalam transaksi transition, tanpa polling metric baru. Retry child
tidak menyalin aggregate parent sehingga laporan lintas attempt tidak menggandakan
durasi; histori dan budget kumulatif tetap disimpan. Late metric generation asal
adalah observasi, tanpa otoritas mengubah hasil domain. Telemetry best effort
tidak menggantikan evidence atau usage accounting.

Dari apps/backend, setelah ada run yang diizinkan:

```sh
.venv/bin/python -m app.pipeline.metrics --project-id ID_PROYEK
```

CLI tidak membuat/migrasi DB atau menjalankan worker/provider. Pending waits
ditampilkan terpisah dari total yang sudah ditutup. Scope/UAT user wait berasal
dari event fase tiket, bukan job aktif. Durasi nested/inclusive dapat overlap:
tool checks mencakup install/test/build, baseline mencakup build/gate. Jangan
menjumlahkannya menjadi wall time. Browser baseline dipisahkan karena feature
test gagal pada base memang diharapkan. Usage/cost masih per job, belum dialokasikan
ke setiap tool. Job lama tanpa telemetry ditandai unavailable, bukan nol.

## Verifikasi/handoff

Pemeriksaan statis: kompilasi source 23 file Python perubahan lulus,
git diff --check bersih dan inspeksi API/call pada checkout aktual. Tidak menambah/menjalankan tes karena
pengguna meminta perbaikan tanpa meminta tes pada assignment ini. Worker tetap
mati, tidak ada live DB/provider/demo yang dijalankan.

Sisa verifikasi perilaku:

1. Checks cold/warm, package/lock/env/image/tree invalidasi, failure/zero tests,
   unicode/long IDs, snapshot unsupported dan evidence pin; concurrent writes,
   cancellation/lease expiry antar fase serta independent candidate gate.
2. Map truncated/invalid syntax/context trimming dan CAS/read_handle; prefix
   stabil, checkpoint state/feedback, pending/parallel tools, bootstrap/write/check/
   error/input/submission rollover, cumulative usage dan recovery setelah crash.
3. Diagnosis/TL bersamaan dengan heavy job/chat; cleanup menahan lane, tidak ada
   sandbox app yang dieksekusi diagnosis, dan generation lama ditolak.
4. Aggregate/idempotent transitions, queue/quota/input waits, retry lineage,
   unknown usage, late metrics, metric write failure serta UAT reject/reopen.
5. Benchmark scope/model/UAC/workload sama: pisahkan cold/warm, bandingkan calls,
   input/cached tokens/cost, install/build/browser duration dan waktu tunggu.
   Catat correctness bersama waktu, bukan hanya speed.

Status tetap IN_PROGRESS sampai verifikasi perilaku tersedia; belum ada review
independen atau klaim penghematan terukur.
