# Review DEV-020 (D–E) — 12 Oktober 2026

Reviewer: Codex. Baseline: `a8d08b48e4f45ec85859dff37ec5b5c972c85d11`.
Target: working tree D–E termasuk tiga file baru role/test. Pengguna meminta review,
langsung memperbaiki bug, lalu commit dan push. Tidak mengimplementasikan DEV-021.
Verdict: siap lanjut; DEV-020 DONE dan REVIEWED setelah fix serta recheck.
Fix oleh reviewer yang sama, tanpa klaim independent review kedua atas fix.

## Temuan terkonfirmasi

| ID | Severity | Lokasi tree akhir | Pemicu dan dampak | Fix / verifikasi |
| --- | --- | --- | --- | --- |
| D-01 | P2 | `apps/backend/app/agents/threads.py:100`, `:128`, `:174` | Penambahan UI/UX ke ROLES otomatis mengizinkan `send_message(needs_reply=True)` dan `request_decision(to_role="ui-ux")`. Threads membuat job reply/answer_message, tetapi StructuredAgentRuntime belum mendukung pasangan role/task itu. Job gagal; request_decision sudah melepas slot dan dapat meninggalkan developer waiting_input tanpa balasan. Rekonsiliasi juga membuat job yang sama dari pesan pending. | `UNWIRED_REPLY_ROLES` mencegah ketiga jalur sebelum menyimpan pertanyaan/membuat job. Test send/request_decision memeriksa tidak ada pesan percakapan baru, tidak ada job reply, tidak ada waiting_request_id, dan sender tetap running. Test rekonsiliasi mem-pin pesan legacy dalam DB immutable dan memeriksa tidak ada enqueue. Ketiga kasus gagal sebelum fix (3 failed, 20 passed), lalu lulus. Audit log dan budget penggunaan tool tetap tersimpan. |
| D-02 | P3 | `apps/backend/app/agents/tools.py:58` | Panggilan langsung helper read_design_reference dengan None, string, atau list menghasilkan TypeError dari set/indexing, alih-alih Invalid sesuai kontrak argumen. ToolFacade sudah memvalidasi object, sehingga ini robustness helper, bukan bypass izin endpoint. | Pemeriksaan dict dilakukan lebih dahulu; test argumen non-object ditambah dan lulus. |

Perbaikan pendukung: pesan NotWired propose_ui_spec merujuk DEV-021; instruksi role
menyatakan copy berupa daftar objek `{key, text}`, ketiga key teks kontrol harus
statis dan maksimal 160 karakter, serta scope berada pada layar yang sama. Ini
menyelaraskan prompt dengan validator UiSpec hasil review A–C.

## Pemetaan AC

| AC / scope | Bukti |
| --- | --- |
| Role terdaftar | ROLES souls/runspec konsisten; load_agents membaca SOUL/instructions UI/UX dengan digest; models.example override dan NEEDS_APPROVED_SCOPE memuat ui-ux. Test context mencakup scope approved/unapproved. Models lokal diabaikan Git; override UI/UX diperiksa sama dengan TL tanpa mencetak konfigurasi sensitif. |
| Tidak bisa menulis source/commit/approval | TOOL_POLICY hanya read_criteria, read_repo, read_design_reference, propose_ui_spec, request_input. Matrix test tool lintas role kini juga menjalankan ui-ux. Test credential workspace nyata dengan stub sandbox mengizinkan read/list dan menolak write, run_command, run_phase build, submit_candidate serta checkpoint; accepted ref tetap sama. Tidak ada tool approval dan jalur domain approval tetap user-only. |
| read_design_reference hanya di folder referensi; traversal/symlink ditolak | scan_tree + read_file_beneath memakai validasi path dan open tanpa mengikuti symlink. Test traversal, path absolut, extension non-md/txt, alias symlink dan link keluar lulus; escaping link membuat tree unavailable. Test batas 65536 byte diterima dan 65537 ditolak. |
| Referensi dan ringkasan dipin | Instruksi merujuk SKILL.md yang sudah dipin, ringkasan <3 KiB; isi pinned dibaca lewat helper dengan hash sesuai SOURCE.md. Tidak ada fetch/update runtime. |
| Tidak perlu migration | Tidak ditemukan CHECK constraint role/stage pada models/migrations. Persistensi dan schema existing diuji pada regresi background. |
| Batas tahap D–E | propose_ui_spec tetap NotWired; scheduler lama tetap technical_plan dahulu. Guard reply mempertahankan batas tahap ini. Stage/pewarisan/amandemen UI/UX tetap DEV-021, konsumsi UiSpec DEV-022, label/type web DEV-025. |

## Verifikasi aktual

Perintah dari `apps/backend`, dengan prefiks toolchain host:

```sh
env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest <selection> -q --tb=short
```

| Selection / tahap | Hasil |
| --- | --- |
| `tests/agents/test_ui_ux_role.py tests/agents/test_souls_outputs.py tests/agents/test_tools.py`, tree implementer | 59 passed |
| `tests/agents/test_ui_ux_role.py`, regression test reply ditambah sebelum fix | 3 failed, 20 passed; kegagalan mereproduksi D-01 |
| `tests/agents tests/domain tests/workers tests/workspace/test_supervisor_logic.py`, sesudah fix | 483 passed, 2 failed, 2 warnings; kedua failure hanya assertion baru yang menghitung audit log tool sebagai pesan pertanyaan |
| `tests/agents/test_ui_ux_role.py tests/agents/test_tools.py tests/agents/test_souls_outputs.py`, assertion audit diperbaiki dan UI/UX ditambah ke matrix lintas role | 70 passed, termasuk dua assertion yang gagal di run sebelumnya dan satu kasus matrix role baru |
| `tests/pipeline/test_scheduler.py tests/pipeline/test_admission.py tests/pipeline/test_contracts.py`, sesudah fix | 55 passed |
| `tests/pipeline tests/persistence`, background implementer yang mulai sebelum fix | 381 passed, 8 warnings, exit 0; log lokal di `/private/tmp/claude-1334639650/-Users-23061535-ai-agent/77bfb983-42e2-441d-9180-7c7c522afdb0/tasks/b3vwqzlml.output` |

Selection agents/domain/workers/supervisor mencakup **486 test setelah recheck**
(483 sebelumnya lulus, dua failure assertion sudah lulus ulang, satu matrix role baru).
Tidak ada failure tersisa pada selection tersebut. Dua warning run gabungan berasal
dari refleksi expression index SQLite oleh SQLAlchemy/Alembic.

`git diff --check` lulus. File baru turut diperiksa, di luar tracked diff.
Test memakai fake provider dan stub sandbox berlabel; ini bukan bukti model UI/UX
nyata atau QA provider. Tidak menjalankan panggilan model nyata atau mengubah DB/runtime
proyek. Suite backend penuh tidak dijalankan. Regresi pipeline/persistence background
membuktikan tree awal D–E; tiga modul pipeline juga diperiksa ulang sesudah fix.

## Diff dan handoff

Patch terhadap baseline, termasuk file baru dan catatan ini:
`/private/tmp/DEV-020-review.patch` (sementara, di luar repository).
Sesudah commit, keseluruhan diff dapat dibaca lewat `git show --stat` / `git show`.

File baru: `agents/ui-ux/SOUL.md`, `agents/ui-ux/instructions.md`,
`apps/backend/tests/agents/test_ui_ux_role.py`, dan catatan review ini. File tracked
yang berubah meliputi registry/context/thread/tool agent, runspec/izin supervisor,
models example, tests souls/tools/supervisor, arsitektur dan backlog.

Known limits: UI/UX belum menghasilkan UiSpec melalui runtime. Tidak ada auto job
UI/UX dari scheduler; generic reply diblokir sampai implementasi routing yang sesuai.
DEV-021 harus mengganti guard tersebut ketika reply/amandemen dapat dieksekusi.
Label role web/office dan kontrak tipe belum ditambahkan (DEV-025); periksa kebutuhan
kompatibilitas ketika job UI/UX pertama diaktifkan. Models lokal tidak masuk commit.
