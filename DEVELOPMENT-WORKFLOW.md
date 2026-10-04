# Panduan mulai implementasi dan code review

Panduan ini memakai [IMPLEMENTATION-BACKLOG.md](./IMPLEMENTATION-BACKLOG.md),
[MVP-BLUEPRINT.md](./MVP-BLUEPRINT.md), dan [ARCHITECTURE.md](./ARCHITECTURE.md).
Tiket pertama DEV-001; panduan ini tidak menandai implementasi sudah dimulai.

## AI pelaksana mendapat konteks dari mana?

Gunakan AI coding yang bisa membaca/mengedit repository ini dan menjalankan
terminal. Instruksikan membaca [AGENTS.md](./AGENTS.md), kemudian tiket yang
ditugaskan dan bagian spesifikasi terkait. Nama `DEV-001` saja dalam chat yang
tidak memiliki akses file belum memberikan konteks yang cukup.

Acceptance criteria pada tiket adalah UAC untuk pembangunan platform ini.
DEV-001 mempunyai ID AC-001-01 sampai AC-001-07 agar implementer/reviewer bisa
memetakan setiap kriteria ke file dan bukti verifikasi. Scope, dependency, dan
verifikasi sudah tersedia; pengguna tidak perlu menulis tiket baru dari nol.
Ini cukup untuk memulai, tetapi pemahaman dan hasil AI tetap diperiksa melalui
kode, bukti, dan review. Ambiguitas material dicatat, bukan ditebak diam-diam.

## Kapan perlu menginstal Hermes?

| Tahap | Kebutuhan |
| --- | --- |
| DEV-001 | Toolchain frontend/backend: Node/package manager, Python/environment, dan Git untuk versioning/review. AI memeriksa yang tersedia dan mendokumentasikan versi/perintah aktual. Tidak memerlukan Hermes, API key, Docker, atau VPS untuk smoke check skeleton. |
| DEV-005 | Container engine yang kompatibel untuk sandbox, tooling Git, dan runner minimum. Belum memerlukan model atau Hermes untuk menguji isolasi. |
| DEV-006 | Runtime kandidat Hermes versi dipin dan provider/model terkonfigurasi untuk percobaan nyata. Instalasi/configuration runtime menjadi bagian tiket ini; jalankan sesi hanya selama eksperimen. |
| DEV-002/003/004/007/008/009 | Fondasi domain/worker/context/API/GUI dapat diuji dengan fake provider berlabel. Tidak menunggu spike nyata. |
| DEV-010 dan DEV-015 | Pipeline dan pilot membutuhkan runtime/provider yang sudah diverifikasi. Bukti nyata tidak dapat diganti fake. |

AI yang membangun platform adalah alat development yang kamu pakai sekarang.
Hermes adalah kandidat executor di dalam platform yang sedang dibangun; keduanya
punya fungsi berbeda. AI pelaksana mengurus setup yang termasuk scope tugas;
bila prasyarat tidak tersedia, ia menjelaskan kebutuhan dan langkah konkretnya.
Jangan meminta pengguna menjalankan Hermes terus-menerus sejak DEV-001.
Versi toolchain/runtime diverifikasi saat implementasi, bukan ditebak dari panduan.

## Prompt pertama untuk AI pelaksana

```text
Baca AGENTS.md, DEVELOPMENT-WORKFLOW.md, dan spesifikasi yang dirujuk.
Kerjakan hanya DEV-001 di IMPLEMENTATION-BACKLOG.md.

Penuhi AC-001-01 sampai AC-001-07. Periksa toolchain yang tersedia,
implementasikan scope tiket, dan jalankan build serta smoke checks aktual.
Update status dan catatan tiket dengan file hasil, perintah/hasil verifikasi,
dan pemetaan tiap AC ke bukti. Jangan mengklaim checks yang belum dijalankan.

Siapkan handoff untuk code review: scope/diff, file baru, bukti verifikasi,
cara menjalankan, serta masalah yang masih diketahui. Jangan mulai tiket berikutnya.
```

Setelah selesai: review kode → perbaiki temuan → ulangi checks yang terdampak →
lanjutkan tiket yang dependency-nya selesai. Jalur spike DEV-001 → DEV-005 →
DEV-006; fondasi domain DEV-002 → DEV-003 → DEV-004 dapat dikerjakan independen.

## Titik review yang disarankan

Ini jadwal review pembangunan platform, terpisah dari approval scope/UAT/release
yang kelak dilakukan pengguna di aplikasi. Pengguna dapat memilih sesi review
dengan Astra atau AI reviewer lain. Baca kode/diff dan jalankan pemeriksaan
relevan; review spesifikasi saja belum merupakan code review.

| Checkpoint | Setelah | Fokus reviewer |
| --- | --- | --- |
| R1 | DEV-001 | Struktur, reproducible setup/lockfiles, config/secret, koneksi frontend/backend, start/stop worker, bukti tujuh AC. |
| R2 | DEV-005, sebelum spike DEV-006 | Broker/path/symlink, metadata Git tidak writable oleh target, larangan akses accepted/ref lain, mounts/network/resource limits, cleanup ownership. |
| R3 | DEV-006 | Adapter memakai API runtime yang nyata, isolasi sesi, evidence fitur dan seeded bug, accounting/caps, stop/restart/klarifikasi. Hasil dan keputusan runtime ditinjau. |
| R4 | DEV-002/003/004, sebelum wiring produksi | Transaksi state/event, scope/version approval, dependency, claim/lease/generation, stale results, waiting input, retry/budget dan recovery. |
| R5 | DEV-007/008/009 | Structured outputs/context/thread, izin commands, input idempotent, SSE replay, cookie host/Origin/CSRF, fake label, board/review scope. |
| R6 | DEV-010/011, sebelum mengandalkan QA/UAT | Runner acceptance independen, mandatory IDs/counts, report palsu/skipped, target/build identity, preview reopen/rebuild, fixture/migration, pin dan feedback. |
| R7 | DEV-012 | Crash antara Git/DB, compare-and-swap, duplicate accept, base berubah, integrator ownership, revalidasi dependency. |
| R8 | DEV-013/014/015, sebelum pilot dinyatakan berhasil | Repo asli utuh, baseline waiver, release freeze/target, reapproval gabungan, bukti alur nyata, restore lokal dengan Git/build/evidence. |
| R9 | DEV-017, sebelum deployment nyata | Login/routing HTTPS/preview cookie, secrets, volume/backup/restore, network policy, restart dan resource limits. |

DEV-016 juga direview sebelum dianggap selesai: event nyata, reconnect, fallback
tanpa WebGL, dan akses board tidak terganggu.

Untuk tiket dalam satu checkpoint, review lebih awal jika perubahan berisiko
atau besar. Jadwal ini tidak menambah kewajiban approval setiap edit. Bila pengguna
meminta satu tiket/checkpoint lalu berhenti, selesaikan hasil reviewable dan
handoff sesuai scope tersebut. Untuk instruksi melanjutkan beberapa tiket,
ikuti batas review yang ditentukan pengguna dan catat checkpoint yang belum direview.

## Bukti yang disiapkan implementer

- ID tiket, AC yang dikerjakan, ringkasan perubahan, dan file terkait.
- Diff terhadap baseline/commit yang jelas bila Git tersedia. Sertakan file baru
  atau untracked yang tidak muncul dalam `git diff`; sebutkan bila repo belum Git.
- Perintah dan hasil checks aktual, termasuk checks gagal atau belum dijalankan.
- Cara reproduksi/jalankan, asumsi/keputusan, known issues, dan sisa pekerjaan.

Reviewer di workspace yang sama membaca file aktual. Jika review melalui chat
terpisah tanpa akses repo, berikan konteks tiket/spesifikasi, diff, file baru, dan
hasil checks; jelaskan bahwa reviewer tidak mengeksekusi kode bila memang demikian.
Jangan mengirim secret atau artefak sensitif untuk melengkapi review.

## Prompt untuk reviewer

```text
Lakukan code review untuk DEV-001 pada repository ini.
Baca AGENTS.md, tiket DEV-001 beserta AC-nya, dan spesifikasi terkait.
Periksa kode/diff serta file baru, bukan hanya ringkasan implementer.

Cari bug konkret, ketidaksesuaian AC, konfigurasi/setup yang tidak bisa
direproduksi, masalah batas akses yang relevan, dan verifikasi yang belum terbukti.
Jalankan checks yang relevan bila environment tersedia. Jika tidak, jelaskan
batas pemeriksaan. Bedakan temuan terkonfirmasi dari dugaan yang perlu dibuktikan.

Laporkan tiap temuan dengan severity, file:line, kondisi pemicu, dampak,
dan langkah reproduksi/verifikasi bila tersedia. Petakan setiap AC ke bukti
atau gap. Akhiri dengan siap lanjut / perlu perbaikan / bukti belum cukup.
Jangan ubah kode dalam sesi review ini.
```

Ganti ID dan fokus untuk checkpoint berikutnya. Gunakan sesi terpisah dari
implementasi bila memungkinkan agar reviewer menilai kode dengan konteks tugas.
Review tidak menjamin bebas bug; targeted tests, evidence, dan manual checks
melengkapi penilaian reviewer.

## Catatan hasil review

Status implementasi `DONE` berarti AC sudah dipenuhi menurut bukti implementer,
bukan berarti independent review sudah selesai. Catat review secara terpisah
pada handoff/backlog: `NOT_REVIEWED`, `NEEDS_FIX`, atau `REVIEWED`, dengan tiket/
checkpoint, reviewer, tanggal, baseline dan head SHA jika tersedia, evidence refs,
findings, fix, dan hasil recheck. Perubahan sesudah review memerlukan review diff
yang relevan; catatan lama tidak membuktikan kode terbaru sudah direview.

Jika review menemukan AC belum terpenuhi, buka kembali tiket `IN_PROGRESS`.
Bug yang memengaruhi batas akses, data, workflow/approval, atau run correctness
harus diperbaiki dan diverifikasi sebelum memakai komponen terkait sebagai dasar
pekerjaan berikutnya. Temuan di luar scope dapat dicatat sebagai follow-up dengan
dampak dan alasan yang terlihat. Jangan menandai temuan fixed hanya dari jawaban AI.
