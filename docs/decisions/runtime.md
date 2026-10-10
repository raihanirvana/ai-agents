# Keputusan runtime — DEV-006

Tanggal: 4 Oktober 2026 (UTC), 5 Oktober WIB. Status: **ACCEPTED untuk MVP**;
spike **DONE**. Review R3: **REVIEWED**, Claude, dikonfirmasi pengguna 2026-10-05
([catatan](../reviews/DEV-006-R3.md)). Integrasi DB/job produk tetap DEV-010.

Pakai **Hermes embed `AIAgent` dalam subprocess**, dengan relay provider/tools
milik supervisor. Pin `v2026.9.24`, commit
`f97608f178d1ffeca59860195ab7da295f7c8e5f`, package `0.21.5`, Python `3.13.16`
(`>=3.11,<3.14`). `run_conversation`, custom tool registry, streaming callback
dan `interrupt` benar-benar dieksekusi. Hanya satu runtime diimplementasikan.
TUI JSON-RPC yang diusulkan pada persiapan digantikan embed; gateway tidak teruji.
Embed memberi allowlist/flags eksplisit. Default Tool Search harus dimatikan
dengan `tools.tool_search.enabled: off`; daftar tools efektif dicek sebelum inference.

Source pin diperiksa bersama dokumentasi resmi:
[integrasi](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration),
[memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/),
[context](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/).
`hermes-source-inspection.json` merupakan provenance pemeriksaan statis historis.

## Bukti keputusan

Qwen melalui OpenRouter menulis fitur cappuccino/keranjang, commit
`d016e5a4de82144eec0a007e8160ac282f0935a3` di managed project. Target lulus
**12/12 node tests**, build Vite dan **4/4 mandatory browser tests** pada runner
terpisah. Operator mencoba artefak yang sama dan mengonfirmasi semuanya berjalan.
Runner menangkap seeded quantity bug: **2 pass / 2 fail**.
[Laporan AC](../spikes/DEV-006.md) dan [hasil](../spikes/DEV-006-results.json)
memuat identitas target/evidence dan provenance recovery.

Provider **OpenRouter**, model default **`qwen/qwen3.8-27b:free`**: coding/tools
terbukti, shared free pool memberi 429. `cohere/north-mini-code:free` berulang
kali memakai path absolut yang ditolak broker; bukan default MVP. Deskripsi path
diperjelas menjadi relatif proyek; keamanan tidak dilonggarkan. Harga catalog
bukan bukti tagihan: [Qwen resmi](https://openrouter.ai/qwen/qwen3.8-27b:free),
[catalog](https://openrouter.ai/api/v1/models).

Fitur: **37 model requests / 66 tools / 322.39 detik aktif**. Cap awal 32/80,
900 detik, 4,096 output tokens tercapai. Pengguna menambah 16 requests/40 tools;
limit 48/120 dan usage lama tetap. Semua scope/probes: **47 requests / 73 tools**.
41 requests melaporkan USD 0; 6 receipts 429 unknown. Total tagihan **unknown**.

## Kontrak adapter final

Nama berikut operasi aplikasi, bukan API gateway Hermes yang diasumsikan tersedia.

| Operasi | Mapping teruji dan kewajiban DEV-010 |
| --- | --- |
| `start(run_spec)` | Caller memvalidasi scope/project/role/attempt/generation/base immutable. Harness `Experiment.start(scope, hermes_python, env_file, hermes_source)` membaca spec persisten, preflight exact pin, home baru, lalu `Popen` embed. CLI start berjalan dalam proses supervisor terpisah; reference tersedia dari stored run. Duplicate start ditolak sebelum inference. DEV-010 mengganti scoped locator dengan RunSpec/job DB dan operation ID produk. |
| `stream(ref, cursor)` | `Experiment.stream`: normalized durable SQLite events/cursor, termasuk assistant deltas, reservations dan result; tidak bergantung replay native Hermes. |
| `send_input(ref, input)` | `Experiment.send_input`/`resume`: request/answer IDs, scope/base/generation; duplicate tidak membuat generation baru. Setelah reconcile proses lama, fresh process memakai verified checkpoint/jawaban. Tidak menyuntik jawaban ke arbitrary active tool. |
| `inspect(ref)` | `Experiment.inspect`: identity/state, process status, finite limits, cumulative usage/candidate. Completion runtime tidak sama dengan QA/UAT. |
| `stop(ref)` | `Experiment.stop`: revoke journal/broker dahulu; kill owned process group, container/anak berlabel run; join command cleanup; arsip evidence. PID crash dicek exact private config melalui `/proc` sebelum kill. |
| `read_result(ref)` | `Experiment.read_result`: current status/generation, candidate, runtime result dan accounting. Partial/failed/stopped tidak berubah menjadi pass; QA memerlukan evidence runner terpisah yang cocok dengan target. |

Child hanya mendapat credential relay sementara, tanpa provider key. Relay fixed
endpoint mencatat reservation sebelum setiap provider/tool invocation/retry,
men-cap model/output dan menolak endpoint alternatif. File tools relatif proyek;
test/build/commit lewat DEV-005. Target tanpa `.git`, secret, control DB/socket.
Native host tools, delegation, memory, profile, compression, background review,
Tool Search dan dispatcher tidak dipakai. Technical-lead canary hanya tiga read tools.

## Recovery dan batas

Terbukti **checkpoint restart**, bukan native continuation/arbitrary mid-tool
exactly-once. Klarifikasi generation 3: satu jawaban dikirim dua kali, satu resume
generation 4. Setelah 429, checkpoint dipromosikan supervisor untuk QA; Hermes
generation 9 menjalankan gates dan handoff idempotent kandidat sama: satu record.
Crash antara answer/credential rotation/session-file replacement diuji dengan
actual Git/SQLite dan sandbox double; generation yang mungkin sudah start harus
direkonsiliasi, tidak direplay otomatis. Counters tidak reset. Downtime crash
sebelum transition dihitung konservatif sebagai aktif; waiting_input dikecualikan.

Actual inference membuktikan call/tool caps. Cap durasi menghentikan proses.
Stop fixture nyata menghapus parent/child Node container. Canary memakai home,
SOUL/context terpisah, tanpa foreign marker; effective memory/profile/aux flags
diperiksa. Rebuild SHA sama/config/base baru mendapat target/evidence baru;
walkthrough operator tetap pada target pertama. Mismatched evidence ditolak.

Limitasi: POSIX/WSL dan scoped journal saja. Product leases/heartbeat/approval
recovery menunggu DEV-004/010; hard disk quota mengikuti batas DEV-005. Tidak
menjamin free quota/kualitas semua role. GUI, PO nyata, deployment/release tidak
terbukti oleh spike. Key/config/home/workspaces/log besar tetap gitignored.

Untuk testing berikutnya, pengguna mengizinkan model berbayar murah dengan
batas total USD 10 (2026-10-05), bukan per model/tiket. Usage dan biaya wajib
dicatat serta budget request/token tetap finite. Izin ini tidak mengubah biaya
atau hasil eksperimen DEV-006 historis dan tidak menjamin bebas timeout/rate limit.

## Perbaikan review Batch 2 — 6 Oktober 2026

Relay yang dipakai spike dan Hermes produk kini memeriksa hostname endpoint
aktual. `usage: {include: true}` dan `provider: {allow_fallbacks: false}` hanya
dikirim ke `openrouter.ai`; endpoint lain tidak menerima kedua ekstensi itu,
termasuk jika payload Hermes menyertakannya. Jalur chat terstruktur menerapkan
aturan yang sama untuk `usage`. Konfigurasi alias tidak mengubah aturan ini.
Reservation, generation, credential, accounting, dan endpoint milik supervisor
tetap menentukan akses. Perbaikan ini belum membuktikan kompatibilitas Hermes
dengan provider lain; inference dan tes regresi tidak dijalankan pada sesi ini.

Di workspace DEV-005, `SandboxError` saat probe smoke dianggap probe gagal dalam
deadline sehingga laporan `healthy: false` dapat menyertakan log container dan
diagnostik `probe_error`, dengan cleanup di `finally`. Error start/log/cleanup
tetap error infrastruktur; tidak diubah menjadi hasil sehat. Install egress
mencatat penolakan filesystem/lockfile sebagai hasil command gagal. Lockfile
dapat dibaca hingga `max_snapshot_bytes`; batas byte, paket, registry dan
integrity tetap berlaku. Onboarding menerima evidence tersebut lewat alur lama.

Pemeriksaan aktual terbatas pada sintaks AST lima file Python dan whitespace
diff. Modul eksperimen standalone yang hanya di-skim oleh reviewer tidak
diklaim sudah melalui review penuh. Pemetaan file dan handoff regresi tercatat
di log Batch 2 pada `IMPLEMENTATION-BACKLOG.md`.

## Ruang tulis target — 10 Oktober 2026

Source host dipasang read-only di `/source`; `/work` memakai tmpfs dengan batas
`ResourceLimits.work_mb` (default 512 MiB, rentang 16–4096). `/tmp` juga terbatas;
`--memory-swap` disamakan dengan `--memory`. Batas filesystem/memory ini terpisah
dari budget token. Node toolchain menyalin source tanpa mengubah symlink relatif.
Start/smoke juga memakai tmpfs; output build terpin tetap berupa mount read-only.

Command berjalan melalui `docker exec`, sehingga exit code berasal dari Docker
CLI, bukan marker yang ditulis target. PID 1 tetap hidup untuk menjaga tmpfs
sampai container dijeda. Supervisor menyalin `/work` sebagai stream tar dengan
batas byte dan waktu, memeriksa path, entry count, ukuran total/per file, symlink,
serta menolak hardlink/sparse/special files. Hanya tree tervalidasi menggantikan
source host; failed export tidak menerapkan output parsial. Timeout/cancel/OOM
tidak menyalin hasil. CLI logs dibaca sebagai stream dengan buffer terbatas.

Batas ini mencegah target menulis tanpa batas langsung ke disk host. Retention
arsip/artefak/Git/cache lintas banyak attempt tetap merupakan masalah terpisah;
ini bukan kuota total data platform. Copy/export source termasuk node_modules
menambah I/O dan perlu pengukuran sebelum optimasi. Runner Node 22 menyediakan
`fs.cpSync` dengan `verbatimSymlinks` dan filter untuk menghindari mount build.

Dasar implementasi: [Docker tmpfs](https://docs.docker.com/engine/storage/tmpfs/),
[resource constraints](https://docs.docker.com/engine/containers/resource_constraints/),
[pause](https://docs.docker.com/reference/cli/docker/container/pause/),
[cp tar stream](https://docs.docker.com/reference/cli/docker/container/cp/), dan
[source Node 22.20.0 cpSync](https://github.com/nodejs/node/blob/v22.20.0/lib/internal/fs/cp/cp-sync.js).
