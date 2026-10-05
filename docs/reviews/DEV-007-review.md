# Review DEV-007 — SOUL, context, models, threads

Tanggal: 2026-10-05. Reviewer: Codex, independen dari implementer Claude.
Baseline HEAD: `a4ea386031948955c7c0cd82166765fdfe655b90`.
Snapshot index yang direview: Git tree `25bce5a2e406afa43490b641bea0ca84508ab01d`.
Verdict review awal: **NEEDS_FIX**. DEV-007 saat itu dibuka kembali **IN_PROGRESS** karena AC4/5/6 belum terpenuhi.
Status setelah perbaikan Codex: **DONE** untuk fake/contract checks; F1–F7 diperbaiki dengan regresi.
Perbaikan di bawah adalah self-check implementer, **menunggu re-review independen**, bukan verdict REVIEWED.
Ini review DEV-007, bukan penutupan R5 untuk DEV-007/008/009.

Pada review awal tidak mengubah implementasi, suite tracked, atau staging. Probe reproduksi dan output Linux ada
di gitignored `data/dev007/review_probes.py` dan `data/dev007/probes-wsl.log`.

## F1 — P1: verifikasi run tidak atomik dengan penulisan/resume

Lokasi: `apps/backend/app/agents/tools.py:60`, `threads.py:184`, `runtime.py:132`.

`ToolFacade` dan runtime memverifikasi lease lewat transaksi baca yang sudah selesai sebelum
transaksi penulisan. `Threads.answer_request` hanya memeriksa project/role, lalu `queue.answer`
memvalidasi job penanya, bukan lease job yang menjawab. `Threads.send`, `reply`,
`propose_decision`, serta runtime `_post` juga tidak memverifikasi capability di transaksi tulis.

Reproduksi deterministik: developer menunggu lead; lead menjalankan `answer_message`; setelah
facade verify tetapi sebelum handler menulis, cancel job lead. Jawaban tetap tersimpan dan
developer berubah `waiting_input -> queued`. Probe kedua membatalkan lead sesudah `_fence`:
rencana dan decision proposal tetap ditulis. Scheduler riil dapat menghasilkan interleaving
yang sama, karena lock transaksi verifikasi sudah dilepas.

Dampak: attempt yang dicabut masih mengubah state run lain; fence sebelum model/penulisan
belum cukup. Validasi lease/owner/generation/role/scope harus berada di transaksi yang sama
dengan efeknya. Jawaban historis yang terlambat dapat disimpan dengan label, tetapi tidak boleh
menjalankan transisi dari capability responder yang sudah dicabut.

Probe: `test_cancelled_reply_cannot_resume_developer_in_write_race`,
`test_cancel_after_prewrite_fence_cannot_store_lead_plan`.

## F2 — P1: `needs_user` malah menjawab dan me-resume developer

Lokasi: `apps/backend/app/agents/runtime.py:240`.

Untuk jawaban lead `outcome=needs_user`, runtime hanya menambahkan prefix pada body, kemudian
tetap memanggil `threads.answer_request`. Developer otomatis queued/resumed walaupun keputusan
requirement belum diperoleh; tidak ada input request untuk user. Instruksi lead sendiri di
`agents/technical-lead/instructions.md` menyatakan pertanyaan ini menuju user, bukan developer.

Reproduksi: developer bertanya lewat `request_decision`; fake lead mengembalikan
`{kind: answer, outcome: needs_user, answer: Ask the user ...}`. Job developer langsung queued.
Harus tetap menunggu dan meneruskan pertanyaan ke user; hanya jawaban user yang valid membuka resume.

Probe: `test_lead_needs_user_does_not_resume_developer`.

## F3 — P2: kunci efek tool berubah pada retry, menghasilkan duplikasi tiket

Lokasi: `apps/backend/app/agents/tools.py:73`.

Docstring menyatakan key retry-stable, tetapi `_key` memakai `identity['job_id']`. DEV-004 membuat
baris job baru pada retry. Panggilan PO `propose_ticket` dengan argumen persis sama pada attempt
kedua menghasilkan creation key baru dan tiket kedua.

Reproduksi: claim PO, call `propose_ticket`, fail retryable, claim retry, call argumen yang sama.
Dua ticket ID berbeda tersimpan. Gunakan identitas efek/checkpoint yang stabil lintas parent chain;
job/generation tetap diperlukan untuk otorisasi, bukan sebagai identitas efek ulang.

Probe: `test_same_tool_after_retry_does_not_duplicate_ticket`.

## F4 — P2: retry sesudah pesan hasil ditulis selalu konflik idempotency

Lokasi: `apps/backend/app/agents/runtime.py:132`.

`_post` memakai key akar stabil, tetapi payload message memuat job/generation dan attachment
context baru. Retry dengan keluaran model identik tetap berbeda secara semantik menurut
`append_message`, sehingga melempar `IdempotencyConflict`. Decision proposal/reply juga memuat
identitas generation dalam payload untuk key stabil. Tes retry yang ada hanya mencakup crash
di tengah pembuatan tiket, bukan sesudah hasil/pesan tersimpan.

Reproduksi: run breakdown selesai menulis result message; simulasikan crash sebelum
`queue.complete` dengan fail retryable; jalankan retry dengan proposal identik. Tiket tidak
digandakan oleh jalur breakdown, tetapi job tidak bisa selesai karena konflik pesan hasil.
Efek tersimpan perlu dikenali dan dipakai ulang; provenance attempt/snapshot harus dipisahkan
dari payload immutable efek. `_root` yang memotong seluruh key pada `#` juga tidak aman sebagai
identitas chain untuk idempotency key arbitrary; gunakan hubungan parent job yang persisten.

Probe: `test_retry_after_result_message_was_written_recovers`.

## F5 — P2: redaksi belum mencakup tools dan manifest snapshot

Lokasi: `apps/backend/app/agents/tools.py:128`, `context.py:292`.

`ToolFacade`/`Threads` tidak menerapkan redactor pada body/args yang dipersist. `send_message`
menyimpan secret dummy dari fixture dalam plaintext. Di context builder, system/user diredaksi,
tetapi manifest `layers.items` dan gap metadata tidak: repo reference path yang berisi secret
tetap ditulis ke artifact JSON, walau prompt sudah bersih.

Reproduksi pertama memakai `SECRET` dummy yang sudah dikenal redactor fixture, lalu
`tools.call(send_message, body=...)`: body DB tetap memuatnya. Reproduksi kedua memasukkan
secret dummy dalam path repo ref: `snapshot.user` bersih, bytes artifact tidak.
Tidak membaca/mengirim key nyata. Redaksi harus berlaku pada seluruh data keluaran yang
disimpan, termasuk metadata, proposal/tool messages, checkpoint, dan fields hasil.

Probe: `test_tools_redact_secret_before_persisting_message`,
`test_snapshot_redacts_reference_metadata_too`.

## F6 — P2: prompt token yang diketahui tidak dihitung ke budget total

Lokasi: `apps/backend/app/agents/models.py:62`,
`apps/backend/app/workers/queue.py:372` (`finalize_usage`).

`Usage.as_counters` memberi `prompt_tokens`; queue menggabungkan `input_tokens` dan
`output_tokens` bila provider tidak melaporkan total. Provider yang melaporkan prompt=90,
completion=10, total=None menghabiskan setidaknya 100 token, tetapi queue menghitung total=10.
Job dengan cap total 100 masih running dan boleh membuat panggilan berikutnya.

Normalisasi nama counter pada batas adapter/queue. Laporan yang tidak lengkap harus tetap
unknown, sekaligus menegakkan lower bound dari seluruh bagian yang diketahui.

Probe: `test_prompt_token_report_enforces_total_budget`.

## F7 — P2: directed message biasa kehilangan reply job setelah crash

Lokasi: `apps/backend/app/agents/threads.py:96`, `threads.py:164`.

`send(needs_reply=True)` menyimpan pesan lalu enqueue melalui transaksi kedua. Reconciler
`ensure_reply_jobs` hanya membaca `kind=input_request`, bukan message clarification/handoff/bug
yang memakai jalur send. Jika crash terjadi sesudah message commit sebelum enqueue, pertanyaan
tetap tersimpan tanpa reply job dan tidak diperbaiki maintenance.

Reproduksi: `send` developer -> lead, directed clarification, needs_reply; matikan proses secara
simulasi tepat sebelum `_enqueue_reply`, lalu jalankan reconciler. Tidak ada `reply:<message_id>`.
Perlu outbox/intent atau rekonsiliasi semua pertanyaan directed yang belum dijadwalkan, dengan
scope/generation/lane dan idempotency yang benar; note/log/broadcast tetap tidak memicu pekerjaan.

Probe: `test_directed_message_crash_recovers_reply_job`.

## Pemetaan AC dan verifikasi

| AC | Hasil review |
| --- | --- |
| 1 Context berlapis/bounded/hash | Fondasi lulus existing tests; manifest redaction perlu F5. |
| 2 Histori/ringkasan/proposal | Existing tests lulus; histori dipertahankan, keputusan accepted dibedakan. |
| 3 Output/tools sesuai peran | Contract/policy tests lulus; retry efek tool masih F3/F4. Tidak menemukan tool approval/status setter. |
| 4 Directed dev->lead | Jalur normal lulus; eskalasi user dan recovery directed message gagal F2/F7. |
| 5 Input/resume idempotent dan valid | Jalur duplicate/scope/cancel target lulus; responder lease race dan retry pesan gagal F1/F4. |
| 6 Model/config/error/usage/redaction | Kontrak provider lulus; gap budget input token dan redaksi F5/F6. |
| 7 Fake/foundation/persistence | Label fake benar dan key tidak wajib untuk contract tests; klaim nyata tetap DEV-015. |

Perintah aktual dari `apps/backend`:

```sh
# Windows: suite tracked
.venv/Scripts/python.exe -m pytest tests/agents tests/domain tests/persistence tests/workers -q --tb=short
# WSL: suite agents tracked
/root/aiagent-dev002-venv/bin/python -m pytest tests/agents -q --tb=short
# Probe expected behavior, sengaja gagal pada implementasi saat review
python -m pytest -c pytest.ini ../../data/dev007/review_probes.py -q --tb=short
```

- Windows suite tracked: **432 passed, 8 skipped**, 53.32 detik.
- WSL agents tracked: **130 passed**, 16.22 detik.
- Probe review: **9 failed** di Windows dan WSL; semuanya menunjukkan perilaku yang salah,
  bukan kegagalan setup. Real SQLite/artifact dan fake provider berlabel.
- Whitespace index: `git diff --cached --check` bersih.
- Tidak menjalankan ulang suite WSL lengkap/Docker, provider nyata, atau pengukuran flaky DEV-004
  1/14. Angka 583 dan laju flaky itu adalah laporan implementer, bukan verifikasi independen review ini.

Known limitations provider nyata, developer/QA workspace NotWired, estimasi token, dan penulisan
decision ke clone managed sesuai scope/dependency yang didokumentasikan; bukan temuan tambahan.
Review awal hanya mencatat hasil. Perbaikan berikut dilaksanakan atas instruksi pengguna.

## Perbaikan F1–F7 oleh Codex (2026-10-05)

| Temuan | Perbaikan dan regresi permanen (`tests/agents/test_review_regressions.py`) |
| --- | --- |
| F1 | Queue menyediakan fence pada session tulis. Send/reply/decision/hasil runtime/snapshot memeriksa capability di transaksi efek; pemeriksaan responder dan `queue.answer` memakai satu transaksi. Regresi race cancel, setiap handler tulis, dan identitas role tanpa run. |
| F2 | `needs_user` membuat input request pengguna dan event eskalasi. Request developer diganti atomik tanpa resume; jawaban pengguna membuka resume tepat sekali. Regresi user-only answer, restart, cancel, serta eskalasi pesan nonblocking. |
| F3 | Key tool memakai root job dari ancestry DB, tetap stabil pada retry/budget extension. Regresi propose_ticket dan pesan directed lintas retry; key enqueue dengan `#` tidak menggabungkan root. |
| F4 | Output tervalidasi dan snapshot disimpan sebelum efek, lalu direplay dari persistence tanpa panggilan model tambahan. Pesan mempertahankan provenance pertama, sementara payload semantik tetap exact-match. Retry mewarisi jawaban pengguna dan context reference; context tetap dipin selama cleanup. Regresi crash setelah hasil/plan, setelah resume, dan sebelum efek pertama dengan cleanup barrier. |
| F5 | Shared redactor dipakai wiring/Threads/tools; argumen, hasil, pesan, proposal, checkpoint, metadata (termasuk key), manifest dan provider/model hasil diredaksi. Regresi memakai secret fixture berformat key serta secret opaque yang diketahui. |
| F6 | Alias prompt/input dinormalisasi sebelum lower-bound token dihitung. Regresi prompt-only, completion-only, total hilang/tersedia dan unknown yang terlihat. |
| F7 | Reconciler menangani semua pertanyaan directed yang memerlukan reply; intent lane disimpan. Source scope dan cancellation diperiksa di transaksi enqueue, request yang sudah tidak menunggu dilewati. Regresi crash, deduplication, lane long, dan origin yang dicancel. |

`append_message` global tetap membandingkan seluruh payload. Helper `effects.py` hanya mempertahankan
`job_id`/`generation` penulisan pertama saat replay; ia tidak mengabaikan body, recipient, metadata
semantik, ataupun attachment. Snapshot asal dipakai untuk output yang sudah dihasilkan model;
panggilan model baru tetap membangun konteks dari DB. Budget kumulatif tidak direset.

Handoff: perubahan fix terlihat lewat `git diff`; file baru `effects.py`,
`test_review_regressions.py`, dan laporan ini disertakan melalui `git status`. Index implementasi awal
tetap dipertahankan. Tidak stage, commit, push, atau memanggil provider nyata pada pekerjaan fix ini.

Verifikasi setelah fix:

- 25 kasus regresi permanen, total suite agents 155 kasus.
- 9 regresi utama diuji ulang terhadap tree awal `25bce5a2…`: **9 failed**, karena bug aslinya;
  archive terisolasi di `data/dev007/staged-baseline`, tanpa mengubah index/worktree implementasi.
- WSL backend lengkap: **606 passed**, 206.35 detik, termasuk Docker nyata. Run tersebut mendahului
  dua kasus tambahan terakhir dan penyesuaian pin cleanup; tidak diklaim sebagai full run kode final.
- Kode final Windows, agents/domain/persistence/workers: **457 passed, 8 skipped**, 56.17 detik.
- Kode final WSL, agents/persistence/workers: **362 passed**, 44.68 detik. Mencakup semua modul
  yang diubah terakhir (eskalasi, queue/supervisor, pin cleanup), tanpa skip.
- `git diff --check` dan `git diff --cached --check`: bersih.

Perintah final: `.venv/Scripts/python.exe -m pytest tests/agents tests/domain tests/persistence
tests/workers -q --tb=short` pada Windows; `/root/aiagent-dev002-venv/bin/python -m pytest
tests/agents tests/persistence tests/workers -q --tb=short` pada WSL.
Known issue flaky DEV-004 tetap dicatat dari implementer; run WSL lengkap kali ini lulus,
tanpa pengukuran ulang laju flaky. Bukti provider nyata tetap kewajiban DEV-015.

## Verifikasi developer dan tindak lanjut (2026-10-05)

Developer melaporkan F1–F7 valid dan perbaikannya layak, tanpa mengubah implementasi.
25 regresi lulus; terhadap kode index sebelum fix, 23 gagal dan dua kasus pengaman lulus.
Suite WSL agents/domain/persistence/workers: **465 passed**. Windows: **456 passed,
8 skipped, 1 failed** pada `test_a_crashing_runtime_is_retried_once_and_usage_accumulates`
(DEV-004). Pengukuran developer: gagal 2/30 pada HEAD dan 4/30 pada tree sekarang;
dicatat sebagai flaky yang sudah ada, bukan blocker DEV-007. Ini laporan developer,
bukan pengukuran baru Codex atau penutupan review independen/R5.

Dua catatan nonblocking:
- Checkpoint replay menyimpan system/user snapshot lengkap di `jobs.runtime_ref`, sehingga
  menduplikasi artifact dan ikut tersalin ke retry. Optimasi berikutnya dapat menyimpan artifact ID/hash
  dan memuat snapshot dari artifact, sambil mempertahankan pin dan validasi identitasnya.
- Request awal ke lead tampil `orphaned` setelah developer berpindah ke request pengguna.
  DEV-008 perlu menampilkan hubungan eskalasi dari `reply_to`, `source_request_id`, dan event
  `input.escalated`, agar keadaan ini tidak disalahartikan sebagai pertanyaan yang hilang.

Pengguna mengizinkan commit/push DEV-007 setelah verifikasi ini. Status R5 tetap terbuka.
Pemeriksaan Codex sebelum commit: 25 regresi DEV-007 dan test supervisor flaky tersebut
**26 passed**, 2.70 detik, Windows. Satu run yang lulus tidak membuktikan flakiness sudah hilang.
