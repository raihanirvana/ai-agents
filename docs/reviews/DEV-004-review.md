# Review DEV-004 — worker, lane, recovery

Tanggal: 2026-10-05. Reviewer: Codex. Implementer awal: Claude.
Snapshot staged yang direview: Git tree `1af40b74e3f16dcafabcba355c93b3f818736664`.
Baseline HEAD: `25925ab8a1e6c43c1a07afdc9e2c78ace762185c`.

Verdict review independen awal: **NEEDS_FIX**. Semua temuan di bawah sudah diperbaiki oleh
reviewer sesuai instruksi pengguna. Verifikasi fix adalah self-check Codex, bukan independent
re-review perubahan Codex. DEV-004 kembali DONE; R4 belum ditutup, termasuk re-review fix DEV-003.

## Temuan dan perbaikan

| Prioritas | Temuan yang direproduksi | Perbaikan |
| --- | --- | --- |
| P1 | `fail`, completion, dan waiting melepas lease sebelum resource dihentikan. Worker lain dapat claim execution kedua; jawaban input dapat resume saat resource generation lama masih aktif. | Ledger ownership/cleanup di DB, ikut menghitung slot. Retry dijadwalkan sesudah cleanup dan arsip; resume menunggu barrier yang sama. |
| P1 | Crash setelah recovery menandai job failed membuatnya hilang dari query expired. Reconciliation gagal juga melepas execution slot walau resource lama belum terbukti berhenti. | Recovery dapat melanjutkan ledger pada status non-running; token fencing dan scheduling retry atomik/idempotent. Gagal rekonsiliasi tetap menahan slot dan needs_human. |
| P2 | Recovery hanya menangani PGID; callback workspace ada di memori. Setelah worker mati, credential/workspace lama tidak dicabut dan log sebelum crash hilang. | Intent resource persisten sebelum launch, provenance job/generation/lease/supervisor, rekonsiliasi workspace milik run, log append-only persisten dan arsip recovery. |
| P2 | Shutdown memanggil stop sebelum mencabut lease, sehingga runtime masih bisa memakai capability/menyelesaikan job ketika stop berjalan. Stop sinkron juga menghambat interaktif/heartbeat. | Revoke lebih dahulu; stop/recovery terpisah dari scheduling loop. Worker yang masih hidup tidak dianggap mati hanya karena lease timeout. |
| P2 | `total_tokens` dibandingkan dengan output saja. Accounting generation lama tidak menegakkan cap pada run baru; interval sebelum waiting/crash tidak dihitung. | Total memakai input+output/laporan total, lower bound tetap unknown jika parsial; semua run scope aktif dicek, sebelum claim/call juga dicek, final active time dicatat. |
| P2 | Caller dapat mengganti budget key/cap scope lewat enqueue; key pekerjaan yang sama di project berbeda tercampur dalam budget usage. | Budget identity milik queue; policy scope harus cocok, query usage terikat project. Perpanjangan cap hanya melalui keputusan user. |
| P2 | Idempotency enqueue mengabaikan role/runtime/payload/limits; keputusan perpanjangan mengabaikan perubahan payload dan tidak menerima cap token. | Seluruh identitas/payload dibandingkan; authorization harus identik dan tambahan positif, cap token didukung tanpa reset usage. |
| P2 | Log terminal dirujuk job tetapi tidak dipin, jadi cleanup dapat menghapusnya. Kegagalan arsip hanya dicatat di log memori yang tidak tersimpan. | Pin log job terminal; artifact+attachment atomik. Archive/cleanup gagal menghasilkan event/error needs_human dan menahan slot. |
| P2 | Resource yang ditambahkan setelah stop tidak dibersihkan; spawn dapat bocor jika lease dicabut sebelum registrasi. Kepemilikan proses unknown dianggap sukses direkonsiliasi. | Serialisasi stop/registrasi, cleanup langsung untuk resource terlambat, intent sebelum spawn dan pencarian tag; unknown/mixed ownership ditolak. Zombie worker boleh direkonsiliasi, proses lain tetap tidak disentuh. |
| P2/P3 | Capacity execution dapat dikonfigurasi lebih dari satu; quota tidak membangunkan pemanggil yang sedang menunggu slot lokal. | MVP memvalidasi tepat satu execution slot dan retry finite; quota notify waiter dan dicek dalam predicate. |

## Bukti dan pemetaan AC

| AC | Bukti utama pada `apps/backend/tests/workers/` |
| --- | --- |
| 1 | Slot DB/two claimers; `test_other_worker_cannot_claim_until_old_resources_are_cleaned`, `test_slow_stop_does_not_block_interactive_claims`, capacity validation. |
| 2 | Eligibility/domain binding, runtime filter, concurrent claim/idempotency; existing `test_queue.py`, `test_supervisor.py`. |
| 3 | Stale lease/tool/result tests; shutdown revoke-first, late usage menegakkan budget tanpa menerima hasil domain lama. |
| 4 | Cancel process group dan workspace credential/archive; durable log dan pin yang benar-benar diuji dengan cleanup artifact. |
| 5 | Crash antara fence/reap dapat dilanjutkan; failed reconciliation menahan slot; SIGKILL worker nyata (termasuk zombie) membersihkan anak dan workspace, run lain tetap aktif. |
| 6 | Request/checkpoint persisten dan jawaban idempotent; resume tidak melewati cleanup; waiting input tidak di-replay tanpa jawaban. |
| 7 | Call/time/token cumulative lintas retry, token input dan laporan parsial, late accounting, policy/user extension, provider interactive reserve dan quota waiter. |
| 8 | Job/event/log/result fake berlabel; penolakan fake QA tetap oleh domain, tanpa klaim kompatibilitas Hermes/provider nyata. |

Verifikasi aktual dari `apps/backend`:

```sh
# WSL, suite lengkap dengan Docker
/root/aiagent-dev002-venv/bin/python -m pytest -q --tb=short
# Windows
.venv/Scripts/python.exe -m pytest tests/domain tests/persistence tests/workers -q --tb=short
# Worker setelah pengecekan tambahan cleanup/recovery
python -m pytest tests/workers -q --tb=short
```

- WSL lengkap: **453 passed**, 189.92 detik; tanpa skipped, Docker nyata.
- Windows subset: **302 passed, 8 skipped** (1 symlink + 7 POSIX).
- Worker terakhir: WSL **77 passed**; Windows **70 passed, 7 skipped**.
- 31 kasus di `test_review_regressions.py`: **31 gagal di snapshot staged awal**, seluruhnya
  lulus di fix. Original modules dimuat dari Git tree ke proses Python terpisah, tanpa mengganti
  file kerja/index. Helper dan output ada di gitignored `data/dev004/`.
- Dua kasus POSIX tambahan di `test_processes.py`: worker proses nyata dimatikan SIGKILL;
  credential dicabut, arsip dan log tersedia, proses anak hilang, run lain tetap berfungsi;
  intent spawn yang belum memiliki PGID juga ditemukan dan dibersihkan.
- Smoke CLI pada DB disposable: missing DB exit 2; upgrade/check 0003 sehat; none tidak claim;
  fake selesai dan cleanup; kedua mode SIGTERM exit 0.

## Batas handoff

Hermes/pipeline nyata tetap DEV-010; command API/input/cancel/budget user tetap DEV-008.
Supervisi proses/recovery produksi menggunakan Linux/WSL `/proc`; native Windows hanya contract
tests antrean/budget. Limiter provider masih per proses sesuai MVP satu supervisor.
Resource atau worker lama yang tidak bisa dibuktikan berhenti menahan slot dengan needs_human.
Callback cleanup tanpa descriptor yang dikenal disimpan sebagai resource opaque; adapter harus
menyediakan intent dan rekonsiliasi untuk recovery otomatis. Resource generation selesai dibuang saat resume.
Provider usage yang tidak dilaporkan tetap unknown; cap model/tool/time tetap finite. Log terminal
dipin sampai ada kebijakan retensi eksplisit. Pengujian workspace memakai `NoContainerSandbox`,
sedangkan isolasi/container aktual diverifikasi suite workspace Docker; keduanya tidak membuktikan
integrasi runtime Hermes. Tidak ada secret atau data runtime di commit.
