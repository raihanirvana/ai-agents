# Handoff DEV-013 — Onboarding existing repo

Tanggal: 2026-10-05. Baseline HEAD `6b7c8addcd388beb69d58f1d6aa73370528fb061`.
Review awal independen oleh Claude (Sonnet 5.5): **NEEDS_FIX**, dengan lima perbaikan dan regresi.
Recheck diff perbaikan oleh Codex: **REVIEWED untuk DEV-013**, dengan 804 backend WSL/Docker,
557 backend Windows (20 skip), 32 GUI, build dan diff check lulus. Detail ada di
[DEV-013-review.md](./DEV-013-review.md). R8 mencakup
DEV-013/014/015 dan belum ditutup; DEV-014 tidak dimulai. Commit/push diotorisasi pengguna sesudah recheck.

## Perubahan

Onboarding existing kini tersedia melalui GUI/API/CLI dan job DB produk. Source Git dibaca tanpa
reset/stash/copy dirty files; import Git independent membangun accepted baseline dengan refs/config/
hooks sendiri. Patch merupakan pilihan pengguna yang dipin source SHA/digest. Baseline menjalankan
toolchain probes dan install/build/test/start di sandbox, mempublish report/log pins, serta memberi
blocker yang konkret untuk runner unsupported/incomplete. Lead/context menerima baseline dan repo
guidance berlabel untrusted. Waiver repo gates diperiksa per failure, bukan count atau signature
seluruh suite baru. Integrator/QA/UAT tetap memakai jalur produk sebelumnya.

Files baru: `app/onboarding/{requests,source,runtime,__main__,__init__}.py`, `tests/onboarding/`,
`tests/http/test_onboarding.py`, GUI `Onboarding.tsx`, browser `onboarding.spec.ts`,
`examples/dev013/{qualification,summary}.py`, keputusan onboarding dan summary evidence.
Files diubah: supervisor baseline builder; worker registration; context/technical-plan;
gates/baseline fingerprints/admission; HTTP schema/route/DTO; contracts; Projects/Workspace UI;
README/backlog dan keputusan API/GUI/pipeline.

## Pemetaan acceptance criteria

| AC DEV-013 (urutan backlog) | Implementasi/bukti |
| --- | --- |
| 1. Independent refs/config/hooks/objects; attempt di managed clone | Git bundle → empty-template bare repo; source test menguji malicious hooks/fsmonitor/clean filter + `.gitattributes`, refs/config/index/source inventory unchanged; objects tetap dapat dibaca setelah sumber dipindah; tidak ada alternates. |
| 2. Dirty changes dilaporkan; patch eksplisit | Porcelain status dan GUI report; patch artifact pengguna dengan SHA/digest, baseline commit sendiri; tests staged/unstaged/untracked, patch selected, stale SHA. Tidak ada reset/stash/stage sumber. |
| 3. Baseline install/build/test/start; failures terpisah; lead plan | Docker test reference React/Vite melakukan semua phases + version probes; report/log artifacts dan immutable start smoke; `ready_with_baseline_failures` tidak memberi waiver; lead technical-plan menerima baseline/plan. |
| 4. Checks pass/default; exact user waiver; failure baru/UAC/infra ditolak | Per-test fingerprints dan all-failure admission; test waiver old failures + new green test, changed diagnostics, replacement failure dengan count sama, environment mismatch/infra; tests domain/pipeline menjaga user-only serta UAC mandatory. GUI failure/waiver tetap eksplisit. |
| 5. Manifest/instructions/policy; unsupported blocker | Manifest parser, static stack/migration/toolchain validation, secret/path/symlink/submodule rejection sebelum checkout; context guidance berlabel untrusted dengan SHA/digest; unsupported manifest API/source tests. |
| 6. Feature existing sampai QA/UAT/Accepted tanpa mengubah source | Kualifikasi receipt dengan source dari DEV-010, Hermes/model nyata, separate browser QA candidate passed/base failed, test-user UAT dan production integrator Accepted; full source file inventory termasuk `.git` unchanged. Approval fixture terpisah dari manual user UAT. |

## Verifikasi

Verifikasi implementasi awal memakai WSL Ubuntu, `/root/aiagent-dev002-venv/bin/python`, Docker nyata.
Angka di bawah adalah histori sebelum perbaikan reviewer; hasil final sesudah perbaikan ada pada
[review dan recheck](./DEV-013-review.md).

- `python -m pytest tests -q`: **794 passed, 1 failed, 0 skipped**, 488.28s. Failure adalah test pipeline
  stop runner: dua jalur cleanup berbarengan dan inspect sempat melihat container yang sedang dihapus
  (`owned container remains after cleanup`). Harness/stop code tidak diubah DEV-013. Rerun test tersebut
  terpisah: **1 passed**, 4.37s. Full suite tidak diklaim seluruhnya hijau.
- Final `python -m pytest tests/onboarding tests/http/test_onboarding.py tests/agents/test_context.py -q`:
  **38 passed**, 113.95s; termasuk latest tool cap, filter inspection/source safety, Docker baseline,
  exact waiver, transaction replay dan restart cleanup ownership.
- Source safety final recheck `tests/onboarding/test_source.py`: **10 passed**, termasuk symlink ditolak sebelum
  patch checkout, hostile clean filter/attributes tidak berjalan, serta local line-ending policy dihormati
  tanpa refresh index. Context/source recheck sebelum penambahan test line-ending: **27 passed**.
- `npm run build`: passed. `npx playwright test --config playwright.web.config.ts`: **32 passed**, 18.4s.
- `git diff --check`: passed. OpenAPI/requests regenerated dan HTTP contract checks passed.

Checks awal terkait: 220 passed/1 failed saat limit onboarding masih 16 tools; publication crash replay
memakai 18 calls dan ditolak cap. Limit finite dinaikkan 32 untuk satu retry; targeted regression passed.
Percobaan provider nyata: **Accepted**, 14 calls, $0,0176504 reported; source unchanged, no fake.

Run source tests, runtime tests dan GUI seperti pada `docs/decisions/onboarding.md`. Diff termasuk
file baru awal disimpan pada `data/dev013/review.patch` (gitignored). Review final harus memakai
files aktual serta `git diff HEAD`, termasuk fixtures TAP dan laporan review yang ditambahkan sesudahnya.

## Known issues dan batas bukti

- Source lokal working tree Git; URL remote, bare source, source non-Git perlu snapshot eksplisit.
- Static React/Vite, npm public lockfile, flat Node TAP dan migrations none; backend DB/services,
  monorepo runner, private registry dan stack lain belum didukung.
- Partial Git staging membutuhkan inspeksi operator. Failed baseline dapat diminta ulang dengan
  manifest diperbaiki; bila source/patch berubah dan DB belum punya accepted tip, import dengan receipt
  diarsipkan sebelum import baru. Repo tanpa receipt atau ref yang berubah tidak diganti.
- Race concurrent Docker runner cleanup pada verifikasi awal telah diperbaiki pada harness dan
  PreviewService; penghapusan ditunggu sampai terbukti selesai, dengan batas waktu dan cek kepemilikan.
- Secret detector file names/patterns konservatif; bukan scanner universal. Source history tetap
  di supervisor; snapshot target tidak mengandung `.git`/credentials yang terdeteksi.
- Browser/mock GUI checks membuktikan rendering/payload; bukan bukti provider. Kualifikasi nyata
  memisahkan QA nyata dari scope/UAT test-user. Tidak mengklaim UAT manual atau DEV-015 selesai.
- Kualifikasi provider dijalankan sebelum penambahan version probes terpisah dan hardening terakhir
  source inspection; perubahan akhir diverifikasi lewat tests Docker/Git. Model/provider tidak
  diulang untuk perubahan tersebut yang tidak mengubah adapter/fitur.
- Review DEV-013 dan recheck fix terpisah dari penutupan R8. Observasi O1–O8 tetap tercatat pada
  laporan review; batas checkpoint DEV-014/015 belum diverifikasi.
