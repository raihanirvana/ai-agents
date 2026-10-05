# DEV-010 — handoff review pipeline/QA

Pelaksana: Codex, 2026-10-05. Implementasi: **DONE**. Review independen R6: **NOT_REVIEWED**.
Handoff ini self-check implementer, tidak menutup R6 atau review DEV-011.

## Hasil dan diff

Tiket approved pada proyek yang dikonfigurasi menghasilkan technical plan, suite QA
persisten, kandidat broker/clean build, review lead, dan evidence browser sebelum UAT.
Tidak ada status setter atau approval tool untuk model. Penolakan memicu feedback dan
repair terbatas. Semua admission/input/budget/generation berasal dari DB produk.

Periksa `git diff HEAD` dan `git status --short` untuk seluruh implementasi staged
serta catatan final di working tree (belum commit):

- `app/pipeline/`: scheduler, kontrak QA/review, relay/accounting DB, Hermes pinned,
  workspace fencing/checkpoint/build/gates, authoritative Docker harness, runtime,
  wiring dan CLI konfigurasi proyek baru.
- `contracts/verification/`: DSL Playwright runner, static server readonly, Dockerfile.
  `.gitignore` mengecualikan folder kontrak ini dari pola runtime `verification/`.
- `tests/pipeline/`: contract/reservation/scheduler serta DB/Git/Docker regressions.
- `examples/dev010/`: model/manifest contoh dan kualifikasi opt-in terisolasi.
- `persistence/transactions.py`: adapter transaksi caller diekstrak dari HTTP service
  untuk domain/queue publication atomik, tanpa nested DB-lock transaction.
- Hook kompatibel: worker pipeline, ToolFacade memakai handler workspace yang wired,
  QA request_input, run ID sebelum workspace creation, completion marker di supervisor,
  Hermes tool schema/required-completion dengan satu correction turn terbatas.
- Test DEV-004 crash-retry menunggu `needs_human` setelah cleanup, bukan status failed
  sementara sebelum retry-finalization. Ini memperbaiki race assertion yang pernah
  tercatat flaky; bukan menghapus assertion atau menambah retry test.

## Pemetaan acceptance criteria

| AC DEV-010 | Bukti |
| --- | --- |
| Stage dan bounded repair sesuai domain | `test_scheduler.py`, review rejection/browser broken pada `test_product_loop.py`; tiga penolakan berhenti needs_human, usage terakumulasi |
| DB produk sebagai authority, input/usage/generation | `test_admission.py`; checkpoint→lead reply→generation baru, recovery dan credential-after-revoke regressions; seeded bug nyata melewati restart dan budget extension |
| Command/exit/env/log/checksum/screenshot/trace | `ProductWorkspace.command_reports`, immutable target, actual Docker harness; artifact IDs/checksums pada results manifest |
| Suite di luar mount developer, build immutable, repo-test review | `test_harness.py` memeriksa mount; actual browser tests, immutable bundle digest; missing-baseline-test regression; lead melihat diff termasuk repo tests |
| Runner terpisah, target/suite/nonce/mandatory IDs pinned | `test_contracts.py`, transport forgery/multiple frames tests; target network-none dan runner-only plan; real target/suite/invocation IDs di manifest |
| Zero/skipped/missing/invalid/coverage missing ditolak | `test_contracts.py`, `test_gates.py`, `test_harness.py`; timeout/unhealthy report menjadi incomplete, bukan pass |
| Automated UAC coverage dan manual checklist | `QaPlan.check_criteria` + test unknown/missing/manual UAC; real feature dua UAC dan seeded bug satu UAC; manual confirmation tetap domain/API/GUI DEV-003/008/009 |
| Base comparison, regression green, infrastructure terpisah | Real feature/bug: kandidat passed, base failed; `test_baseline_green_is_allowed_only_for_regression_not_a_new_feature` dua kasus actual Docker; timeout evidence incomplete |
| Broken candidate/stale/scope tidak maju UAT | Actual green-unit/broken-browser loop; stop/scope-revision sesudah harness run; DB-revoked credential; atomic-publication crash regression |
| Required checks dan exact user waiver | Empty/skipped/inconsistent Node TAP tests; mandatory-baseline IDs; waiver regression exact match dengan gate `waived`, changed same-count/test-ID/infrastructure/incomplete ditolak |

Transport/report validation memakai Docker stub dan fake provider secara eksplisit;
itu contract proof, bukan eksekusi browser/provider nyata. Product-loop/recovery
tests memakai DB, Git, Docker, dan browser nyata dengan **model FAKE**; pass fake
downgrade menjadi incomplete sehingga tidak dapat membuka UAT. Bukti provider nyata
adalah dua eksperimen terpisah berikut, bukan hasil fake test tersebut.

## Eksperimen provider/Hermes nyata

Lihat [DEV-010-results.json](../spikes/DEV-010-results.json). Summary ini menyalin
identitas, counts, coverage, suite, baseline dan checksums dari DB/artifact asli;
report authoritative tetap di private artifact store, bukan JSON summary yang diedit.

| Run | Hasil | Usage scope |
| --- | --- | --- |
| feature-05 | Toggle diskon 8.00→7.20→8.00; satu repair lead; 2 mandatory browser tests passed kandidat/failed base; phase UAT, fake=false | 24 calls, 43 tools, 150.247 tokens, $0,0322168 |
| bug-01 | Seed +100 cents/item diperbaiki; 1 browser test passed kandidat/failed base; repo tests 2 passed; phase UAT, fake=false | 37 calls, 68 tools, 194.530 tokens, $0,068536 |

Bug run memakai 32 calls `openai/gpt-4.1-mini`, restart dengan checkpoint/input DB,
lalu extension eksplisit maksimal 16 calls di budget yang sama; 5 tambahan memakai
`openai/gpt-4.1`. Seluruh penggunaan sebelumnya tetap dihitung. Semua enam percobaan
termasuk empat percobaan awal gagal memakai **$0,12668808**, unknown kosong pada
ringkasan run. Tidak ada auto paid-model fallback. Scope approval fixture oleh
test-user; tidak ada user UAT acceptance, release, export, push atau deployment.

Hermes pin: package 0.21.5, commit `f97608f178d1ffeca59860195ab7da295f7c8e5f`,
Python 3.13.16. Target mininum React 18.3.1/Vite 6.4.3/Node 22.20.0.
Real-run image acceptance DEV-006 yang kompatibel dipin dengan exact image ID di
target; Dockerfile runner DEV-010 kemudian dibangun dan contract/browser suite
dijalankan ulang dengan image baru. Evidence lama tidak ditransfer ke target/image baru.

Private evidence tersedia lokal `/root/aiagent-dev010/feature-05` dan
`/root/aiagent-dev010/bug-01` (DB, artifacts, managed Git, private Hermes transcripts).
Log penuh suite lokal `/tmp/dev010-backend-full.log`; kualifikasi serta model config
tidak mengandung key yang di-commit. Reviewer harus memeriksa artifact checksum dan
DB scope/candidate/target bila menilai bukti actual, bukan hanya percaya prose summary.

## Cara menjalankan dan hasil verifikasi

Setup Docker, Hermes, manifest, proyek baru dan worker ada di
[pipeline.md](../decisions/pipeline.md). Model nyata hanya opt-in melalui
`examples/dev010/qualification.py`, root baru untuk feature/bug; invocation memakai
product DB/queue/domain, bukan journal spike. Setup fixture bukan DEV-013 onboarding.

Verifikasi yang sudah selesai:

- WSL final: `cd apps/backend; python -m pytest -q` dengan venv Linux dan Docker aktif:
  **718 passed**, tanpa skip, 330,60 detik; termasuk seluruh **54** pipeline cases.
- WSL standalone sebelum tambahan Stop: `python -m pytest tests/pipeline -q`:
  **53 passed**, 101,27 detik, dengan runner image hasil Dockerfile publik.
- Windows: `apps/backend/.venv/Scripts/python.exe -m pytest apps/backend/tests -q`
  dengan `--ignore` untuk workspace, runtime_spike, dan dua file product Docker/POSIX:
  **551 passed, 9 skipped**, 70,19 detik. POSIX/process-group/symlink limitations.
- Windows pipeline standalone: **38 passed, 3 skipped**, 2,55 detik.
- `docker build -t aiagent-verification:1.63.0 contracts/verification`: berhasil.
- `git diff --check`, compileall, `node --check` static server, serta delivery JSON/
  evidence-summary/secret-pattern checks lulus.
- Container pipeline tersisa: **0**. Sisa target dari run test cleanup awal yang
  gagal dibersihkan setelah owner label dan mount run `pytest-80` diverifikasi;
  run final tidak meninggalkan container pipeline.
- Satu warning Starlette/httpx upstream tetap ada. Frontend GUI suite tidak dijalankan
  ulang karena tidak ada perubahan frontend/kontrak HTTP; seluruh HTTP tests backend
  termasuk suite lengkap di atas. Review ini tidak mengklaim pilot PO DEV-015.

## Batas dan area review yang penting

- Static React/Vite + flat Node TAP saja; test framework lain incomplete. Fixture
  stateless/migrations none. Custom start/port/health manifest belum menjadi lifecycle
  preview; trusted acceptance memakai server internal 4173 dan health `/`.
- Tidak ada monetary USD cap otomatis; finite model/tool/token/active-time caps dan
  reported/unknown cost tetap enforced/visible. Izin testing USD10 adalah izin sesi,
  bukan setting default produk.
- Waiver signature konservatif terhadap perubahan output selain timing; hanya user
  fingerprint baseline, tidak menutup browser UAC/infrastructure.
- Qwen di percobaan awal gagal menyelesaikan QA. Kualifikasi final Qwen belum diulang;
  qualified real path memakai dua model OpenRouter di atas. PO nyata tetap DEV-015.
- CLI setup hanya proyek mode new, tidak mengeksekusi instruksi repo asli. Preview,
  accepted integration, existing repository, release masing-masing tiket berikutnya.
- Review khusus R6: authority/report separation, atomic publication dan crash windows,
  cleanup proof/ownership, missing mandatory tests, fingerprint waiver, immutable
  artifact pin, role tool authorization dan late-spend accounting. R6 belum tertutup.

Self-check akhir menambah pengaman cleanup: inspect error yang bukan explicit
not-found tidak dianggap container hilang, termasuk bila Docker info berhasil.
Format error Docker uppercase/lowercase didukung. Regression ini dan 53 test pipeline
lulus (101,27 detik). Stop pipeline kini langsung memanggil stopper resource,
dan file product regressions (9 cases, 44,11 detik) menguji cancellation saat harness
berjalan tanpa menunggu timeout 150 detik. Suite lengkap final 718 passed mencakup
regresi ini setelah kondisi test diperketat menunggu container runner yang aktif.

Implementasi sudah staged; catatan final backlog/handoff masih di working tree.
Belum di-commit atau di-push. Tiket berikutnya yang dependency-nya terpenuhi:
DEV-011; tidak dimulai dalam assignment ini.
