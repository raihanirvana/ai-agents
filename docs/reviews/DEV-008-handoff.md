# DEV-008 implementation handoff

Status implementasi: DONE. Review: NOT_REVIEWED (R5 tetap terbuka).
Pelaksana: Codex, 2026-10-05. Baseline: `a9918f3` (DEV-007).
Tidak ada commit/push DEV-008. Ini handoff implementer, bukan independent review.

## Hasil dan file

API factory sekarang memakai SQLite/domain/queue produk. Auth lokal persisten,
receipt command atomik, trusted runtime credential terikat lease, query publik,
chat/input/approval/feedback/run commands, SSE replay dan client TypeScript tersedia.
Migrasi 0004 menambah tiga tabel support tanpa mengubah 12 entity inti/trigger.

| File | Tanggung jawab |
| --- | --- |
| `apps/backend/app/http/application.py` | Factory/lifespan, routes, error mapping dan orchestration |
| `security.py` | Host/Origin/CSRF guard, cookie/session dan runtime credential |
| `service.py` | Receipt + efek layanan dalam transaksi yang sama |
| `queries.py`, `events.py` | DTO snapshot/evidence dan replay SSE |
| `schemas.py`, `contract.py` | Strict request schema, export OpenAPI/TS |
| `apps/backend/app/api.py` | Entry point dengan port/origin dari config |
| `apps/backend/app/domain/service.py` | Domain create project, update brief, priority |
| `apps/backend/app/persistence/models.py`, `migrations/versions/0004_api.py` | Session, runtime binding, receipt |
| `apps/backend/tests/http/` | 48 integration/regression tests |
| `tests/persistence/test_migrations.py` | Memperhitungkan support tables pada metadata/head |
| `contracts/api/{openapi.json,requests.ts,types.ts}` | Generated request contract dan public response DTO |
| `apps/web/src/api/client.ts` | Credential/CSRF/command/SSE client, tanpa GUI DEV-009 |
| `playwright.api.config.ts`, `tests/api-browser/` | Real HTTP/Chromium fixture dan header capture |
| `requirements-dev.txt`, README, backlog, `docs/decisions/api.md` | Reproducibility, setup, keputusan dan AC |

Semua berkas baru perlu dibaca saat review; `git diff` saja tidak mencakup untracked
files. Gunakan `git status --short`, `git diff`, lalu baca direktori baru di atas.
Fixture memakai temporary DB/artifacts dan fake provider; tidak menyentuh DB/local
provider key produk. Browser fixture bootstrap code bersifat test-only.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| 1: trusted identity, domain intent, revision/idempotency | `test_commands`, `test_security`, `test_evidence`, `test_restart`, `test_proposals`: competing revision, identical concurrent retry, atomic batch/rollback, DB restart, runtime/user isolation, scope/UAT/release tetap domain |
| 2: board/detail/log/evidence snapshot | Public query projections; `test_evidence`, `test_worker`, `test_proposals`, `test_runs_events`; build/target/evidence IDs, unavailable attachment, scope proposals/UAC dan run status/usage |
| 3: durable replay, expired cursor, final persisted message | `test_runs_events` replay >100 events, project isolation, expired/ahead snapshot reset; `test_worker` fake PO melalui Supervisor; browser live stream/native reconnect/reload |
| 4: reconnect tidak menggandakan efek; structured errors | Concurrent receipt dan fresh API+DB restart; duplicate chat/approval/input; malformed/revision/fencing/500 errors; browser Last-Event-ID header + satu message |
| 5: preview tidak memperoleh kontrol | Test Host/Origin/CORS termasuk authenticated cookie; browser incoming header capture localhost serta actual API OPTIONS 403; sandbox network-none check dalam suite WSL Docker |
| 6: architecture §10 | Persistent hashed sessions, host-only HttpOnly Strict Path=/ cookie, exact 127.0.0.1 Host/Origin/CSRF; runtime Bearer berbeda, generation revoke; real browser logout menutup live stream |
| 7: fenced input dan user waiver | Parametrized request/scope/generation/revision failures, cancel/scope edit tidak resume, duplicate answer/restart receipt; nonblocking escalation; baseline waiver exact fingerprint/user-only command |

## Verifikasi aktual

Environment: Windows Python 3.12.10; Ubuntu WSL Python 3.13.16,
Docker engine 29.8.1; Playwright 1.63.0 Chromium 153.0.8010.12.
Perintah backend dijalankan dari `apps/backend`.

| Check | Hasil |
| --- | --- |
| Windows `.venv/Scripts/python.exe -m pytest tests/http -q --tb=short` | **48 passed** pada tree akhir |
| WSL `/root/aiagent-dev002-venv/bin/python -m pytest tests/http -q --tb=short` | **48 passed** pada tree akhir |
| WSL `python -m pytest -q --tb=short` (seluruh backend, Docker nyata) | **652 passed**, 0 skipped; run sebelum tambahan empat test terakhir (`test_restart`, dua `test_proposals`, artifact read race); keempatnya kemudian lulus dalam suite HTTP akhir |
| Windows `pytest tests/http tests/persistence tests/domain tests/agents -q --tb=short` | **432 passed, 1 skipped**; run dengan 45 test HTTP sebelum tambahan dua test proposal. Skip symlink Windows |
| Windows `pytest tests/http tests/domain tests/persistence tests/agents tests/workers -q --tb=short` | **500 passed, 8 skipped, 1 failed**; run saat HTTP berisi 44 test. Failure lama di `test_a_crashing_runtime_is_retried_once_and_usage_accumulates` DEV-004 (`needs_human` belum tersimpan saat assertion) |
| WSL core suite awal HTTP/domain/persistence/agents/workers | **505 passed, 1 failed**; failure lama `test_dead_worker_recovery_revokes_workspace_keeps_logs_and_preserves_other_run` (log spawn belum terarsip saat crash). Suite WSL penuh berikutnya lulus termasuk test ini |
| `python -m app.http.contract`; drift/schema test | OpenAPI dan TS generated cocok dengan routes; upgrade/downgrade 0003↔0004 mempertahankan entity rows dan SQL semua trigger |
| Root `npm run build` | TypeScript dan Vite build lulus |
| Root `npx playwright test --config playwright.api.config.ts` | **2 passed** real Chromium: host-only cookie, preview headers absent, actual preview OPTIONS 403, live SSE, native reconnect/header, reload, session revoke; actual TS client memulihkan chat saat cursor expired dan menampilkan conflict |
| `git diff --check` | Lulus |

TestClient dengan pinned HTTPX 0.28.1 mengeluarkan satu deprecation warning
Starlette yang menyarankan HTTPX2. Checks masih lulus; dependency tidak diganti
ke library lain tanpa kebutuhan fungsional. Browser evidence tersanitasi berada
di `data/dev008/browser-results.json` (gitignored) dan attachment Playwright;
tidak memuat token/code/key. Report browser menyimpan versi dan execution time.

## Cara menjalankan / batas

Lihat [api.md](../decisions/api.md) dan README untuk bootstrap/login/endpoint.
Test browser Windows menggunakan venv backend pada path default; Linux/macOS
set `API_TEST_PYTHON` ke path interpreter venv. Install Chromium lewat
`npx playwright install chromium`. Server fixture bind 127.0.0.1:19841/19842/19843;
browser preview membuka localhost:19843, temp DB/artifacts dibersihkan setelah exit.

Tidak ada provider call nyata dalam verifikasi DEV-008. Evidence UAT/release
di test adalah synthetic trusted domain fixtures, bukan QA nyata. GUI DEV-009,
workspace/QA wiring DEV-010, preview DEV-011, integration DEV-012 dan release
creation/export DEV-014 belum dikerjakan. Stop API merevoke lebih dulu dan
mendelegasikan physical cleanup ke supervisor; response menyatakan pending.

Known issues: dua flaky DEV-004 di atas sudah dilaporkan sebelum DEV-008 dan
tidak diubah di tiket ini; full suite Windows tidak diklaim hijau. Tidak ada
garbage collector session/receipt pada tiket ini; retention/cleanup bersama DB
menunggu lifecycle berikutnya. Request TS generated, query DTO masih manual dan
perlu ikut direview saat public projection berubah. R5 perlu independent review
DEV-007/008/009; handoff ini tidak menutupnya. Tiket berikutnya: DEV-009.
