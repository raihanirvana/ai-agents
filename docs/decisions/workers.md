# Worker, lane, dan recovery — DEV-004

Tanggal: 5 Oktober 2026. Pelaksana: Claude (Opus 5.5). Status implementasi: DONE.
Review independen implementasi awal oleh Codex: **NEEDS_FIX**, 5 Oktober 2026.
Temuan sudah diperbaiki oleh Codex dan diverifikasi dengan regresi; diff fix belum mendapat
re-review independen. Checkpoint R4 tetap terbuka. Laporan: `docs/reviews/DEV-004-review.md`.
Kode: `apps/backend/app/workers/`, `apps/backend/app/adapters/runtime/fake.py`,
`apps/backend/app/worker.py`, migrasi `0003_job_scheduling.py`.

## Komponen

| Komponen | Tanggung jawab |
| --- | --- |
| `JobQueue` (`queue.py`) | Enqueue idempotent, claim atomik, lease/generation, heartbeat, reservasi call, retry, waiting input/quota, cancel, recovery, perpanjangan budget. Satu transaksi `BEGIN IMMEDIATE` per operasi. |
| `Supervisor` (`supervisor.py`) | Dua lane; tiap job berjalan di thread sendiri, thread supervisor hanya claim/heartbeat/rekonsiliasi. Stop runtime saat lease dicabut, arsip log, shutdown tertib. |
| `RunContext` (`runtime.py`) | Satu-satunya jalur runtime ke state: reservasi model/tool, input request, registrasi process group dan resource lain (workspace run), label kepemilikan proses. |
| `ProviderLimiter` (`limiter.py`) | Kapasitas request bersama dua lane dengan slot interaktif yang dicadangkan; sinyal quota provider memblokir semua job sampai waktu retry. |
| `FakeRuntime` | Runtime/provider **berlabel fake** untuk test dan dry run. Hermes nyata di-wire pada DEV-010. |

## Aturan yang ditegakkan

Review Claude Batch 1 (6 Oktober 2026): extension budget menghasilkan satu anak
retry per parent. Replay authorization yang sama mengembalikan ID anak itu;
authorization baru pada parent yang sudah memiliki retry ditolak sebelum cap
ditambah. Chat tiket berikutnya mengambil policy terbaru dari pool interaktif
scope yang sama, tanpa mengambil policy pool execution atau versi scope lain.
Loop worker melaporkan tipe exception tick dan menunggu poll sebelum mencoba lagi;
shutdown berada dalam finally, termasuk ketika loop terinterupsi. Pesan exception
mentah tidak dicetak karena dapat memuat data request/credential.
Kegagalan snapshot/bind/start sebelum thread runtime hidup mengembalikan claim
ke antrean dengan backoff dan menutup intent cleanup kosong attempt itu; tidak
menunggu lease expiry untuk claim yang tidak memiliki executor.

Recovery tetap memeriksa owner/resource setiap lease interval agar resource yang
kemudian berhenti bisa direkonsiliasi. Error `reconcile_failed` yang sama pada
generation cleanup yang sama hanya menghasilkan satu event `job.needs_human`;
error baru atau generation berbeda dapat menghasilkan event baru. Ledger dan pin
cleanup tetap menahan slot sampai penghentian resource terbukti. Edit scope
mempertahankan creation_key tiket sehingga retry breakdown tidak menduplikasi
tiket yang sudah diedit, tanpa mempertahankan approval/attempt scope lama.

`pipeline_workspace_root` di config dipakai worker, pipeline, integrator dan
preview. Path relatif `PIPELINE_WORKSPACE_ROOT` di-resolve terhadap root repository,
default `<DATA_DIR>/pipeline-workspaces`; path absolut tetap dipakai. Perubahan ini
tidak memindahkan workspace lama secara otomatis: instalasi yang sudah memakai
workspace relatif-CWD perlu mengatur path absolut ke lokasi itu atau memindahkannya
secara eksplisit ketika seluruh runtime berhenti.

- **Lease adalah capability.** `Lease(job, owner, generation)` wajib cocok dengan job berstatus
  `running` dan lease yang belum kedaluwarsa untuk complete/fail/reserve/input/register/release.
  Setiap claim menaikkan generation; cancel, release, wait quota, budget habis dan recovery juga
  menaikkannya. Attempt lama tidak bisa mengubah apa pun (`StaleLease`); hasilnya dibuang dan
  dicatat di log (`stale_result_rejected`). Usage dari generation lama tetap dicatat sebagai
  akuntansi karena token benar-benar terpakai. Akuntansi lama boleh menghentikan run aktif pada
  scope yang sama jika budget terlampaui, tetapi tidak boleh menerima hasil domain attempt lama.
- **Satu slot execution** dihitung di database dalam transaksi claim yang terserialisasi, bukan di
  memori proses; konfigurasi execution selain satu ditolak. Run dengan cleanup yang belum selesai
  juga menahan slot walaupun lease-nya sudah dilepas. Worker lain tidak bisa menjalankan job execution kedua. Lane interaktif punya
  kapasitas sendiri dan diproses walaupun execution sedang berjalan lama.
- **Eligibility dari domain.** Job tiket dengan stage `development`/`technical_review`/`qa` hanya
  di-claim bila `Workflow.startable` (helper baca-saja baru di DEV-003, sama dengan aturan
  `bind_attempt`) mengizinkan, lalu attempt diikat lewat `Workflow.bind_attempt`. Penolakan domain
  mengembalikan job ke antrean dengan backoff. Job yang scope-nya berubah dibatalkan saat claim.
  Worker hanya mengambil job untuk runtime yang ia miliki.
- **Pembatalan revoke-dulu.** Siapa pun yang membatalkan (pengguna, `_invalidate` domain, recovery)
  menaikkan generation dan menghapus lease di DB. Heartbeat supervisor melihat pencabutan, lalu
  menghentikan runtime: process group (SIGTERM, lalu SIGKILL) dan resource terdaftar, misalnya
  `WorkspaceSupervisor.stop_run` yang mencabut credential workspace, mematikan container, dan
  mengarsip. Log run selalu disimpan sebagai artifact `log` (meta producer/job/generation/end_state/
  fake) dan dirujuk dari `jobs.result.evidence_artifact_ids`. Baris log disimpan sebagai pesan
  append-only selama run, sehingga crash worker tidak menghilangkan log sebelumnya. Artifact log
  dipin oleh job termasuk sesudah terminal; pembuatan artifact dan pemasangan pin satu transaksi.
- **Recovery.** Job `running` dengan lease kedaluwarsa: (1) di-fence (generation naik, status
  `failed`, `lease_expired`); (2) process group yang dicatat dihentikan **hanya bila** semua anggota
  group yang masih hidup membawa label `AIAGENTS_RUN=<job>:<generation>` milik attempt itu (dibaca
  dari `/proc` pada Linux, psutil pada macOS); proses lain (PID dipakai ulang, preview) tidak disentuh; kepemilikan campuran tidak
  pernah dibunuh; (3) rekonsiliasi workspace dengan supervisor ID, job, generation, dan lease yang
  cocok; (4) arsip log lalu retry. Intent pembuatan process group/workspace dicatat sebelum launch,
  sehingga crash sebelum registrasi resource tetap bisa ditangani. Proses worker zombie dianggap
  mati; worker yang masih hidup menahan retry agar thread lama tidak bisa membuat resource baru.
  Ledger cleanup dan token rekonsiliasi persisten mempertahankan pekerjaan jika crash terjadi
  setelah fencing. Cleanup failed/waiting/cancelled dapat dilanjutkan tanpa replay pekerjaan input.
  Bila kepemilikan tidak bisa diverifikasi
  (platform tidak didukung atau akses inspeksi ditolak), job ditandai `needs_human`. Job `waiting_input` tidak punya lease dan tidak
  pernah dijalankan ulang tanpa jawaban.
- **Retry terbatas.** Kegagalan transient (runtime crash, lease kedaluwarsa) membuat **attempt baru**
  (baris job baru, `parent_job_id`, `attempt+1`), default satu retry (`max_attempts=2`). Setelah itu,
  atau untuk kegagalan non-retryable, job `failed` dengan `needs_human=true` dan event
  `job.needs_human`.
- **Status eksekusi berbeda.** `waiting_input` (request + checkpoint disimpan sebagai pesan
  `input_request`, slot dilepas, runtime dihentikan; jawaban pertama mengantre ulang, duplikat
  no-op, scope berubah membatalkan), `waiting_quota` (alasan + `retry_at`, dipromosikan otomatis),
  `stopped` (budget habis, `needs_human`), `failed`, `cancelled`, `succeeded`.
- **Budget kumulatif.** `jobs.limits` wajib memuat `model_calls`, `tool_calls`, `active_s` finite
  positif (`output_tokens` per call dan `total_tokens` opsional). Batas berlaku pada usage kumulatif
  per `budget_key` (tiket + versi scope, atau job pertama untuk pekerjaan tanpa tiket), jadi retry
  tidak mendapat budget baru. Call direservasi **sebelum** dijalankan; call provider yang gagal tetap
  terhitung dan usage-nya `unknown`. Waktu aktif dibebankan lewat heartbeat; menunggu tidak dihitung.
  Budget habis -> `stopped`; perpanjangan hanya lewat keputusan pengguna eksplisit
  (`extend_budget`, idempotent per job/authorization ID dan payload keputusan) yang membuat attempt baru dengan cap lebih tinggi
  dan usage lama tetap. Budget key ditetapkan queue dan diisolasi per project; enqueue tidak bisa
  mengganti policy scope. Total token memakai laporan total atau jumlah input/output; laporan
  parsial menyimpan lower bound yang diketahui sekaligus penanda unknown. Usage terlambat tetap
  menegakkan cap pada run berikutnya. Interval terakhir sebelum waiting/crash/cancel ikut dihitung.
- **Fake berlabel.** `runtime_ref.fake=true`, setiap event `job.*` membawa `fake`, hasil job fake
  mendapat `fake_provider=true`, log artifact ber-meta `fake`, dan CLI mencetak peringatan. Receipt QA
  dengan `fake_provider` ditolak domain DEV-003, jadi fake tidak pernah menghasilkan QA pass nyata.

## Menjalankan

```sh
cd apps/backend
./.venv/bin/python -m app.persistence upgrade
./.venv/bin/python -m app.worker                 # runtime none: hanya rekonsiliasi lease/quota
./.venv/bin/python -m app.worker --runtime fake  # dry run berlabel FAKE
./.venv/bin/python -m pytest tests/workers -q
```

Konfigurasi: `WORKER_RUNTIME` (`none`/`fake`), `WORKER_LEASE_S` (default 30; heartbeat lease/3),
`--worker-id`. Worker menolak start (exit 2) bila database belum di revisi terbaru.

## Batas dan known issues

- Hanya fake runtime. Adapter Hermes nyata (DEV-006 embed + relay) dan pipeline lead/developer/QA
  dengan bukti test di-wire pada DEV-010 memakai `RunContext`/`ctx.actor()` ini.
- Supervisi process group mendukung Linux (`killpg`, `/proc`) dan macOS (`killpg`, psutil,
  `/bin/ps` untuk discovery PID/UID); di Windows jalankan worker di WSL. Logika
  antrean/lane/budget juga diuji di Windows native.
- Perbaikan macOS 2026-10-06: 86 worker tests lulus, termasuk process/recovery nyata.
  Identitas owner memakai create time untuk mendeteksi PID reuse. Record macOS lama
  tanpa start identity hanya boleh pulih setelah owner hilang/zombie. Akses ownership
  yang ditolak tetap memblokir cleanup. Linux/WSL belum diuji ulang setelah perubahan ini.
- Deteksi pembatalan mengikuti interval heartbeat (CLI default 10 detik), bukan push. Stop resource
  dan recovery berjalan di thread terpisah agar tidak memblokir heartbeat/lane interaktif.
- Limiter provider dan status quota bersifat per proses supervisor (MVP satu supervisor); job yang
  terdampak tetap tercatat persisten sebagai `waiting_quota` dengan `retry_at`.
- `total_tokens` hanya bisa ditegakkan bila provider melaporkan token; jika tidak, usage `unknown`
  dan cap request/waktu tetap berlaku.
- API/GUI untuk menjawab input, membatalkan, atau memperpanjang budget adalah DEV-008.
- Cleanup/arsip gagal atau worker lama masih hidup: slot tetap tertahan, `needs_human` dan event
  menunjukkan kegagalannya. Recovery berikutnya hanya melepasnya setelah ownership/cleanup terbukti.
- Callback cleanup tanpa descriptor yang dikenali dicatat sebagai resource opaque; sesudah crash
  tidak dianggap bersih. Adapter DEV-010 wajib mencatat intent sebelum membuat resource dan
  menyediakan rekonsiliasi descriptor-nya. Resource generation yang sudah selesai dibuang saat resume.
- Log job terminal tetap dipin; kebijakan retensi/purge log memerlukan keputusan terpisah.
- Dependency DEV-003 berstatus DONE, tetapi diff perbaikan review-nya belum di-re-review independen.

## Verifikasi review/fix

- WSL suite backend lengkap: **453 passed**, 189.92 detik, Docker nyata, tanpa skip.
- Windows domain/persistence/workers: **302 passed, 8 skipped** (symlink dan tujuh POSIX).
- Worker setelah pemeriksaan akhir cleanup/recovery: **77 passed** di WSL; **70 passed, 7 skipped** di Windows.
- **31 regresi perilaku** gagal pada snapshot staged awal dan lulus setelah fix; dua tes POSIX baru
  menguji SIGKILL worker, workspace/credential/log/ownership, serta crash sebelum registrasi PGID.
- CLI DB baru: migrasi/check 0003 sehat; runtime none tidak claim, fake menyelesaikan job dan cleanup;
  kedua mode berhenti dengan SIGTERM exit 0. Provider nyata tidak dipanggil.


## Pemulihan run gagal lewat web — 7 Oktober 2026

Public run projection menyertakan recovery.can_retry/needs_action/reason/
retry_job_id. Readiness berasal dari aturan queue/domain dan dihitung ulang di
transaksi mutasi. Histori yang sudah punya child retry tidak lagi ditampilkan
sebagai run yang menunggu tindakan. Budget, scope, dependency, stage, target/
candidate terkini, cleanup dan repair limit tetap membatasi eligibility.

POST /runs/{run_id}/retry memakai session user, Origin/CSRF policy, revision dan
Idempotency-Key. Runtime tidak dapat mengotorisasi retry pengguna. Mutasi
memanggil JobQueue.retry_failed yang menyimpan authorization user/key, menciptakan
satu child job, memulihkan checkpoint dan tetap mengakumulasi usage. Key yang
sama mereplay receipt; request berbeda untuk parent yang sudah punya child
ditolak. Retry tidak memperbesar budget atau menghapus approval/generation lama.
Web menampilkan Perlu tindakan dan alasan, serta Coba lagi untuk run eligible;
worker tetap berjalan terpisah dari sesi browser pengguna.

## Polling pipeline tanpa write lock idle — 10 Oktober 2026

PipelineScheduler melakukan scan dalam transaksi baca WAL. Hanya tiket dengan
proposal dispatch membuka transaksi tulis; revision, approval/dependency,
candidate/target, accepted tip, active jobs/cleanup, idempotency key dan cap
budget diperiksa ulang sebelum enqueue dalam transaksi yang sama. Perubahan
saat jeda snapshot ditunda ke tick berikutnya. Tidak meng-upgrade transaksi
baca menjadi write, tidak memotong usage atau menaikkan kapasitas execution.
Detail/batas verifikasi: [audit](../audits/source-redaction-and-scheduler-2026-10-10.md).
