# Review DEV-010 — Pipeline lead/developer/QA dengan bukti test

Tanggal: 2026-10-05. Reviewer: Claude (Opus 5.5), independen dari implementer Codex.
Baseline HEAD: `fd734a6` (DEV-009). Snapshot yang direview: index staged (41 file, +3.668/−38 baris, termasuk
`docs/spikes/DEV-010-results.json` 769 baris).
Verdict review awal: **NEEDS_FIX**. Pemisahan otoritas (runner vs target, suite di luar mount developer, report hanya
dari stdout runner, publikasi atomik, pemeriksaan ulang di domain) solid. Ada satu bug P1 pada kebijakan budget yang
memotong pipeline atau menolak chat PO, dan satu bug P3 pada status job penolakan. Keduanya diperbaiki reviewer atas
instruksi pengguna dengan regresi. Perbaikan ini self-check reviewer dan **menunggu re-review independen**; review ini
bukan penutupan R6.

Scope yang dibaca penuh: `app/pipeline/*` (contracts, gates, harness, relay, runtime, scheduler, workspace, hermes,
wiring, `__main__`), `contracts/verification/*`, hook di `agents/tools.py`, `http/service.py`,
`persistence/transactions.py`, `workers/supervisor.py`, `worker.py`, `workspace/supervisor.py`,
`runtime_spike/hermes_worker.py`, `examples/dev010/*`, `docs/decisions/pipeline.md`, handoff. Bagian domain/queue yang
dipakai pipeline dibaca ulang (`enqueue`, `reserve`, `finalize_usage`, `extend_budget`, `request_changes`,
`_invalidate`, `_attempt`, `open_uat`, `evidence.verification`, `waiver_matches`). Bukti nyata OpenRouter/Hermes
(`/root/aiagent-dev010/*`) tidak diulang: tidak ada panggilan provider berbayar dalam review ini.

## Yang sudah baik

- **Report authoritative tidak bisa ditulis target.** Target static berjalan `--network none`, read-only, tanpa port
  terbuka. Runner hanya berbagi network namespace target, dan suite/plan dipasang read-only hanya ke runner. Report
  dibaca dari tepat satu frame `PIPELINE_REPORT` di stdout runner; teks halaman target masuk report hanya sebagai
  string JSON yang ter-escape, sehingga tidak bisa membuat frame kedua. `validate_report` mencocokkan nonce invocation,
  target digest, suite digest, ID mandatory persis, pemetaan UAC, dan menghitung ulang counts. Status pass dari model
  tidak pernah dipakai.
- **Pertahanan berlapis di domain.** Walau pipeline salah, `open_uat` → `evidence.verification` tetap menolak
  verifikasi yang bukan `passed`, `fake_provider` bukan `False`, ada infrastructure failure, counts/commands tidak
  konsisten, coverage UAC otomatis hilang, suite digest berbeda dari target, atau dipublikasikan attempt QA lain.
  Label fake terbawa sampai domain: harness nyata + model fake menjadi `incomplete`.
- **Publikasi atomik dan fencing.** Submit kandidat, approve review, dan open-UAT menyelesaikan job dalam transaksi
  domain yang sama, ditambah marker `pipeline_completion` yang dikenali supervisor. Setiap operasi broker memeriksa
  lease DB (`FencedWorkspace`), dan stop/revisi scope sesudah harness ditolak di transaksi publikasi (diuji dengan
  Docker nyata).
- **Akuntansi.** Reservasi model/tool masuk DB sebelum panggilan. Usage dari generation yang sudah di-revoke tetap
  dihitung. Reservasi yang tertinggal saat crash menjadi unknown, bukan nol. Retry dan perpanjangan tidak mereset usage.
- **Build dan baseline.** Kandidat dipak ulang tanpa symlink lalu diverifikasi terhadap `build_digest` sebelum
  di-mount. Build baseline yang berasal dari sandbox di-scan dengan `fsutil.scan_tree`; root berupa symlink atau
  symlink yang keluar dari tree ditolak (sudah saya telusuri: jalur ini fail-closed). Static server menolak symlink di
  semua ancestor. Test ID baseline yang hilang dari kandidat membuat gate `incomplete`.
- **Gate repo.** Node TAP yang tidak flat, kosong, skipped/todo/cancelled, ID duplikat, atau summary tidak konsisten
  menjadi `incomplete`; gate repo tidak pernah menggantikan acceptance browser.
- **Tidak ada secret di file yang di-commit.** `DEV-010-results.json`, contoh model/manifest, dan skrip kualifikasi
  hanya merujuk nama env var; key provider hanya ada di relay supervisor dan child mendapat bearer per-run.

## Temuan dan perbaikan

### R010-01 — P1: budget pipeline berbagi key dengan chat PO, sehingga pipeline mewarisi caps chat atau chat ditolak

Lokasi: `pipeline/scheduler.py` (`caps = jobs[-1].limits`), `workers/queue.py` `enqueue` (budget key
`ticket:<id>:v<n>` dan pemeriksaan "scope budget caps must match").

Chat `revise` dari API (`POST /projects/{id}/messages`) membuat job PO dengan `ticket_id`, sehingga budget key-nya
sama dengan job pipeline untuk scope itu, tetapi dengan caps chat (8 model call, 24 tool, 120 detik, 32k token).
Dampaknya:

1. **Chat lebih dulu** (alur normal DEV-009: PO mengusulkan revisi, pengguna menolak, lalu approve versi yang sama):
   scheduler menyalin `jobs[-1].limits`, yaitu caps chat. Seluruh pipeline (plan, QA plan, developer, review, verify)
   berjalan dengan 8 model call dan 120 detik aktif, lalu berhenti `budget_exhausted`. Kualifikasi nyata memakai
   20–37 call per tiket.
2. **Pipeline lebih dulu:** chat `revise` pengguna selama development ditolak `enqueue` dengan `QueueError` karena caps
   berbeda; command chat gagal.
3. Perpanjangan budget pipeline oleh pengguna juga menaikkan caps chat, karena `extend_budget` memperbarui semua job
   dengan key yang sama.

Reproduksi: `tests/pipeline/test_review_regressions.py` (dua tes pertama), gagal pada tree awal dengan caps
`{model_calls: 8, active_s: 120, ...}` dan `QueueError: scope budget caps must match the existing authorized policy`.

Perbaikan: `enqueue(..., budget_pool=...)`. Pool kosong mempertahankan key lama (`ticket:<id>:v<n>`, chat PO/lead).
Scheduler pipeline memakai pool `pipeline` (`ticket:<id>:v<n>:pipeline`) dan mengambil caps hanya dari job di pool itu.
Pool disimpan di `runtime_ref`, jadi retry dan perpanjangan budget ikut terbawa. Reply lead terhadap pertanyaan developer
mewarisi pool pengirim (`Threads._enqueue_reply`), sehingga tetap memakai budget pipeline. Kedua budget tetap finite
dan terlihat; tidak ada usage yang direset. Nama pool divalidasi; key lama tidak berubah untuk data yang sudah ada.

### R010-02 — P3: penolakan lead/QA membatalkan job yang mempublikasikannya

Lokasi: `pipeline/runtime.py` `_reject`, `domain/service.py` `request_changes` → `_invalidate`.

`request_changes` memanggil `_invalidate`, yang membatalkan semua job aktif tiket, termasuk job lead review atau QA
yang sedang mempublikasikan penolakan itu. Setiap penolakan teknis atau kegagalan browser tercatat sebagai run
`cancelled` (GUI: "Dibatalkan") dengan event `cancellation_requested`. Hasilnya (kandidat, verification) tidak tercatat
di job, dan supervisor mencatat end state `revoked`. Berbeda dengan jalur approve/open-UAT yang menyelesaikan job
secara atomik.

Reproduksi: `test_a_rejection_completes_its_job_in_the_publication_transaction` (POSIX), gagal dengan
`assert 'cancelled' == 'succeeded'`.

Perbaikan: `_invalidate(..., keep_job_id=...)`. `request_changes` dari agent tidak membatalkan attempt-nya sendiri;
job lain tetap dibatalkan seperti sebelumnya, dan request-changes oleh pengguna tidak berubah. `_reject` kini
menyelesaikan job di transaksi yang sama dengan marker `pipeline_completion`. Untuk QA yang gagal, hasil job
membawa `verification_id`/evidence. Supervisor lalu mengenali completion durable seperti jalur approve.

## Observasi (tidak diubah)

- **O1 — Diskriminasi base bergantung pada label `purpose` dari model QA.** Hanya test `feature`/`bug` yang wajib gagal
  pada base. QA bisa melabeli test untuk UAC baru sebagai `regression`, sehingga assertion yang juga lulus di base
  tetap memenuhi coverage. Lead tidak meninjau QA plan. Saran: lead review atas purpose, atau wajib minimal satu test
  diskriminatif per UAC otomatis bila base applicable.
- **O2 — Tiket bisa macet tanpa jalur pemulihan pengguna.** Verify `incomplete` (mis. timeout Docker) atau
  "runner/base changed" menghasilkan job `failed` non-retryable. Scheduler tidak membuat job kedua dengan key yang
  sama, dan tidak ada command pengguna untuk mengulang QA; satu-satunya jalan adalah membatalkan tiket. Kegagalan
  terlihat, tetapi tidak bisa dipulihkan.
- **O3 — Waiver baseline praktis sulit dipakai.** Signature mencakup seluruh stdout TAP (termasuk test yang lulus).
  Kandidat yang menambah satu test baru mengubah signature, sehingga waiver tidak lagi cocok. Konservatif dan
  terdokumentasi, tetapi untuk tiket fitur waiver hampir tidak pernah berlaku.
- **O4 — Default model skrip kualifikasi adalah Qwen**, padahal dokumen menyatakan Qwen belum lolos kualifikasi QA.
  Sebaiknya `--model` wajib pada run baru.
- **O5 — Plan file runner tidak dihapus** (`<site parent>/<invocation>-plan.json`, chmod 0444) dan direktori
  `.verification/<job>/<gen>` tidak dibersihkan. Kecil, tetapi menumpuk di workspace root.
- **O6 — `PipelineScheduler` memakai `Workflow._eligible` dan `ToolFacade._handlers`/`_execute` privat.** Bekerja,
  tetapi mengikat pipeline ke detail internal domain/agent.

## Verifikasi reviewer

| Perintah | Hasil |
| --- | --- |
| WSL + Docker, `python -m pytest tests/pipeline -q` pada tree awal | 54 passed (104,95 dtk) |
| Regresi baru pada tree awal | 3 gagal sesuai temuan (dua R010-01, satu R010-02), 1 lulus |
| WSL + Docker, suite backend lengkap sesudah perbaikan | **722 passed**, 0 skip (318,70 dtk) |
| Windows, `pytest tests --ignore=tests/workspace --ignore=tests/runtime_spike` | 554 passed, 12 skipped |
| WSL, `tests/pipeline/test_review_regressions.py tests/domain` | 107 passed |

Tidak dijalankan: kualifikasi OpenRouter/Hermes nyata (berbayar; bukti implementer di
`docs/spikes/DEV-010-results.json` tidak diverifikasi ulang terhadap DB/artifact privat), build ulang image runner, dan
suite GUI (tidak ada perubahan frontend/kontrak HTTP).

## Pemetaan AC (penilaian reviewer)

| AC | Penilaian |
| --- | --- |
| Tahapan sesuai domain, repair terbatas | Terpenuhi setelah R010-01 (sebelumnya caps chat dapat menghentikan pipeline) |
| Harness terhubung ke DB/job produk | Terpenuhi; admission/usage/generation dari JobQueue |
| Command/exit/env/log/checksum/screenshot/trace | Terpenuhi |
| Suite di luar mount developer, kandidat immutable | Terpenuhi |
| Runner terpisah, identitas dipin | Terpenuhi |
| Zero/skipped/missing/invalid → incomplete | Terpenuhi |
| UAC otomatis ke bukti, manual sebagai checklist | Terpenuhi; lihat O1 untuk kualitas diskriminasi |
| Base pembanding, environment vs bug | Terpenuhi untuk test berlabel feature/bug (O1) |
| Kandidat rusak/stale tidak maju UAT | Terpenuhi |
| Required checks dan waiver fingerprint tepat | Terpenuhi, dengan batas O3 |

## File yang diubah reviewer

- `apps/backend/app/workers/queue.py` — `enqueue(budget_pool=...)`.
- `apps/backend/app/pipeline/scheduler.py` — pool `pipeline`, caps hanya dari pool itu.
- `apps/backend/app/agents/threads.py` — reply mewarisi pool pengirim.
- `apps/backend/app/domain/service.py` — `_invalidate(keep_job_id=...)`, dipakai `request_changes` agent.
- `apps/backend/app/pipeline/runtime.py` — `_reject` menyelesaikan job secara atomik.
- `apps/backend/tests/pipeline/test_review_regressions.py` — 4 regresi (baru).
- `docs/reviews/DEV-010-review.md`, catatan review di `IMPLEMENTATION-BACKLOG.md`.

Tidak ada stage/commit/push oleh reviewer. Status tiket DEV-010 tetap **DONE** (AC terpenuhi sesudah perbaikan), dengan
review **NEEDS_FIX → diperbaiki, menunggu re-review**.

## Recheck perbaikan — Codex, 2026-10-05

R010-01 dan R010-02 diperiksa ulang terhadap working tree, termasuk caller API,
reply Threads, cumulative accounting, extension, invalidation dan durable completion.
Kedua perbaikan diterima; tidak ditemukan blocker tambahan pada diff perbaikan.
Recheck ini memverifikasi perubahan Claude, bukan review independen atas seluruh
implementasi Codex atau penutupan R6 DEV-010/011. Observasi O1–O6 tetap terbuka.

- WSL/Docker: `python -m pytest tests/pipeline tests/domain tests/workers tests/agents -q`:
  **394 passed**, 150,46 detik.
- File review regressions sesudah tambahan test extension: WSL **5 passed**;
  Windows **4 passed, 1 skipped** (POSIX). Test tambahan membuktikan extension pipeline
  tidak menaikkan cap chat, mempertahankan spending sebelumnya, dan berhenti kembali
  pada cap baru.
- Ringkasan `feature-05` dan `bug-01` dicocokkan dengan DB privat: phase, cumulative
  usage, Verification, target/suite digest, counts/coverage dan seluruh artifact
  checksums/ukuran/producer dalam manifest sesuai. Tidak ada request provider baru;
  tidak mengklaim eksperimen lama memakai budget pool yang baru ditambahkan.
- Suite penuh 722/Windows 554 tetap hasil run Claude di atas; tidak diklaim sebagai
  run ulang Codex. `git diff HEAD --check` dan pemeriksaan file commit dijalankan.

Commit/push DEV-010 diotorisasi pengguna setelah recheck ini; status R6 tetap terbuka.
