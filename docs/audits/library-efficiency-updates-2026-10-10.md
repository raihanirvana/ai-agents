# Handoff pembaruan efisiensi — 10 Oktober 2026

Scope: prioritas 1 dan 2 dari [audit demo selesai](./library-completed-efficiency-2026-10-07.md).
Implementasi DONE; review independen NOT_REVIEWED. Tidak ada perubahan data demo,
model, budget, approval, runner image atau proses layanan lokal.

## Perubahan dan bukti acceptance

| Kebutuhan | Implementasi | Bukti |
| --- | --- | --- |
| Kurangi read/edit bolak-balik | SourceTools read_files, handle attempt-local, edit_file_batch satu file | tests/workspace/test_source_tools.py: paging/byte JSON, continuation Unicode, handle/CAS/checkpoint |
| Gagal edit tidak merusak source | Semua match/size divalidasi dulu; temporary file + dir-fd rename | invalid edit kedua, stale handle, disk-full fixture: byte lama utuh, checkpoint tidak ditulis |
| Izin/path/fencing tetap | ToolFacade role policy dan supervisor authorize sebelum read/write | revoked credential, traversal/.git, tests/agents/test_tools.py dan workspace supervisor/fs |
| Context tetap bounded | Batch reads ikut working set; handle/path dari hasil mutasi dipertahankan; argumen batch lama diarsip | tests/pipeline/test_transcript_projection.py, transcript asli tidak diubah |
| Handoff tidak hilang | CandidateSubmission message/handoff/test_concerns; Review.test_concerns | legacy defaults, schema bound/no extra authority, public handoff dan concern pada product loop |
| Concern palsu/obsolete tidak berpengaruh | Exact source excerpt dan candidate/scope/commit/suite pins | test_test_concerns.py: source palsu, path test/.git/traversal, wrong step/selector/identity |
| QA memperbaiki kontrak dengan bukti | Original candidate suite di browser sebelum baseline; existing observed-fill repair | browser nyata membuat target baru sebelum base_build, semua action/input/assertion/UAC lain sama |
| Preflight tidak meloloskan QA/menyembunyikan bug | Target baru perlu full execution; bukti/proposal hanya advice | fake pass tetap incomplete; seeded application bug tetap gagal; mixed failure/abstain lanjut normal |
| Retry tidak mengulang pekerjaan identik | Receipt dan proposal immutable keyed root job dengan exact pins | dua preflight hanya satu browser call; abstain satu model call; perubahan target menolak reuse |

Kode utama: pipeline/source_tools.py, transcript.py, contracts.py,
test_concerns.py, workspace.py, runtime.py; workspace/supervisor.py, fsutil.py,
errors.py; agents/tools.py dan instruksi Developer/TL/QA. Policy dirinci di
[qa-policy.md](../decisions/qa-policy.md) dan [pipeline.md](../decisions/pipeline.md).

## Cara verifikasi dan hasil

Dari apps/backend, dengan venv dan Docker/image Node serta verification yang
sudah tersedia. PATH lokal yang dipakai menyertakan /usr/local/bin (Docker)
dan /Users/23061535/homebrew/bin.

```sh
./.venv/bin/python -m pytest tests/workspace/test_source_tools.py tests/workspace/test_fsutil.py tests/workspace/test_supervisor_logic.py tests/pipeline/test_test_concerns.py tests/pipeline/test_selector_repair.py tests/pipeline/test_contracts.py tests/pipeline/test_transcript_projection.py tests/agents/test_tools.py -q
./.venv/bin/python -m pytest tests/pipeline/test_product_loop.py tests/pipeline/test_product_regressions.py tests/pipeline/test_review_regressions.py tests/pipeline/test_review_context.py tests/pipeline/test_scheduler.py tests/pipeline/test_admission.py tests/pipeline/test_hermes.py -q
```

Hasil: 165 passed (60,37 detik) dan 37 passed (152,25 detik), tanpa skipped.
Browser/container nyata; model/driver test FAKE. Ini bukti kontrak platform,
bukan pengujian provider nyata atau QA pass proyek pengguna. Self-check diff
tidak dianggap independent review.

## Batas dan pengukuran berikutnya

- Batch edit atomik untuk satu file; edit lintas file tetap operasi terpisah.
- Early concern recovery khusus missing fill selector. Other actions/assertions,
  kontrol ambigu, unsupported/mixed failures tetap diagnosis biasa. QA dapat abstain.
- Window source dan jumlah concern dibatasi; source yang tidak cukup tidak
  mendapat koreksi otomatis. Receipt hilang/pins berubah gagal tertutup.
- Tidak ada klaim penghematan token/waktu sebelum demo pembanding dengan provider
  nyata. Ukur model_calls, tool errors, known contract failures, active time dan
  biaya per accepted candidate pada proyek baru sebanding.
- Prioritas cache baseline/dependency, install readiness, telemetry GUI dan
  concurrency belum diimplementasikan pada pembaruan ini. Kandidat peningkatan
  berikutnya adalah cache baseline berdasarkan semua identitas execution.
- Full suite dan lintas OS belum dijalankan. Worker memuat tools/policy baru pada
  proses berikutnya; tidak ada layanan yang direstart oleh pembaruan ini.
