# Perbaikan worker dan workspace — 10 Oktober 2026

Assignment: memperbaiki empat temuan polling, dependency recovery, ruang tulis
target dan race identitas supervisor. Implementasi siap; review independen dan
verifikasi perilaku belum dilakukan. Worker dimatikan, tanpa demo/retry.

| Temuan | Perubahan | File |
| --- | --- | --- |
| `expired()` membaca semua job | Query ID via union, indeks lease dan JSON cleanup deadline | workers/queue.py, persistence/models.py, migrations/versions/0006_recovery_indexes.py |
| `revalidate_dependency` tidak punya pemanggil produk | Scheduler dispatch + accepted base build/repo gate + harness upstream contract + fenced domain receipt | pipeline/scheduler.py, pipeline/runtime.py, pipeline/dependency_revalidation.py |
| Target memenuhi disk lewat `/work` | Readonly input + bounded tmpfs + bounded validated export saat paused; exit code Docker exec | workspace/sandbox.py, workspace/bounded_io.py, workspace/runspec.py |
| Race `.supervisor-id` | Lock bersama pembaca/penulis + atomic fsynced publication + verifikasi file/owner/ID | workspace/supervisor.py |

## Bukti saat assignment

- `apps/backend/.venv/bin/python -m py_compile` untuk modul yang diubah dan
  migrasi baru: exit 0.
- `git diff --check`: exit 0.
- Inspeksi process list: tidak ada `app.worker` maupun `hermes_worker`.
- Tidak menambah atau menjalankan tes pada assignment ini. Kompilasi bukan
  bukti recovery, concurrency atau keamanan sandbox sudah lulus eksekusi nyata.
- Tidak mengubah DB demo, approval, budget, accepted ref, credential, atau model.
- Migrasi belum diterapkan pada DB lokal; startup berikutnya harus upgrade ke
  `0006` menggunakan jalur migrasi aplikasi sebelum worker berjalan.

## Handoff verifikasi berikutnya

1. Database baru dan upgrade `0005 → 0006`; query deadline timezone/fractional,
   deduplikasi running+cleanup, query plan indeks, dan recovery job historis.
2. Beberapa supervisor memulai bersamaan: ID tunggal stabil; existing ID tetap;
   symlink, special file, owner salah dan ID invalid ditolak.
3. Docker install/build/test/start/smoke dengan Node 22: tmpfs terisi hingga
   batas, host source tidak writable, symlink npm `.bin` tetap berfungsi,
   accepted build readonly, export pada container paused, timeout/cancel/OOM,
   background process, log besar, invalid/oversized tar dan rollback source.
4. Dependency accepted upstream + downstream approved blocked: harness pass
   membuka edge saja. Failed/incomplete/fake/missing proof tetap blocked.
   Scope/base/runner/upstream/request berubah selama eksekusi ditolak; crash
   setelah domain effect tidak menggandakan effect; transitive dependencies
   dan beberapa edge direvalidasi berurutan; UAT/release tidak auto-approved.

## Batas praktis

Revalidasi menjalankan suite upstream terpin pada current accepted base. Ini
tidak membuktikan fitur downstream yang belum ditulis; review/QA/UAT downstream
tetap diperlukan. Tiket terminal mempertahankan histori dan membutuhkan tiket
follow-up untuk perubahan kode, sesuai domain yang sudah ada.

Tmpfs/export dibatasi per container/command; arsip/Git/cache yang terakumulasi
lintas banyak attempt masih membutuhkan kebijakan retention. Copy/export
node_modules berulang dapat menambah waktu command. Karena perubahan sandbox
cukup besar, jalankan verifikasi di atas sebelum demo berikutnya.
