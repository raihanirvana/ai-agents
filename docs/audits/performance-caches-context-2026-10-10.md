# Perbaikan performa pipeline — 10 Oktober 2026

Status: implementasi tersedia; verifikasi perilaku dan benchmark belum dijalankan.
Worker tetap dimatikan sesuai instruksi pengguna. Tidak ada perubahan state demo,
approval, konfigurasi model, budget atau database lokal.

## Temuan dan perubahan

| Temuan | Perubahan | Lokasi |
| --- | --- | --- |
| Baseline install/build/gate berulang | Simpan build dan hasil gate baseline yang lulus sebagai artefak immutable. Key mencakup accepted SHA, manifest lengkap, image ID, batas resource dan digest kode eksekusi. Restore memeriksa checksum, build digest dan ketersediaan laporan asal. | `pipeline/execution_cache.py`, `pipeline/workspace.py` |
| Browser baseline berulang | Reuse laporan authoritative hanya untuk target baseline, suite, node image, runner dan policy yang sama. Validasi kembali identitas, UAC, counts dan smoke. Invocation asal dipertahankan dan cache artifact dipin pada evidence. | `pipeline/execution_cache.py`, `pipeline/runtime.py` |
| `npm ci` mengulang ekstraksi | Snapshot privat `node_modules` diterbitkan tepat setelah installer offline tetap berhasil, sebelum build/test target. Key mencakup byte package/lock, image, env, batas snapshot dan kode installer. Tree diverifikasi saat restore; target tidak mendapat akses ke cache. | `workspace/installation_cache.py`, `workspace/sandbox.py` |
| TL menunggu Docker | Job baru `technical_plan` dan `technical_review` memakai lane interactive. Claim membatasi pekerjaan TL ke capacity−1 jika capacity memungkinkan; default dua slot menyediakan satu untuk chat. Lane execution tetap satu untuk pekerjaan berat. | `pipeline/scheduler.py`, `workers/queue.py` |
| Transcript input tumbuh | Pertukaran source/check lengkap disegel per delapan pertukaran. Simpan maksimal 24 pertukaran tersegel sebagai receipt; observasi yang lebih tua menjadi digest sejarah. Source read aktif dibatasi 64 ribu karakter. User/system/feedback, keputusan dan pertukaran belum lengkap dipertahankan. | `pipeline/transcript.py`, `pipeline/hermes.py` |
| Diagnostik test lulus mahal | Kandidat memakai action trace tanpa snapshot DOM/screenshot per langkah; zip trace dan screenshot disimpan hanya saat gagal. Test lulus membuang trace tanpa export. Baseline mematikan tracing/screenshot. Report/counts seluruh test tetap dicatat. | `pipeline/harness.py`, `contracts/verification/acceptance.py` |
| Polling per handle | `_reap_finished` tidak membaca DB selama semua thread aktif. Handle selesai dan cleanup tertunda diperiksa lewat satu query batch. Claim memisahkan query running/cleanup dengan UNION agar jalur index tersedia. | `workers/supervisor.py`, `workers/queue.py` |

SQL filtering `expired()`, index recovery pada migrasi 0006 dan pemindaian
scheduler di read transaction berasal dari batch sebelumnya. Transaksi tulis
masih diperlukan untuk claim/enqueue/heartbeat, dengan pemeriksaan ulang state
sebelum enqueue. Scheduler masih memindai tiket aktif beserta job scope mereka;
ini belum menjadi scheduler yang sepenuhnya dipicu event.

## Batas dan invalidasi

- Hasil QA kandidat selalu dieksekusi kembali pada target yang dipin. Cache
  baseline tidak memberikan approval QA/UAT/release dan tidak meneruskan approval
  kandidat lama. Hasil fake, incomplete dan infrastructure failure tidak disimpan.
- Baseline build yang gate-nya gagal belum dicache, termasuk baseline yang
  membutuhkan waiver. Perubahan suite memerlukan browser baseline baru; build
  baseline dapat dipakai kembali jika input build tetap sama.
- Original invocation, duration dan command records adalah bukti eksekusi asal,
  bukan eksekusi baru. `cache_hit`, `cache_artifact_id`, `cache_origin` serta log
  `execution.cache` menunjukkan reuse. Restore tetap membuat workspace terdaftar
  untuk cleanup dan mengeluarkan biaya copy/hash/Git.
- Instalasi hit dicatat sebagai `execution_kind=cache_reuse` dan
  `dependency_acquisition=verified-install-snapshot`. Build/test tetap dijalankan.
  Tidak ada klaim bahwa `npm ci` baru dijalankan ketika memakai snapshot.
- Cache instalasi dibatasi 512 MiB dan 16 entry dengan eviction LRU. Tree yang
  melampaui limit atau memakai symlink keluar/special file tidak diterbitkan.
  Installer yang sukses tetap sukses bila publikasi cache gagal.
- Snapshot instalasi mengganti seluruh `node_modules`, sehingga hasil edit dari
  command target sebelumnya tidak masuk ke cache. Registry/integrity lock tetap
  divalidasi pada hit. Saat cache mati, jalur npm offline sebelumnya dipakai.
- Artefak cache baseline mengikuti pin dan retention ArtifactStore. Belum ada
  kuota disk khusus untuk total artefak cache baseline; setiap output dibatasi
  oleh batas packing/tree yang sudah ada.
- Untuk build/test yang bergantung pada waktu, randomness atau input eksternal
  yang tidak dinyatakan dalam manifest, gunakan `PIPELINE_EXECUTION_CACHE=0`.
  `PIPELINE_INSTALL_CACHE=0` memaksa install kembali. Keduanya default `1` pada
  `.env.example`; perubahan konfigurasi perlu restart worker saat nanti dinyalakan.
- Routing lane berlaku untuk job baru. Job yang sudah antre tidak dimutasi.
  Batas provider tetap dapat menunda TL walaupun lane worker tersedia.
- Transcript runtime lengkap tetap tersedia untuk diagnosis. Pemadatan terjadi
  pada body provider, bukan penghapusan histori persisten. Ledger berubah saat
  blok lama keluar; prompt cache provider belum dijamin dan harus diukur.
  Pesan keputusan/user yang panjang tidak dipotong, sehingga total konteks bukan
  hard cap. Masalah model melakukan ratusan call belum otomatis teratasi.
  Update lanjutan menambahkan pergantian turn dengan checkpoint state dan arsip
  diagnosis berbatas; lihat [audit lanjutan](developer-checks-qa-context-telemetry-2026-10-10.md).
- Trace gagal kini memiliki action trace dan screenshot akhir, tanpa seluruh
  snapshot DOM. Tidak ada replay test untuk memperoleh diagnostik.
- Runner memuat `acceptance.py` lewat mount `/suite`; perubahan kode mengubah
  runner code digest. Target lama harus mengikuti recovery target/runner dan
  menjalani QA/UAT baru. Image tidak perlu dibangun ulang untuk perubahan script
  ini saja karena Dockerfile/dependency runner tidak berubah.

## Verifikasi dan handoff

Pemeriksaan statis: kompilasi source Python yang berubah dan `git diff --check`.
Tidak menambah atau menjalankan tes pada assignment ini karena pengguna meminta
perbaikan tanpa meminta tes/verifikasi perilaku. Tidak menjalankan Docker,
provider atau demo, tidak menerapkan migrasi, dan tidak menyalakan worker.
Implementasi tetap `IN_PROGRESS` / `NOT_REVIEWED` sampai verifikasi perilaku.

Skenario verifikasi berikutnya, ketika diminta:

1. Base/manifest/image identik: first run miss, next run hit dengan bytes/gate
   asal yang sama; ubah setiap input satu per satu dan pastikan miss.
2. Hapus/rusakkan cache atau artefak asal: rebuild/re-execute, tanpa waiver atau
   penerimaan report incomplete. Pastikan fake tidak menerbitkan cache.
3. Ubah suite: browser baseline baru; kandidat selalu invocation baru.
   Perubahan accepted base saat QA tidak boleh dibandingkan dengan base salah.
4. Installer hit/miss menghasilkan tree identik; edit `node_modules` target lalu
   install lagi tidak meracuni snapshot. Periksa cancel, konkurensi, eviction,
   inode/symlink, env/image/package invalidasi dan evidence cache-reuse.
5. Eksekusi berat aktif bersamaan dengan review TL dan chat PO; cleanup gagal
   tetap menahan capacity dan generation lama tetap ditolak.
6. Transcript panjang: pasangan tool-call/result tidak terpisah; feedback dan
   decision terjaga; active read bound; prefix tersegel stabil di dalam blok;
   periksa read/edit setelah receipt dan tidak ada EditConflict akibat proyeksi.
7. Test browser lulus menghasilkan report tanpa artefak; test gagal memiliki
   diagnostik asli bila tersedia; baseline tidak merekam trace/screenshot.
8. Bandingkan total tokens, calls, active time, queue time, cache hit/miss,
   install/build/browser wall time pada demo yang sama. Pisahkan cold/warm run.

Belum ada angka penghematan yang terukur atau review independen untuk batch ini.
Kontrak trace yang digunakan: [Playwright tracing](https://playwright.dev/python/docs/api/class-tracing).
