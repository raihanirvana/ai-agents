# Review DEV-015 (reviewer: Claude) — 2026-10-05

Baseline: `b12d11f` + perubahan DEV-015 di working tree (belum commit). File ini terpisah dari self-check implementer
([DEV-015-review](DEV-015-review.md)). Hasil: **satu bug nyata ditemukan dan diperbaiki (R015-A)**; sisanya observasi.
Review ini **tidak menutup R8**: pilot berbayar tidak dijalankan ulang dan UAT manual pengguna belum ada.

## Yang sudah baik

- Alur runbook (backup → restore ke root baru → `persistence check` → `retry-job`) dijalankan ulang end to end dan sesuai.
- Snapshot memakai SQLite backup API di bawah `BEGIN IMMEDIATE`, bare Git independen tanpa hooks/config/alternates, dan
  restore memvalidasi checksum/refs/integritas DB, menghapus sesi/credential lama dan menaikkan generation job.
- `retry_failed` idempotent, mempertahankan parent/cap/budget key/usage, menolak budget habis, scope/phase usang, child
  retry yang sudah ada dan user agent.
- Schema JSON di `_ask` ikut pada request dan repair; konteks terburuk 3067 → 4268 token (batas 8000). Checkpoint key
  tetap dari task asli.
- Artefak ekspor release (patch/bundle) ter-pin dan bertahan dari `cleanup_unpinned` (probe sementara, dihapus).
- Angka di receipt/pilot-report konsisten (148 calls / 281 tools / 982.335 token / USD 0,2141148).

## Temuan

### R015-A — P2 (diperbaiki): receipt import onboarding hilang dari backup/restore

`backup()` menyalin DB, artefak dan bare Git, tetapi tidak `workspaces/<project>/onboarding-import.json`. Proyek yang
baseline-nya diblokir (belum pernah aktif) lalu tidak dapat di-onboard ulang sesudah restore: `import_source` menolak
dengan "partial managed import has no provenance". Direproduksi dengan test baru.

Fix: `app/recovery/offline.py` menyalin receipt ke snapshot (restore sudah menyalin semua file `workspaces/` di inventory,
jadi tidak perlu perubahan restore). Regresi:
`tests/recovery/test_offline.py::test_a_blocked_onboarding_import_keeps_its_provenance_across_backup_and_restore`
(import nyata, backup, restore, import ulang → baseline/source SHA sama; inventory memuat file receipt).
Catatan: [recovery.md](../decisions/recovery.md) diperbarui.

## Observasi (bukan klaim perbaikan)

1. Pilot dibantu operator: satu repair tambahan, dua token extension, revisi scope, satu retry PO. Tercatat jujur di
   laporan; bukan bukti workflow berhasil tanpa intervensi.
2. Trigger directed message pada pilot adalah skrip; approval adalah test-user, bukan UAT manual.
3. Outage/quota provider berbayar tidak diuji; hanya automated dengan fixture berlabel.
4. `inventory.json` tidak ditandatangani; `inventory['pins']` tidak dipakai restore untuk validasi (pin diturunkan ulang
   dari DB).
5. `archive/` (hasil import yang diarsipkan) dan dependency cache tidak ikut backup.
6. Receipt import tidak dicocokkan dengan DB/artefak privat saat restore (hanya checksum file).
7. Diff review lead yang sangat besar masih dapat melewati konteks 8000 token (pre-existing, bukan dari DEV-015).
8. Pilot berbayar tidak dijalankan ulang oleh reviewer; angka biaya berasal dari receipt, invoice belum dicocokkan.

## Verifikasi reviewer

| Pemeriksaan | Hasil |
| --- | --- |
| `tests/recovery` (WSL), sesudah fix | 7 passed |
| Windows `pytest tests -q --ignore=tests/workspace --ignore=tests/runtime_spike` | 578 passed, 22 skipped |
| WSL full backend suite (`pytest -q`, Docker) | 855 passed, 804,15s (test flaky DEV-004 lulus pada run ini) |
| GUI `npx playwright test -c playwright.web.config.ts` | 36 passed |
| `npm run build` | lulus |
| `git diff --check` | bersih (hanya peringatan LF→CRLF) |

## Pemetaan AC

Seluruh AC DEV-015 tetap dipetakan di [handoff](DEV-015-handoff.md); review ini tidak menemukan AC yang tidak terpenuhi
selain gap provenance di atas (kini tertutup). Status backlog DEV-015 tetap DONE.

## File yang diubah reviewer

- `apps/backend/app/recovery/offline.py`
- `apps/backend/tests/recovery/test_offline.py`
- `docs/decisions/recovery.md`
- `IMPLEMENTATION-BACKLOG.md` (catatan review)
- `docs/reviews/DEV-015-review-claude.md` (file ini)


## Recheck perbaikan reviewer — Codex, 2026-10-05

R015-A diterima: receipt import ikut inventory/checksum dan restore, sehingga import baseline yang belum aktif
mempertahankan provenance. Tidak ditemukan blocker baru. Verifikasi WSL dari `apps/backend`:

```sh
/root/aiagent-dev002-venv/bin/python -m pytest tests/recovery tests/onboarding/test_source.py \
  tests/release/test_recovery.py tests/workers/test_operator_retry.py -q
```

Hasil: **24 passed, 12,07s**. Pilot berbayar tidak diulang; batas review/R8 di atas tetap berlaku.
Pengguna mengotorisasi commit/push setelah recheck ini.
