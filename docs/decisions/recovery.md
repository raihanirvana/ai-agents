# Keputusan backup/restore lokal (DEV-015)

Status: implementasi offline POSIX/WSL. Modul `app/recovery/offline.py`; CLI `python -m app.recovery`.
Backup online dan multi-host belum didukung. Operator menghentikan API writer, worker dan preview terlebih dahulu;
`--offline` adalah pernyataan bahwa langkah itu sudah dilakukan, bukan mekanisme penghentian proses.
CLI menolak job running, ledger cleanup, preview aktif, DB rusak/revisi berbeda, atau tip Git/DB tidak cocok.
SQLite `BEGIN IMMEDIATE` menahan writer selama snapshot; koneksi pembaca terpisah memakai backup API agar WAL terikut.

Snapshot berisi SQLite, semua artefak file terdaftar yang tersedia (build bundle, target, evidence, context, checkpoint),
serta objects/refs/HEAD bare Git yang independen. Git objects termasuk commit tanpa ref yang masih direferensikan DB.
Metadata bare baru dibuat dengan config aman; hooks, config sumber, alternates, linked worktrees, runtime homes,
provider keys dan repo asli tidak disalin. Fixture/migration/toolchain/config non-secret terpin pada target/DB.
Build tersimpan sebagai bundle artefak; direktori build/run sementara tidak diperlukan untuk reopen preview.
Receipt provenance import onboarding (`workspaces/<project>/onboarding-import.json`) ikut disalin bila ada. Tanpa itu,
proyek yang baseline-nya diblokir (belum aktif) tidak dapat di-onboard ulang sesudah restore ("partial managed import
has no provenance"). Receipt dicatat di inventory dan diperiksa checksum seperti file lain (review R015-A).

`inventory.json` mencatat checksum/ukuran file, refs per proyek, ID artefak hilang dan pin produk, revisi schema,
serta layout konfigurasi. Ini mendeteksi kerusakan, bukan tanda tangan terhadap penyerang yang mengganti DB/inventory
bersama-sama. Simpan snapshot di storage privat (direktori 0700/file 0600), dengan kontrol akses/backup disk operator.
Snapshot memuat percakapan, source dan data proyek yang sensitif; jangan commit atau membagikannya sembarangan.

Restore hanya ke direktori baru, sebelum writer berjalan. DB dan Git selalu divalidasi ketat; refs harus cocok
inventory dan accepted tip DB. Semua artifact refs/file hashes diperiksa. Hilangnya file yang semula lengkap
menolak restore biasa; `--allow-unavailable` mengizinkan kehilangan *artefak* sebagai restore terdegradasi, dengan
status/event unavailable. DB/Git rusak tidak bisa diabaikan. Artefak yang sudah unavailable pada backup tetap demikian.
Tidak ada klaim QA/UAT baru; IDs/digests/evidence/approval tetap histori yang sama.

Sesi login dan runtime credential lama dihapus. Generation job dinaikkan sebelum resume, ownership/resource/process
lama dibuang; caps/usage, budget key, context/checkpoint dan waiting request tidak direset. `waiting_input` mempertahankan
generation request immutable agar jawaban pengguna masih valid, tetapi tidak memiliki running lease; claim sesudah
jawaban membuat generation dan tool credential baru. Tidak ada worktree lama yang perlu direlokasi: supervisor membuat
attempt baru. Source path existing dipertahankan sebagai referensi read-only; operator memastikan sumber masih tersedia
sebelum export/sync. Restore ke host lain beserta remapping sumber belum dikualifikasi.

Pin/cleanup tetap berasal dari referensi produk, bukan salinan daftar manual. Snapshot/restore belum punya UI retention
atau kompresi otomatis; operator menentukan lokasi, jadwal offline dan penghapusan snapshot lama.
