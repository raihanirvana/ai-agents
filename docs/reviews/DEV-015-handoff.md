# Handoff DEV-015 — end-to-end pilot dan operasi lokal

Pelaksana: Codex, 2026-10-05. Baseline `b12d11f` (DEV-014 sudah commit/push). Status implementasi **DONE**;
release approved dan receipt final telah diaudit. Review kode Claude selesai dan fix reviewer telah direcheck Codex; **R8 OPEN** sesuai batas
[review Claude](DEV-015-review-claude.md). Dokumen ini tetap handoff implementer, bukan approval manual pengguna.

## Diff dan file baru

- `apps/backend/app/recovery/`: CLI backup/restore offline dan retry job gagal yang diotorisasi operator lokal.
  Snapshot SQLite WAL konsisten, registered artifacts dan managed bare Git independen; inventory hashes/refs/pins.
  Restore ke root baru memvalidasi DB/Git/bytes, menandai missing data unavailable, menghapus sesi/capability lama,
  membuat parent attempt kosong, mempertahankan waiting request/checkpoint/caps/usage, dan fencing generation.
- `apps/backend/app/workers/queue.py`: `retry_failed` idempotent, hanya user/cleanup selesai/scope-stage eligible,
  parent/root/caps/usage tetap, tidak menjadi budget extension atau tool agent.
- `apps/backend/app/agents/runtime.py`, `agents/po/instructions.md`, `tests/agents/test_runtime.py`: setiap structured
  request/repair menyertakan JSON Schema kontrak yang benar; revisi tidak menggunakan dependency key breakdown.
- `apps/backend/tests/recovery/`, `tests/release/test_recovery.py`, `tests/workers/test_operator_retry.py`:
  cold restore, corruption/path/ref guard, pin cleanup, exact release target/build dan izin/budget retry.
- `examples/dev015/qualification.py`, `summary.py`: finite real-provider pilot pada DB terisolasi dan audit receipt.
  Resume mempertahankan usage, baik pada root awal maupun root hasil restore; tidak mengulang approval yang selesai.
- `agents/qa/instructions.md`: baseline source, exact text, atribut CSS, dan feature/bug/regression yang benar.
  Tiap test memakai fresh context; seluruh urutan dua click/assert harus berada dalam satu testcase.
- README, backlog, `docs/decisions/recovery.md`, `docs/runbooks/local-operations.md`, `docs/pilot-report.md`.
  [Receipt publik final](../spikes/DEV-015-results.json) telah diaudit; DB/key/workspaces/evidence privat tidak masuk diff.
- `agents/developer/instructions.md`: menjaga repo unit tests/assertions/framework; acceptance browser milik runner QA.

Diff lengkap, termasuk file baru, harus direview dari commit DEV-015 atau `git diff HEAD` setelah staging. Tidak ada
deployment, push ke repo sumber fixture, atau implementasi DEV-016/017.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| 01: brief sampai release nyata | Pilot memakai PO/lead OpenRouter, developer/QA Hermes, Docker dan browser runner production. Scope/UAT/release adalah test-user command eksplisit; release approved, 9 browser + 2 Node pass, 13 UAC dan 472 pin diaudit pada receipt final. |
| 02: tiga fitur, independen/dependency, mencoba per tiket | Tiga proposal scope; profile/menu UAT sebelum acceptance profile; transaction menunggu accepted menu; preview HTTP + authoritative browser checks. |
| 03: chat aktif/restart/context/approval/stale attempt | PO chat saat developer running, directed lead reply serta natural pipeline handoff; restore DB/context/target; `tests/workers`, `tests/recovery` memagari lease lama. |
| 04: stale base QA/UAT baru; existing source utuh | Feedback profile, acceptance menu/profile dan invalidasi kandidat lama; candidate/target/base baru; full source fingerprint termasuk `.git`. |
| 05: PO/proposal/handoff nyata | Provider metadata/context hashes/messages/receipts disimpan. Trigger directed message awal berasal dari skrip, jawaban lead nyata; plan/suite/implementation/review pipeline adalah output model nyata. |
| 06: caps/input/quota/same-SHA/empty-skipped-forged/waiver | Workers/domain/preview/pipeline/onboarding automated checks; tests foundation memakai fake/contract fixture berlabel. Flat TAP regresi waiver memakai keluaran Node nyata; pilot baseline green. |
| 07: offline restore/pin/unavailable | WAL DB + objects/refs + artifacts/context + inventory pada root baru; preview digest sama; cold attempt baru; corruption/degraded restore dan cleanup pin tests; drill release nyata memulihkan 585 file/472 pin, tanpa pin unavailable. |
| 08: operasi/provider/cost/support | README, runbook, keputusan recovery, DB reported usage termasuk run gagal; caps finite; total reported USD 0,2141148, 148 model calls/281 tools/982.335 tokens; invoice belum dicocokkan. |
| 09: laporan honest classifications/blockers | `docs/pilot-report.md` memisahkan real/fake/automated/manual; Review Claude/recheck tercatat dengan batas R8 OPEN eksplisit. |

## Menjalankan dan memeriksa

Ikuti [runbook](../runbooks/local-operations.md) untuk pinned Hermes, cheap model example, Docker runner dan env privat.
Skrip menciptakan fixture sendiri dan mengirim paid requests; pilih root Linux baru. Jangan jalankan pada repo pengguna.

```sh
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev015/qualification.py \
  --root /absolute/linux/dev015/pilot-001 --hermes-python "$HERMES_PYTHON"
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev015/summary.py \
  --root /absolute/linux/dev015/pilot-001-restored --output /absolute/linux/dev015/receipt.json
cd apps/backend
.venv/bin/python -m pytest tests -q
```

Reviewer perlu memeriksa receipt terhadap DB/artefak privat, proposal/scope versions, current accepted refs, exact
target/build/runner reports, approvals dan pins. Public IDs/hashes sendiri tidak membuktikan keaslian output model.
Model tidak memperoleh provider secret, DB kontrol atau Docker socket pada target sandbox; executor relay supervisor
memegang akses provider dengan lease yang dipagari.

## Hasil verifikasi dan batas

WSL full **853 passed** (810,94s), sebelum schema fix terakhir; final agents/pipeline **219 passed** (157,02s)
sesudah fix. Recovery/workers/release targeted **87 passed** (23,27s). Windows dengan `--ignore=tests/workspace
--ignore=tests/runtime_spike` **577 passed, 22 skipped** (93,19s) sebelum schema fix; final agents/operator retry
**160 passed** (20,79s). GUI **36 passed** (27,6s), TypeScript/Vite build lulus. Windows cold Git/Docker tests memerlukan
WSL; ini bukan compatibility claim native Windows. Instruksi developer diperjelas kemudian dan dipakai pada pilot nyata.

Transaksi v2 memerlukan satu repair tambahan setelah tiga kandidat mengganti unit tests secara salah, serta dua
explicit token extensions 250.000 → 350.000 → 400.000; usage tidak direset. Model mengembalikan imports/assertions
Node tests awal (perbedaan hanya urutan import dan satu komentar). Ini pilot dengan bantuan operator yang dicatat.

Known issues: menu v1 ambigu memerlukan revisi PO/test-user v2; transaksi v1 memiliki suite dua-click dengan satu click,
konflik preservation paragraph dan berhenti pada cap token. Transaksi v2 memperjelas kontrak secara eksplisit melalui
PO/test-user, dengan histori usage/failure v1 tetap. PO revision awal ditolak karena field dependency keys; schema prompt
dan operator retry memperbaikinya. Initial qualification wiring salah; restore parent runs
awalnya missing, sudah diberi regresi. Histori gagal/biaya dipertahankan. Test-user approval bukan manual UAT. Paid quota
outage belum diinduksi. Scope dukungan stateless React/Vite/Node TAP, offline same-host/WSL restore; tanpa hosting,
target DB/payment, online backup, remapping multi-host, compression/retention UI. R8 dan fix DEV-014 menunggu reviewer
independen; tidak ada klaim self-check menutupnya.
