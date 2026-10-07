# Backlog Pembangunan AI Software Development Team

Tanggal: 4 Oktober 2026. Status awal: belum ada implementasi aplikasi.
Revisi: 3, diselaraskan dengan review Astra dalam verdit2.md.

Kebutuhan: [MVP-BLUEPRINT.md](./MVP-BLUEPRINT.md).
Rancangan: [ARCHITECTURE.md](./ARCHITECTURE.md).
Panduan AI: [AGENTS.md](./AGENTS.md).
Cara mulai, setup Hermes, prompt, dan checkpoint review: [DEVELOPMENT-WORKFLOW.md](./DEVELOPMENT-WORKFLOW.md).

## Cara memakai backlog

Ini adalah tiket untuk **membangun platform**, bukan tiket coffee shop/mobile
yang kelak dibuat PO di dalam platform. Satu dokumen ini cukup untuk awal;
Jira/Trello atau aplikasi kita sendiri tidak diperlukan untuk memulai coding.

Setiap tiket memiliki scope, dependency, acceptance criteria, dan verifikasi.
AI membaca panduan repository, mengambil tiket yang ditugaskan, dan mencatat
hasilnya di sini. Semua tiket diawali `TODO`; membuat dokumen ini tidak berarti
implementasi sudah selesai.

Status: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`. Dependency terpenuhi ketika
tiket terkait `DONE`. Jika tiket memerlukan provider nyata yang belum tersedia,
catat blocker dan lanjutkan tiket independen dalam scope instruksi pengguna.

Urutan tabel adalah urutan rekomendasi; ID tetap agar referensi tidak berubah.
Jalur spike: DEV-001 → DEV-005 → DEV-006. Workspace minimum tidak menunggu
scheduler penuh. Jalur domain DEV-002 → DEV-003 → DEV-004 → DEV-007 → DEV-008/009
dapat berjalan independen dari provider nyata. DEV-010 menghubungkan keduanya
dengan database/job/approval produk sebagai acuan otorisasi dan usage.
DEV-016/017 adalah tahap setelah pilot, bukan prasyarat keberhasilan MVP.

Struktur direktori acuan sama dengan arsitektur §14: `apps/web`, `apps/backend`,
`agents/<role>`, `contracts`, `docs`, `infra`; runtime data/workspaces/verification
gitignored. Review historis tidak mengganti spesifikasi revisi terbaru.

Kontrak review Astra diterapkan pada tiket berikut:

| Kontrak | Tiket utama |
| --- | --- |
| A: build/verification target | DEV-002, DEV-003, DEV-005, DEV-010/011/012/014 |
| B: metadata Git dan broker | DEV-005, DEV-006, DEV-012 |
| C: hasil acceptance authoritative | DEV-006, DEV-010, DEV-015 |
| D: host/cookie preview | DEV-001, DEV-008, DEV-011, DEV-017 |
| E: durable waiting input | DEV-004, DEV-007, DEV-008, DEV-015 |
| F: run caps dan usage | DEV-004, DEV-006, DEV-007, DEV-015 |
| G: waiver baseline pengguna | DEV-003, DEV-010, DEV-013 |
| H: pin dan restore lokal | DEV-002, DEV-011, DEV-015, DEV-017 |

## Daftar tiket

| ID | Hasil | Dependency | Status |
| --- | --- | --- | --- |
| DEV-001 | Skeleton repository dan cara menjalankan lokal | — | DONE |
| DEV-005 | Workspace Git dan sandbox minimum | DEV-001 | DONE |
| DEV-006 | Spike runtime nyata dan keputusan adapter | DEV-005 | DONE |
| DEV-002 | Persistence, migrasi, dan event | DEV-001 | DONE |
| DEV-003 | Domain tiket, versi scope, dan approval | DEV-002 | DONE |
| DEV-004 | Worker persisten, dua lane, dan recovery | DEV-003 | DONE |
| DEV-007 | Soul, context, model client, dan pesan antar-agent | DEV-002, DEV-004 | DONE |
| DEV-008 | API aplikasi, autentikasi lokal, dan SSE | DEV-003, DEV-004, DEV-007 | DONE |
| DEV-009 | GUI board, chat PO, dan review scope | DEV-008 | DONE |
| DEV-010 | Pipeline lead/developer/QA dengan bukti test | DEV-003, DEV-006, DEV-007 | DONE |
| DEV-011 | Preview kandidat dan feedback UAT | DEV-008, DEV-009, DEV-010 | DONE |
| DEV-012 | Integrasi accepted, dependency, dan recovery Git/DB | DEV-005, DEV-010, DEV-011 | DONE |
| DEV-013 | Onboarding repository existing | DEV-005, DEV-012 | DONE |
| DEV-014 | Release yang dibekukan dan verifikasi gabungan | DEV-012, DEV-013 | DONE |
| DEV-015 | Pilot end-to-end dan panduan operasional | DEV-009, DEV-011, DEV-013, DEV-014 | DONE |
| DEV-016 | Kantor Three.js dari aktivitas nyata | DEV-015 | DONE |
| DEV-017 | Packaging satu VPS | DEV-015 | TODO |

## DEV-001 — Skeleton repository dan cara menjalankan lokal

### Catatan pengerjaan
Status: DONE
Pelaksana/sesi: Codex, sesi 2026-10-04
Rencana singkat: buat skeleton React/Vite/TypeScript, FastAPI health API, worker
proses terpisah dengan shutdown sinyal, konfigurasi lokal non-secret, ignore
runtime, dan README reproducible. Repo awal hanya berisi spesifikasi dan belum
memiliki Git metadata.
File hasil: `apps/web/**`, `apps/backend/app/**`, `apps/backend/requirements.txt`,
`apps/backend/requirements.lock`, `package.json`, `package-lock.json`, `.env.example`,
`.gitignore`, `README.md`.
Verifikasi:
- `npm ci` — berhasil; 70 package terpasang dari lockfile.
- `npm run build` — berhasil; TypeScript dan Vite production build selesai.
- `npm audit` — 0 vulnerability setelah Vite dikunci pada 6.4.3.
- API dijalankan lewat `cd apps/backend && ./.venv/bin/python -m app`; `curl -i
  http://127.0.0.1:8000/health` memberi HTTP 200 dan `{"status":"ok"}`.
- CORS smoke dengan Origin `http://127.0.0.1:5173` memberi
  `Access-Control-Allow-Origin` exact-origin; web dev server memberi HTTP 200.
- Worker idle mencetak readiness lalu keluar kode 0 setelah SIGINT dan SIGTERM,
  masing-masing dengan pesan shutdown tertib.
- `npx --yes playwright screenshot --browser chromium --wait-for-timeout 1000
  http://127.0.0.1:5173/ /tmp/dev001-connected.png` dengan API hidup; hasil
  browser menampilkan “Backend terhubung”.
- Setelah API dihentikan, `npx --yes playwright screenshot --browser chromium
  --wait-for-timeout 3500 http://127.0.0.1:5173/ /tmp/dev001-unavailable.png`;
  hasil browser menampilkan “Backend tidak tersedia” dan halaman tetap tampil.
  Screenshot berada di `/tmp`, tidak dimasukkan sebagai artefak repo.

Pemetaan AC:
- AC-001-01: health fetch, timeout/error handling dan label connected/unavailable
  ada di `apps/web/src/App.tsx`; kedua state diverifikasi di Chromium, termasuk
  saat API dimatikan.
- AC-001-02: `apps/backend/app/api.py` dan `apps/backend/app/__main__.py`; health
  smoke lulus dan API start tanpa provider/key atau database.
- AC-001-03: `apps/backend/app/worker.py`; readiness, idle wait, SIGINT dan SIGTERM
  telah dijalankan dan exit code 0.
- AC-001-04: `.env.example`, `.gitignore`; example hanya local defaults, Vite
  hanya membaca prefiks `VITE_`, dan generated/runtime paths di-ignore.
- AC-001-05: `README.md`, `package.json`, `package-lock.json`, requirements files;
  versi dan perintah dicatat. `uv` tidak tersedia; backend memakai pip/venv.
- AC-001-06: `apps/web/vite.config.ts`, `apps/backend/app/__main__.py`,
  `apps/backend/app/api.py`; default loopback/port benar dan CORS smoke lulus.
- AC-001-07: build/API/worker dan dua keadaan UI diverifikasi aktual seperti di
  atas. Chromium dijalankan via `npx` sementara, tidak ditambahkan ke dependency
  proyek.

Blocker/sisa: tidak ada untuk scope DEV-001.
Handoff R1: file baru tercantum di atas (repo belum Git, jadi tidak ada diff Git
atau baseline SHA). Cara menjalankan ada di `README.md`. Batas scope yang diketahui:
UI hanya menunjukkan skeleton health state; scheduler dan agent loop berada di
tiket berikutnya. Tiket berikutnya setelah DEV-001 selesai sesuai dependency
adalah DEV-005 (jalur spike) atau DEV-002 (fondasi domain independen).
Review: REVIEWED — R1, Codex, 4 Oktober 2026. Empat temuan diperbaiki dan
seluruh AC direcheck. Bukti terbaru mengungguli catatan implementasi awal di atas:
[docs/reviews/DEV-001-R1.md](./docs/reviews/DEV-001-R1.md).
Tambahan file hasil review: `apps/backend/app/config.py`,
`playwright.config.ts`, `tests/smoke/health.spec.ts`, dan report R1.
Hasil terbaru: build lulus, 5/5 regression tests lulus, API stop/recovery tanpa
reload lulus, worker SIGINT/SIGTERM exit 0, env/CORS/ignore checks lulus,
npm audit dan pip-audit tidak menemukan vulnerability yang diketahui.

**Tujuan:** fondasi yang bisa dijalankan tanpa akun model atau VPS.

**Scope:** React/Vite/TypeScript, FastAPI, entry point worker, konfigurasi,
dependensi yang dikunci, dan dokumentasi setup. Gunakan `apps/web`, `apps/backend`,
`agents/<role>`, dan direktori pendukung sesuai arsitektur §14.

**Acceptance criteria (UAC implementasi):**

- **AC-001-01:** Halaman awal React tampil dan membaca health API untuk menunjukkan
  backend connected/unavailable. Backend dimatikan menunjukkan unavailable,
  bukan tetap connected atau membuat halaman crash.
- **AC-001-02:** `GET /health` mengembalikan HTTP 200 dan JSON `{"status":"ok"}`.
  Backend dapat start tanpa provider/key, Hermes, container engine, atau DB produk.
- **AC-001-03:** Worker mempunyai entry point proses terpisah, menunjukkan readiness,
  dapat menunggu tanpa job, dan berhenti tertib lewat SIGINT/SIGTERM. Belum
  menjalankan scheduler produksi, agent loop, atau kebutuhan runtime/model.
- **AC-001-04:** `.env.example` hanya placeholder/default non-secret. `.gitignore`
  mengecualikan secrets, database/WAL, workspace/verification runtime, dependency
  lokal, dan build/artifact runtime. Secret tidak dikirim ke bundle frontend.
- **AC-001-05:** README mencatat toolchain/version yang dipakai serta perintah
  install, build, dan menjalankan web/API/worker dari checkout. Dependency dikunci;
  perintah cocok dengan file/script aktual dan tidak meminta key/Hermes/VPS.
- **AC-001-06:** Web/API menggunakan 127.0.0.1 dengan port terkonfigurasi (default
  5173/8000); localhost disisihkan untuk preview. Health request frontend bekerja
  pada origin kontrol yang didokumentasikan; autentikasi lengkap menjadi DEV-008.
- **AC-001-07:** Build frontend, smoke health API, start/stop worker, dan connected/
  unavailable UI diverifikasi aktual. Catatan tiket memetakan AC-001-01–07 ke file,
  perintah/hasil/evidence; checks yang belum bisa dilakukan ditandai belum teruji.

**Verifikasi:** jalankan checks sesuai tujuh AC, catat hasil dan known issues.
Tidak perlu membuat alur agent palsu atau test yang sekadar menyalin implementasi.
**Handoff review:** checkpoint R1 pada DEVELOPMENT-WORKFLOW.md; siapkan diff/file
baru, bukti checks, dan cara menjalankan sebelum melanjutkan sesuai assignment.

## DEV-002 — Persistence, migrasi, dan event

### Catatan pengerjaan
Status: DONE — perbaikan dan re-review Codex selesai 2026-10-05.
Pelaksana/sesi: Claude (Sonnet 5.5), sesi Windows 2026-10-05
Rencana singkat: paket `apps/backend/app/persistence/` (engine SQLite WAL + FK +
`BEGIN IMMEDIATE`, 12 model SQLAlchemy, helper state+event satu transaksi, artifact
store dengan checksum/availability, pin turunan dari referensi produk + cleanup),
Alembic `apps/backend/migrations/` dengan trigger penegak aturan, CLI
`python -m app.persistence`, dan test di `apps/backend/tests/persistence/`. API tetap
bisa start tanpa DB (AC-001-02); DB belum diwire ke API/worker (itu DEV-003/004/008).
Tidak memakai provider/model/Hermes.

File hasil: `apps/backend/app/persistence/{__init__,__main__,columns,models,db,changes,
events,artifacts,pins,cleanup,usage,messages,migrate}.py`, `apps/backend/alembic.ini`,
`apps/backend/migrations/{env.py,script.py.mako,versions/0001_initial_schema.py}`,
`apps/backend/tests/persistence/{conftest,factories,test_migrations,test_constraints,
test_events_changes,test_artifacts,test_pins_cleanup,test_usage_messages,test_restart}.py`,
`apps/backend/app/config.py` (DATA_DIR/DATABASE_PATH/ARTIFACT_DIR), `.env.example`,
`apps/backend/requirements.{txt,lock}` (SQLAlchemy 2.1.3, Alembic 1.20.0, Mako, MarkupSafe),
`README.md`, `docs/decisions/persistence.md` (rancangan, aturan storage, cara pakai, batas).

Pemetaan AC ke bukti (test dalam `apps/backend/tests/persistence/`):
- AC migrasi/FK/revision/unique: `test_migrations` (DB kosong -> 12 entitas, head, tanpa drift
  terhadap model, downgrade lalu upgrade, migrasi gagal rollback total, WAL/FK/synchronous/
  busy_timeout, CLI check) dan `test_constraints` (36 test: FK, unique, CHECK, trigger).
  Revision: trigger "naik tepat satu" + `version_id_col`; test di `test_events_changes`.
- AC state+event satu transaksi: `apply_change`; `test_events_changes` (commit bersama, error
  setelah perubahan, IntegrityError setelah event, stale revision, tanpa celah cursor).
- AC cursor/artefak checksum: `read_events(after=cursor)`, cursor tidak reset lintas restart
  (`test_restart`), `ArtifactStore` + `test_artifacts` (checksum, read-only, canonical JSON,
  nama/path tidak aman, rollback menghapus file).
- AC target manifest/evidence refs/input request/usage/waiver: FK komposit
  `(target_artifact_id, target_digest)`, artifact `target_manifest`, messages input
  request/answer (satu jawaban), `jobs.usage` + `scope_usage` (unknown bukan 0), approvals
  `baseline_waiver`: `test_constraints`, `test_usage_messages`.
- AC pin dan cleanup: `pins.py`, `cleanup.py`, `test_pins_cleanup` (pin approval/release/
  verification/kandidat aktif/job/lampiran, superseded melepas pin, dry-run, umur, ownership,
  proyek lain, cleaned tidak hidup lagi); file hilang/rusak/terpotong -> `unavailable` + event,
  approval baru ditolak storage: `test_artifacts`.
- AC data tersedia setelah restart: `test_restart` (reopen, `os._exit` setelah commit,
  `os._exit` saat memegang kunci tulis sebelum commit tanpa sisa state/event/cursor).
- AC transaksi pendek/konflik writer: `test_events_changes` (6 thread x 5 update tanpa
  kehilangan, retry `transact` hanya pada RevisionConflict, writer kedua menunggu kunci,
  pembaca tidak diblok) dan `test_usage_messages` (update usage dan jawaban bersamaan).

Verifikasi aktual (dari `apps/backend`):
- Windows: `.venv/Scripts/python.exe -m pytest tests/persistence -q`: **109 passed, 1 skipped**
  (symlink tidak bisa dibuat pada host ini). Linux/WSL venv baru dari `requirements-dev.txt`
  (Python 3.13.16, SQLite 3.53.1): **110 passed, 0 skipped**, 6.5 detik.
- `python -m app.persistence upgrade` lalu `check` pada DB baru lewat `DATABASE_PATH`: revisi 0001,
  "database is healthy", exit 0. `pip check`: tidak ada requirement rusak.
- Regresi: import `app.api`/`app.worker` tidak membuat database; `tests/runtime_spike` di WSL:
  40 passed. Test workspace DEV-005 tidak dijalankan ulang (kode workspace tidak berubah).
- Mutation check: mengganti `BEGIN IMMEDIATE` dengan `BEGIN` membuat
  `test_concurrent_usage_updates_are_not_lost` gagal; kode dikembalikan.
- `git diff --check` pada file baru bersih; pemindaian secret pada file baru bersih.

Temuan saat pengerjaan (semua diperbaiki sebelum DONE): kolom JSON opsional ter-infer NOT NULL
(terlihat di autogenerate); CHECK `availability` dan `storage_shape` meloloskan NULL (alasan/
size kosong); nama artifact dengan `/` menjadi sub-direktori; `Database.read()` meng-expire
objek karena `rollback()`; modul bernama `types.py` menimpa stdlib saat dijalankan dari
direktorinya (diganti `columns.py`).

Keterbatasan: bukan scheduler (claim/lease/heartbeat = DEV-004); slot execution tunggal belum
dipaksa di storage; status kandidat/dependency/release adalah usulan dari ARCHITECTURE §4 dan
dapat direvisi DEV-003 lewat migrasi (migrasi batch harus membuat ulang trigger); file yatim
akibat crash antara tulis file dan commit belum ada penyapunya; database di `/mnt/c` dari WSL
tidak diuji; backup/restore adalah DEV-017. Detail: `docs/decisions/persistence.md`.
Handoff R4: diff = 6 file tracked yang berubah + file baru di atas (belum di-stage; gunakan
`git status`/`git ls-files --others --exclude-standard`). Cara menjalankan ada di README bagian
"Database lokal (DEV-002)". Tiket berikutnya sesuai dependency: DEV-003.
Review: NOT_REVIEWED — R4 menunggu DEV-003/004; ini self-check implementer, bukan independent review.

Review DEV-002 oleh Codex (2026-10-05): **NEEDS_FIX**, bukan penutupan R4 penuh.
Scope: staged tree `600bcdcfb0941760b6cb473e91407294afd204a5`, baseline
`5b5ec1d52f0b28032d38678e3185a14e338ed0bb`. Suite Windows dijalankan ulang:
109 passed / 1 symlink skipped. Tiga bug direproduksi dengan DB/file sementara:
(P1) pin approval tidak melindungi build/context setelah kandidat superseded;
(P1) rollback savepoint menghapus file artefak transaksi luar yang kemudian commit;
(P2) idempotency pesan mengabaikan recipient/ticket/metadata/attachments, sehingga
generation berbeda bisa diterima sebagai retry identik. AC pin/cleanup belum
terpenuhi, sehingga DONE dibuka kembali. Kode implementasi/index tidak diubah;
laporan dan status review adalah perubahan dokumentasi unstaged.
Detail, langkah reproduksi, pemetaan AC dan sisa pekerjaan:
[docs/reviews/DEV-002-review.md](./docs/reviews/DEV-002-review.md).

Perbaikan temuan review (Claude, 2026-10-05); ketiganya direproduksi dulu dengan DB/file nyata:
- **R002-01 (P1)**: closure bukti sekarang immutable. `verifications` menyimpan snapshot
  `commit/build/context` (kolom FK baru di migrasi 0001, belum dirilis sehingga direvisi di tempat),
  trigger mewajibkan snapshot = keadaan kandidat saat itu (hasil untuk target lama ditolak), dan pin
  verification mencakup semuanya selamanya. Ekspektasi test lama yang keliru (build tidak dipin setelah
  supersede) dikoreksi. Regresi: supersede dengan approval lalu cleanup nyata `now`+2 hari menyisakan
  commit/build/context/target/evidence (bytes masih terbaca), rebuild mempertahankan build target lama,
  kandidat rejected tanpa verifikasi tetap melepas pin.
- **R002-02 (P1)**: file artifact terikat pada rantai transaksi pembuatnya. Hook rollback hanya membuang
  file milik savepoint yang di-rollback (termasuk rollback dari sub-transaksi flush yang gagal); pelepasan
  savepoint memicu `after_commit` sehingga bookkeeping hanya dilepas pada commit transaksi luar. Regresi:
  rollback inner/commit outer, commit inner/rollback outer, savepoint bersarang, flush gagal di savepoint.
- **R002-03 (P2)**: dedupe pesan membandingkan seluruh payload (recipient, tiket, metadata, lampiran, sender,
  thread, kind; JSON dinormalisasi). Key sama dengan payload berbeda -> `IdempotencyConflict`; jawaban input
  juga membandingkan sender/metadata. Regresi: 8 variasi override, retry identik dengan urutan key berbeda.
Verifikasi: Windows `pytest tests/persistence` **126 passed, 1 skipped** (symlink); Linux/WSL **127 passed**;
`python -m app.persistence check` pada DB baru sehat (tanpa drift). Mutation check: dengan `pins.py`,
`artifacts.py`, `messages.py` versi lama, 13 test baru gagal; dengan kode baru semuanya lulus.
- **Sisa temuan release (dilaporkan pengguna)**: cleanup dapat menghapus build/context yang dirujuk release
  `approved`. Direproduksi: skema `releases` memang tidak punya rujukan ke build/context. Kini `releases`
  menyimpan snapshot beku `build_artifact_id` (wajib), `commit_artifact_id`, `context_artifact_id`
  (FK, frozen oleh trigger, guard available/satu proyek) dan pin release mencakup semuanya selamanya.
  Regresi: cleanup nyata `now`+10 tahun setelah release approved menyisakan build/commit/context/target/
  evidence dengan bytes terbaca; build wajib; kolom beku; artifact tak tersedia/proyek lain ditolak.
  Mutation check: tanpa pin release, 2 test gagal. Audit: semua kolom rujukan artifact kini tercakup pin
  (kecuali `jobs.context_artifact_id` job terminal, sengaja). Windows 129 passed/1 skipped; Linux 130 passed.
Dokumentasi diperbarui di `docs/decisions/persistence.md`. Status DEV-002 kembali **DONE**.
Review: NEEDS_FIX -> perbaikan selesai, **menunggu re-review independen** (ini bukan penutupan R4).

Re-review Codex (2026-10-05): **REVIEWED untuk DEV-002**, tidak menutup R4
DEV-003/004. R002-01/02/03 terverifikasi fixed melalui kode/migrasi dan regresi.
Sisa pin release yang ditemukan pada recheck juga fixed: release mem-pin snapshot
build/commit/context sendiri, build wajib, referensi frozen serta tersedia/satu
proyek diperiksa storage. Cleanup nyata 10 tahun kemudian menjaga bytes build/
context; artefak tak dirujuk tetap dibuang. Suite dijalankan ulang: Windows
**129 passed / 1 skipped**, 8.66 detik; WSL **130 passed**, 7.95 detik.
Tidak ada temuan blocking tambahan pada scope perbaikan ini. Log review lama
dipertahankan sebagai riwayat; status akhir DONE/REVIEWED. Catatan lengkap:
`docs/reviews/DEV-002-review.md`. Tidak ada commit/push atau perubahan staging
oleh reviewer. Migrasi 0001 direvisi sebelum rilis; DB percobaan versi sebelumnya
tidak otomatis di-upgrade hanya karena revision ID masih sama.

**Tujuan:** restart tidak menghilangkan pekerjaan atau percakapan.

**Scope:** SQLite WAL, SQLAlchemy/Alembic, penyimpanan artefak lokal, dan 12
entitas inti di arsitektur: projects, tickets, ticket_versions, approvals,
dependencies, messages, jobs, candidates, verifications, artifacts, releases,
events. Integration operation boleh disimpan sebagai data kandidat sesuai desain.

**Acceptance criteria:**

- Migrasi membangun database baru; foreign key, revision, dan unique constraints
  yang diperlukan ditegakkan oleh storage.
- State change dan event terkait disimpan dalam transaksi yang sama.
- Event memiliki urutan/cursor persisten; artefak menyimpan referensi dan checksum.
- Build/verification target manifests immutable disimpan sebagai artifacts;
  approval/verification mempunyai target/evidence refs. Input request metadata,
  usage per scope, dan baseline waiver memakai 12 entitas yang sudah ada.
- Pin refs produk melindungi commit/build/evidence/context kandidat aktif,
  approval, dan release. Cleanup hanya data unpinned; missing/corrupt unavailable.
- Data proyek, percakapan, scope, dan approval tetap tersedia setelah restart.
- Transaksi pendek dan konflik writer ditangani tanpa kehilangan perubahan.

**Verifikasi:** migrasi database kosong, rollback transaksi gagal, reopen database,
state/event konsisten, cleanup pin, serta referensi artefak hilang/corrupt.

## DEV-003 — Domain tiket, versi scope, dan approval

### Catatan pengerjaan

Status: **DONE** setelah perbaikan review Claude. Pelaksana: Codex, 2026-10-05.
Putaran fix R003-01..06: fence repair langsung dari counter dan pertahankan blocker
independen; contract invalidation integrator-only dan hanya edge terdampak;
receipt broker per attempt untuk commit terdeduplikasi; Accepted tetap historis
tanpa blocker baru; malformed scope menjadi domain Invalid. Required checks
dipertahankan melintasi scope edit dan aturan tiga putaran ditulis eksplisit.
Regresi Windows/WSL dan recheck implementer selesai; commit/push diotorisasi
pengguna setelah perbaikan. Dependency DEV-002 DONE/REVIEWED.
Rencana awal: command service domain di atas transaksi persistence, aktor/intensi
spesifik, versi scope/proposal, batch atomik, dependency DAG/pin/revalidasi,
fencing attempt, target/evidence QA/UAT/release, waiver, cancel dan repair.

File hasil: `apps/backend/app/domain/{__init__,types,evidence,service}.py`,
`apps/backend/migrations/versions/0002_workflow.py`, tambahan metadata JSON pada
`app/persistence/models.py`, `apps/backend/tests/domain/{__init__,conftest,
test_workflow,test_dependencies,test_evidence,test_transactions}.py`, `README.md`,
dan [handoff/keputusan workflow](docs/decisions/workflow.md).

Seluruh 8 AC dipetakan pada handoff. Bukti utama: approval pengguna exact version,
batch revision/DAG atomik dan fault rollback/concurrent approvers; dependency
menunggu integrated Accepted dan mem-pin version/candidate/SHA; proposal PO
accept/reject/direct edit pengguna; scope change/cancel/feedback mencabut job dan
generation tanpa menghapus usage; role/lease/project/attempt fencing; exact target
dan QA suite/smoke/UAC/evidence; UAT approval lama tidak pindah ke candidate yang
SHA-nya sama setelah rebuild config; release punya receipt/approval sendiri;
waiver fingerprint exact user-only explicit waived; repair kumulatif/bounded
extension; Integrating menolak mutasi hingga receipt tepercaya direkonsiliasi.
Revalidasi mengikat ID permintaan contract change dan execution mandatory/counts,
sehingga receipt perubahan lama tidak dapat dipakai ulang; bukti dipin melalui
lampiran histori pesan.

Verifikasi aktual (`cd apps/backend`):
- Windows `.venv/Scripts/python.exe -m pytest tests/domain tests/persistence -q`:
  **213 passed, 1 skipped** (symlink host). Domain baru: **84 tests**.
- WSL Ubuntu `/root/aiagent-dev002-venv/bin/python -m pytest tests/domain tests/persistence -q`:
  **214 passed**, termasuk symlink pada filesystem Linux.
- WSL suite lengkap `python -m pytest -q`: **354 passed**, termasuk Docker nyata,
  sebelum pengetatan terakhir required checks dependency (3 kasus tambahan).
  Perubahan akhir direcheck pada suite domain+persistence di atas; perbaikan
  terakhir test approval rebuild juga direcheck dengan `pytest tests/domain -q`
  di Windows dan WSL (**84 passed** masing-masing).
- `python -m app.persistence upgrade --db .../data/dev003/check.sqlite3`:
  revisi **0002**; `check` menghasilkan **database is healthy**. Test upgrade/
  downgrade/re-upgrade DB 0001 berisi data mempertahankan trigger/cursor dan
  menguji JSON/revision/immutability. `git diff --check` lulus.

Test menggunakan DB/artifact asli dan synthetic trusted contract receipts
berlabel, bukan bukti model/QA harness nyata; tidak memanggil provider. Tidak
ada status setter arbitrer atau endpoint baru. Actor harus dibentuk auth/
supervisor tepercaya, tidak dari role JSON klien. Process/credential cleanup dan
job scheduling aktual DEV-004, API/auth DEV-008, produksi harness DEV-010,
Git CAS/crash recovery DEV-012, release orchestration DEV-014. Domain baru
menerbitkan cancellation event dan menerima receipt integrator tanpa menulis Git.

Handoff awal: **NOT_REVIEWED**. Ini self-check implementer; R4 menunggu independent
review DEV-003/004, review DEV-002 tetap terpisah. Diff lengkap termasuk file
baru: `data/dev003/review.patch` (gitignored), baseline
`228e0d8327ac85c03d72e9edbb2bc7e4c8daa30b`; belum stage/commit/push.
Tiket berikutnya sesuai dependency: DEV-004, belum dikerjakan pada assignment ini.

Review DEV-003 oleh Claude (Opus 5.5, 2026-10-05): **NEEDS_FIX**, bukan penutupan R4 penuh.
Scope: staged tree `6b134fc855fe5e21107b91427e9c4697bc7b778c`, baseline
`228e0d8327ac85c03d72e9edbb2bc7e4c8daa30b`. Suite dijalankan ulang: Windows 213 passed /
1 symlink skipped, WSL 214 passed. Temuan direproduksi dengan DB/artifact asli:
(P1) batas repair `needs_human` hilang setelah contract change + revalidasi, attempt ke-4
bisa diikat tanpa `authorize_repair`; (P2) technical-lead dengan job tiket lain dapat
menginvalidasi downstream termasuk kandidat UAT; (P2) commit attempt scope lama dapat
dikirim sebagai kandidat scope baru (provenance commit tidak diikat ke attempt);
(P3) contract change menandai edge upstream yang tidak berubah; (P3) downstream Accepted
mendapat blocker yang tidak bisa diselesaikan; (P3) dokumen scope non-object
menghasilkan AttributeError. AC5 dan AC8 belum terpenuhi, sehingga DONE dibuka kembali.
Pada sesi review tersebut kode implementasi/index tidak diubah; laporan dan status
review saat itu adalah perubahan dokumentasi unstaged. Detail, reproduksi, observasi dan pemetaan AC:
[docs/reviews/DEV-003-review.md](./docs/reviews/DEV-003-review.md).

Recheck perbaikan — Codex, 2026-10-05: **R003-01..06 FIX_VERIFIED oleh implementer**.
Batas repair kini ditegakkan dari counter di eligibility/bind; revalidasi tidak
memberi budget repair, dan bounded extension tidak menghapus dependency gate.
`contract_changed` integrator-only, mencatat reporter/perubahan dan hanya edge
terdampak. Accepted/Cancelled tetap historis tanpa blocker baru. Commit submission
memerlukan receipt broker exact project/ticket/job/generation/scope/base/commit,
terpisah dari artifact Git terdeduplikasi SHA; receipt dipin melalui pesan submission.
Malformed scope dan artifact ID kosong menjadi domain Invalid tanpa partial write/
SAWarning. File baru: `tests/domain/test_review_regressions.py` dan laporan review.

Observasi scope: title/UAC edit mempertahankan pending checks dan merotasi request
ID; UAC benar-benar diubah pada test version 2. Approval scope baru tetap perlu
required checks. Riwayat contract change upstream mencegah tiket baru atau edge
hapus-lalu-tambah melewati checks. Observasi repair: batas semula tiga putaran
review/QA/UAT yang berakhir request-changes, termasuk putaran awal, dipertahankan;
dua perbaikan otomatis, request-changes ketiga memerlukan keputusan pengguna.
Tidak menambah budget tanpa otorisasi. Kontrak/keputusan di `docs/decisions/workflow.md`.

Verifikasi **snapshot final**:
- Windows `.venv/Scripts/python.exe -m pytest tests/domain tests/persistence -q --tb=short`:
  **232 passed, 1 skipped** (symlink host). Domain kini **103 tests**.
- WSL `/root/aiagent-dev002-venv/bin/python -m pytest -q --tb=short`:
  **376 passed**, seluruh suite backend termasuk Docker nyata; tidak skipped.
- **19 regression tests baru gagal pada tree original review**
  `6b134fc855fe5e21107b91427e9c4697bc7b778c`, lalu lulus di kode perbaikan.
  `data/dev003/check-regressions-original.py` (gitignored) memuat original service/
  evidence dari Git object, tidak mengubah file/index. Hanya keyword receipt baru
  dibuang agar signature lama dapat dijalankan; logic lama tidak diganti.
- `python -m app.persistence check --db .../data/dev003/check.sqlite3`:
  **database is healthy** (0002); whitespace/secret scan seluruh reviewable files/
  patch lulus. DB/helper/patch tetap gitignored.

AC5 dan AC8 kini terpenuhi menurut regresi implementer, seluruh delapan AC
terpetakan di handoff. DEV-003 kembali DONE. Review awal Claude NEEDS_FIX tetap
historis; **diff fix belum independent re-review**, R4 belum ditutup. User meminta
commit/push setelah perbaikan, tanpa approval tambahan atau klaim review Claude
atas kode baru. DEV-004 belum dimulai.

**Tujuan:** aturan produk ditegakkan sebelum agent bisa bekerja.

**Scope:** command service untuk workflow Draft sampai Accepted, revision
proposal, batch approval, dependency, feedback, cancel, dan batas repair cycle.
Efek eksternal integrasi Git baru diimplementasikan pada DEV-012.

**Acceptance criteria:**

- Hanya approval pengguna pada scope/version yang tepat membuat tiket eligible.
- Batch approval all-or-nothing dengan expected revision; dependency invalid atau
  siklus ditolak. Dependency kode menunggu kandidat terintegrasi dan Accepted.
- Dependency mem-pin accepted scope/version/candidate/integration SHA. Revert
  lewat tiket baru mempertahankan histori; contract change memicu revalidasi
  downstream dan approval scope baru bila UAC berubah.
- PO mengusulkan revisi; pengguna menerima/menolak atau mengedit langsung.
  Scope berubah mengembalikan review dan membatalkan otorisasi attempt lama.
- Command memiliki izin/intensi spesifik; status tidak bisa diganti arbitrer.
- UAT mengacu candidate/scope, immutable verification target, dan evidence IDs;
  release memiliki target/approval sendiri. SHA sama dengan build/config berbeda
  tidak memenuhi approval target lama.
- Command waiver baseline hanya milik pengguna, dengan fingerprint/base SHA/
  environment/scope tertentu. Agent tidak bisa memberi waiver atau menutup UAC
  tiket dan infrastructure failure dengannya; UI mendapat status waived eksplisit.
- Cancel dan repair limit mengikuti arsitektur; perubahan pada kode Accepted
  menggunakan tiket baru. Integrating harus direkonsiliasi sebelum dimutasi.

**Verifikasi:** test transisi valid/invalid, stale revision, rollback batch,
approval actor salah, dependency siklus/revalidasi, target build yang berbeda,
serta pembatasan aktor dan fingerprint waiver baseline.

## DEV-004 — Worker persisten, dua lane, dan recovery

### Review Claude Batch 1 DEV-001–004 — 2026-10-06

Status: DONE (scope perbaikan review). Perubahan demo sebelumnya sudah
commit/push `cfee58d`. Keenam temuan dikonfirmasi pada kode terbaru: double
extension budget biasa; chat tiket memakai cap default setelah extension;
exception tick melewati shutdown; event cleanup gagal berulang; creation_key
hilang saat edit scope; path workspace bergantung CWD. Rencana: guard latest
retry untuk seluruh budget decision, cap chat dari pool scope yang sama,
recovery loop dengan shutdown finally, dedup event tanpa menghentikan recheck,
preserve creation_key, dan resolve workspace terhadap root repository.
File: workers queue/supervisor, HTTP application, domain service, config,
worker/pipeline wiring dan keputusan worker. App/demo tetap dihentikan.
Hasil review/fix:
1. Authorization berbeda ditolak jika parent stopped sudah memiliki anak retry;
   replay authorization identik tetap mengembalikan ID retry semula.
2. post_message memakai cap peer dengan budget key tiket/scope/pool interaktif
   yang persis sama; default baru tidak menimpa extension atau pool pipeline.
3. run_forever menangkap exception tick, mencetak tipe saja, retry dengan poll,
   shutdown via finally. Exception sebelum thread hidup mengembalikan claim dengan
   backoff dan mengakhiri intent cleanup kosong milik generation itu.
4. needs_human reconcile_failed didedup per alasan dan generation cleanup;
   recheck tetap setiap lease interval, ledger/pin/slot tidak dilepas prematur.
5. creation_key dipertahankan saat reset workflow scope; approval/attempt lama
   tidak dipertahankan dan counter scope baru tetap direset sesuai domain.
6. worker, pipeline, integrator, preview memakai pipeline_workspace_root;
   path relatif terhadap ROOT, default DATA_DIR/pipeline-workspaces, env injection
   tetap didukung. Workspace lama tidak dipindah otomatis; config Mac saat ini
   sudah memakai absolute root sehingga data demo tidak berpindah.

Verifikasi: inspeksi jalur transaksi/fencing dan diff aktual; AST syntax parse
**7 file Python lulus**, `git diff --check` lulus. Tes regresi tidak ditambah atau
dijalankan (tidak ada permintaan eksplisit menjalankan tes); suite Docker lengkap
juga tidak dijalankan. Tidak menjalankan provider/Docker atau menyalakan kembali
BE/FE/worker. Pemeriksaan proses menunjukkan layanan proyek tetap mati.
Handoff: diff sembilan file pada log ini termasuk keputusan worker, keenam
mapping di atas; independent re-review NOT_REVIEWED. Risiko/keterbatasan:
perilaku concurrency/recovery perlu tes regresi; instalasi lama yang memakai
workspace relatif-CWD perlu config absolut atau migrasi eksplisit saat idle.

### Perbaikan macOS — 2026-10-06
Status: DONE. Pelaksana: Codex, assignment pengguna memperbaiki cleanup
dan memulihkan antrean proyek Daftar Belanja. Cleanup sebelumnya memakai `/proc`
Linux dan menahan slot execution setelah qa_plan berhasil pada macOS.
Rencana/file: tambahkan inspeksi ownership/process group/start identity macOS pada
`app/workers/runtime.py`, pin psutil di requirements, jalankan process/recovery
tests pada Mac, lalu recovery melalui supervisor tanpa menghapus hasil/evidence.
File hasil: `apps/backend/app/workers/runtime.py`, `apps/backend/requirements{.txt,.lock}`,
`apps/backend/tests/workers/test_processes.py`, file baru
`apps/backend/tests/workers/test_macos_identity.py`, `docs/decisions/workers.md`.
Verifikasi: `PATH=/Users/23061535/homebrew/bin:$PATH ./.venv/bin/python -m pytest
tests/workers -q` dari apps/backend — **86 passed** pada macOS. Tes meliputi cancel
process group, child yang tertinggal, recovery worker zombie, launch sebelum
registrasi, foreign ownership, akses inspection ditolak, PID reuse, identitas owner
aktif dan record macOS lama tanpa start identity. Run pertama subset tes: 5 failed,
33 passed; akses UID proses root `login` menyebabkan discovery ditolak dan helper
cleanup tes mengirim signal ke group yang sudah kosong. Keduanya diperbaiki, lalu
subset 38 passed dan full suite 86 passed. Linux/WSL belum diuji ulang untuk diff ini.
Recovery aktual: worker lama dihentikan lewat SIGINT, worker pipeline baru pada DB
yang sama merekonsiliasi qa_plan `d6e30b139b264efe8e25a6012e60f61c`; cleanup ledger
dan cleanup_error sudah kosong, suite/result succeeded tetap tersimpan. Scheduler
membuat development tiket #1 dan mengambil qa_plan tiket #4. Tidak ada DB reset,
approval baru, atau replay qa_plan sukses. Error awal tetap sebagai histori result.
Handoff: inspeksi macOS memakai psutil 7.2.2 dan `/bin/ps` PID/UID; Linux tetap
memakai `/proc`. Access denied/unknown ownership menahan cleanup. Pada record
macOS lama tanpa start identity, owner hidup tetap memblokir dan owner hilang
dapat direkonsiliasi. Ini bukan bukti QA/pipeline proyek Daftar Belanja selesai.
Review perubahan ini: NOT_REVIEWED; review historis tidak mencakup diff ini.

### Catatan pengerjaan
Status: DONE (review/fix Codex selesai, 2026-10-05).
Rencana review: verifikasi fencing, cleanup/recovery/resume race, idempotency,
budget kumulatif/token, evidence/ownership. Tambahkan regresi konkret, perbaiki,
jalankan suite Windows/WSL, catat laporan lalu commit/push sesuai instruksi pengguna.
Pelaksana/sesi: Claude (Opus 5.5), sesi Windows + WSL 2026-10-05
Rencana: antrean persisten claim/lease/generation, supervisor dua lane non-blocking, pembatalan
revoke-lalu-stop, recovery dengan verifikasi kepemilikan proses, retry terbatas, waiting input/
quota, cap kumulatif per scope, limiter provider, fake runtime berlabel, worker CLI.
Dependency DEV-003 DONE (diff perbaikan review DEV-003 belum di-re-review independen).

File hasil: `apps/backend/app/workers/{__init__,queue,limiter,runtime,supervisor}.py`,
`apps/backend/app/adapters/{__init__,runtime/__init__,runtime/fake}.py`, `apps/backend/app/worker.py`
(skeleton DEV-001 diganti supervisor), `apps/backend/migrations/versions/0003_job_scheduling.py` +
kolom `jobs.available_at` di `models.py`, helper baca `Workflow.startable` di
`app/domain/service.py` (aturan sama dengan `bind_attempt`), `tests/workers/{__init__,conftest,
test_queue,test_supervisor,test_processes}.py`, assertion head revisi di
`tests/domain/test_transactions.py`, `.env.example`, `README.md`, `docs/decisions/workers.md`.

Pemetaan AC ke bukti (`apps/backend/tests/workers/`):
- AC1 satu slot execution, interaktif tetap jalan, heartbeat/cancel: `test_single_execution_slot_is_counted_in_the_database`,
  `test_only_one_execution_job_runs_at_a_time`, `test_interactive_work_is_served_while_a_long_execution_job_runs`,
  `test_cancel_while_a_tool_is_active_revokes_then_stops_and_keeps_the_log`.
- AC2 claim eligible, dua worker tidak menyelesaikan job sama: `test_two_claimers_never_take_the_same_job`,
  `test_ticket_work_starts_only_when_the_domain_says_it_is_eligible`, `test_claim_honours_runtime_and_available_at`.
- AC3 hasil/tool call attempt lama ditolak: `test_stale_generation_cannot_change_anything`,
  `test_borrowed_or_forged_lease_is_rejected`, `test_a_revoked_attempts_late_result_is_rejected`, edit scope
  domain mencabut attempt development yang berjalan (test eligibility di atas).
- AC4 cancel mencabut credential, menghentikan process group/container, menyimpan log:
  `test_cancel_stops_the_whole_process_group`, `test_cancel_revokes_and_archives_the_attempts_workspace_run`
  (credential DEV-005 ditolak setelah cancel, `ARCHIVE-MANIFEST.json` ada), log artifact pada semua cara selesai.
- AC5 crash/restart, retry terbatas, needs_human, ownership preview terpisah:
  `test_expired_lease_is_fenced_then_retried_once_then_needs_human`,
  `test_recovery_after_a_dead_worker_stops_its_verified_processes_then_retries`,
  `test_recovery_never_kills_a_process_that_is_not_the_attempts`, `test_unverifiable_leftover_processes_block_the_retry`,
  `test_a_crashing_runtime_is_retried_once_and_usage_accumulates`.
- AC6 status berbeda, request/checkpoint sebelum slot dilepas, resume generation baru:
  `test_input_request_and_checkpoint_are_persisted_before_the_slot_is_released`,
  `test_answer_resumes_exactly_once_with_a_new_generation`, `test_answer_after_scope_change_cancels_instead_of_resuming`,
  `test_recovery_ignores_jobs_waiting_for_input`, `test_waiting_for_input_survives_a_restart_and_resumes_once`.
- AC7 cap durasi/model/tool/token, usage tidak reset, limiter interaktif dan quota:
  `test_fake_model_that_keeps_calling_tools_stops_at_the_cap`, `test_active_time_cap_stops_the_job`,
  `test_budget_is_cumulative_across_retries_and_stops_at_the_cap`, `test_extending_a_budget_needs_a_user_decision_and_keeps_usage`,
  `test_usage_per_ticket_scope_survives_retries`, `test_failed_provider_call_is_counted_and_its_usage_is_unknown`,
  `test_interactive_capacity_is_reserved_in_the_provider_limiter`, `test_shared_provider_quota_puts_every_affected_job_in_waiting_quota`,
  `test_quota_wait_is_visible_and_promoted_after_retry_time`.
- AC8 fake berlabel, bukan QA pass: `test_enqueue_is_idempotent_and_fake_is_labelled`,
  `test_fake_job_completes_labelled_and_its_log_is_archived` (`fake_provider=true`, semua event `fake`); domain
  DEV-003 menolak receipt QA ber-`fake_provider`.

Verifikasi aktual (dari `apps/backend`):
- WSL suite backend lengkap `/root/aiagent-dev002-venv/bin/python -m pytest -q`: **420 passed**, 197 detik
  (376 sebelumnya + 44 worker; Docker nyata, tanpa skip).
- Windows `.venv/Scripts/python.exe -m pytest tests/domain tests/persistence tests/workers -q`:
  **271 passed, 6 skipped** (1 symlink + 5 test process group yang butuh POSIX).
- Mutation check di WSL (dipulihkan setelahnya): tanpa fence generation 2 test gagal; budget di-reset per retry
  1 gagal; slot hanya di memori 2 gagal; reaper tanpa cek label 1 gagal.
- CLI di WSL: DB belum dimigrasi -> exit 2 dengan pesan; `--runtime none` tidak meng-claim job, SIGINT exit 0;
  `--runtime fake` menyelesaikan 2 job, SIGTERM exit 0, 8/8 event job berlabel fake.
- `python -m app.persistence check` pada DB baru: revisi 0003, sehat. Test upgrade DB 0001 berisi data kini
  naik ke head (0003) dan turun lagi ke 0001 dengan trigger/cursor utuh.

Temuan saat pengerjaan (diperbaiki): reaper awal hanya memeriksa leader group sehingga anak yatim bisa
lolos (kini semua anggota group diperiksa, kepemilikan campuran tidak dibunuh); call provider yang gagal
tidak ditandai usage `unknown`.

Keterbatasan: hanya fake runtime (Hermes nyata di DEV-010); supervisi proses POSIX-only (WSL di Windows);
deteksi cancel mengikuti interval heartbeat; limiter/quota per proses supervisor (status job tetap
persisten); `total_tokens` hanya bila provider melaporkan token; API jawab input/cancel/perpanjang budget
adalah DEV-008. Detail: `docs/decisions/workers.md`.
Handoff R4: diff = file tracked yang berubah + file baru di atas (belum di-stage; lihat `git status`).
Review: NOT_REVIEWED — self-check implementer; R4 (DEV-002/003/004) menunggu independent review.
Tiket berikutnya sesuai dependency: DEV-007.

Review independen DEV-004 oleh Codex (2026-10-05): **NEEDS_FIX** pada snapshot staged awal
`1af40b74e3f16dcafabcba355c93b3f818736664`. Pengguna menginstruksikan perbaikan langsung dan commit/push.
Semua temuan telah diperbaiki: barrier cleanup persisten sebelum slot/retry/resume tersedia,
rekonsiliasi crash setelah fencing, intent resource sebelum launch dan ownership workspace/PG,
shutdown revoke-first dan stop non-blocking, token input/total/late usage serta final active time,
policy budget terikat project/scope dan keputusan pengguna, idempotency payload penuh,
log persisten/pin artifact/arsip gagal yang terlihat, capacity execution satu dan quota waiter.
File fix: worker queue/runtime/supervisor/limiter, fake adapter, `persistence/pins.py`,
`tests/workers/test_review_regressions.py`, tambahan `test_processes.py`, expectation usage pada
`test_queue.py`, `docs/decisions/workers.md`, laporan `docs/reviews/DEV-004-review.md`.

Verifikasi Codex: WSL backend lengkap **453 passed** (189.92 detik, Docker nyata, tanpa skip);
Windows domain/persistence/workers **302 passed, 8 skipped** (symlink + tujuh POSIX).
Worker setelah pemeriksaan akhir cleanup/recovery: WSL **77 passed**, Windows **70 passed, 7 skipped**.
31 regresi perilaku gagal di kode staged awal dan lulus setelah fix; dua tes POSIX tambahan
mematikan worker nyata dan menguji cleanup workspace/credential/log, run lain tidak tersentuh,
serta spawn sebelum PGID tersimpan. Smoke CLI DB disposable: 0003 sehat, mode none/fake,
SIGTERM exit 0. Helper/output/patch review lokal ada di gitignored `data/dev004/`.
Pemetaan AC lengkap dan batas pengujian ada di laporan review. Tidak memakai provider nyata.

DEV-004 kembali DONE setelah AC terpenuhi. Review awal independen telah dilakukan; diff fix Codex
diverifikasi melalui self-check, **belum independent re-review**. R4 tetap terbuka juga karena
diff fix DEV-003 belum re-review; ini tidak mengklaim R4 selesai. Commit/push mengikuti instruksi pengguna.

**Tujuan:** developer bekerja tanpa memblokir chat PO dan tanpa job ganda.

**Scope:** supervisor, job claim/lease/generation, lane interactive dan execution,
heartbeat, bounded retry, cancellation, serta fake provider untuk pengujian.

**Acceptance criteria:**

- MVP memiliki satu slot execution; pekerjaan interactive tetap diproses saat
  execution berjalan. Supervisor tetap dapat heartbeat dan menerima cancellation.
- Worker hanya claim pekerjaan eligible; dua worker tidak menyelesaikan job sama.
- Hasil/tool call attempt lama ditolak berdasarkan identitas dan izin run.
- Cancel mencabut credential run, menghentikan process group/container milik run,
  dan menyimpan bukti/log yang tersedia.
- Crash/restart memulihkan atau mengantrekan ulang job sesuai lease; retry terbatas
  dan failure yang butuh manusia terlihat. Preview memakai ownership terpisah.
- waiting_input, waiting_quota, stopped, failed, cancelled berbeda. Input request/
  checkpoint dipersist sebelum slot dilepas; ownership run tetap direkonsiliasi.
  Penantian lama menghentikan resource, resume/rerun memakai generation baru;
  lease expired tidak mengulang job waiting tanpa jawaban valid.
- Supervisor menerapkan finite active duration/model calls/tool calls dan
  token/output bila terukur. Usage per scope/tiket tidak direset lintas retry.
  Provider limiter menyisakan kapasitas interaktif dan menampilkan quota/backoff.
- Fake provider diberi label di state/event dan tidak menghasilkan QA pass nyata.

**Verifikasi:** simulasi worker mati, dua claimer, stale submission, cancel saat
tool aktif, restart saat menunggu input, dan permintaan interactive saat execution
lama berjalan. Fake model yang terus meminta tool berhenti pada cap; restart/
retry tidak menghapus usage sebelumnya, quota shared tampil sebagai waiting_quota.

## DEV-005 — Workspace Git dan sandbox minimum

### Review Claude Batch 2 DEV-005/006 — 2026-10-06

Status perbaikan: DONE (implementasi). Pelaksana: Codex. Review perbaikan: NOT_REVIEWED.
Tiga temuan dikonfirmasi dari kode dan jalur pemanggilnya:

1. `WorkspaceSupervisor.smoke_target` menangkap `SandboxError` pada probe saja,
   melanjutkan polling dalam deadline, mengambil log dan menulis smoke JSON.
   Probe yang tidak pernah berhasil menghasilkan `healthy: false` dengan
   `probe_error` terbatas 400 karakter; cleanup tetap berjalan di `finally`.
   Onboarding dapat mencatat laporan start dan blocker health contract yang benar.
2. Install egress mengubah `WorkspaceError` (termasuk `LimitExceeded` dan
   `PathViolation`) menjadi `CommandResult` gagal sehingga `_execute`/build
   mencatat evidence install. Validasi argv dan `.npmrc` juga melalui jalur ini.
   Pembacaan lockfile memakai `max_snapshot_bytes`, bukan batas read broker 5 MB;
   batas snapshot, jumlah paket, registry, integrity, dan jaringan tetap diterapkan.
3. Relay hanya memasang `usage`/`provider` untuk hostname tepat `openrouter.ai`;
   endpoint lain menghapus kedua field, termasuk jika berasal dari runtime.
   Jalur chat terstruktur memiliki masalah `usage` yang sama dan diperbaiki dengan
   pemeriksaan hostname yang sama. Alias provider tetap mengikuti endpoint aktual.

File hasil: `apps/backend/app/workspace/{supervisor,sandbox,dependencies}.py`,
`apps/backend/app/runtime_spike/relay.py`, `apps/backend/app/agents/models.py`,
`docs/decisions/runtime.md`, dan log ini.
Verifikasi aktual: `apps/backend/.venv/bin/python` dengan `ast.parse` pada lima
file Python yang berubah — **5 file OK**; `git diff --check` — **lulus**.
Self-check diff memetakan tiga temuan ke perubahan di atas; bukan review independen.
Tes regresi tidak ditambah/dijalankan karena permintaan lanjutan ini tidak secara
eksplisit meminta tes. Docker, aplikasi, worker, dan inference tidak dijalankan.
Handoff: diff tracked tujuh file ini; regresi yang perlu diperiksa berikutnya ialah
probe timeout dengan log tersimpan, lockfile >5 MB/dibatasi/symlink dengan evidence
install gagal, dan payload relay/chat untuk OpenRouter versus endpoint lain.
Keterbatasan: kegagalan Docker saat start, membaca log, atau cleanup masih dapat
melempar exception; perubahan probe bukan jaminan pemulihan seluruh outage Docker.
Kompatibilitas Hermes dengan provider selain OpenRouter tidak dikualifikasi ulang.
Catatan reviewer tentang modul spike yang hanya di-skim adalah batas cakupan
review, bukan bukti bahwa semua modul tersebut sudah direview penuh.

### Catatan pengerjaan
Status: DONE
Pelaksana/sesi: Claude (Sonnet 5.5), sesi 2026-10-04
Rencana singkat: paket `apps/backend/app/workspace/` berisi run spec persisten,
Git broker tepercaya, snapshot sumber tanpa `.git` dengan validasi path/symlink,
runner manifest React/Vite + target manifest immutable, sandbox Docker, dan
tool broker per run (role/project/ticket/version/attempt/generation). Stop
mengarsipkan bukti sebelum cleanup dengan pemeriksaan label ownership.
File hasil: `apps/backend/app/workspace/{__init__,errors,fsutil,gitbroker,manifest,
runspec,sandbox,supervisor}.py`; `apps/backend/tests/workspace/**` (termasuk target
referensi `fixtures/reference-react-vite/`); `apps/backend/pytest.ini`,
`apps/backend/requirements-dev.txt`; bagian DEV-005 di `README.md`.
Verifikasi (dari `apps/backend`, macOS, Docker Desktop 24.0.5 aarch64, image
`node:22.20.0-alpine`, Python 3.11.6, Git 2.56.0):
- `./.venv/bin/python -m pytest` — 86 passed (67 tanpa Docker, 19 dengan
  container nyata). `pytest -m "not docker"` — 67 passed. Tidak ada container
  `aiagent-*` tersisa setelah run (`docker ps -a`).
- Probe manual sebelum desain: container bridge default dapat menjangkau layanan
  loopback host lewat `host.docker.internal` (HTTP 200 dari server di
  127.0.0.1); `--network none` tidak. Container juga dapat membuat symlink ke
  `/etc/passwd` dan FIFO di bind mount sehingga sinkron balik memakai
  lstat/O_NOFOLLOW dan menolak tipe khusus.
- Satu kegagalan nyata selama pengerjaan: skrip test fixture `node --test test/`
  gagal di Node 22; harness mencatatnya sebagai fase test gagal (bukan pass).
  Fixture diperbaiki menjadi `node --test`.

Pemetaan AC ke bukti:
- Worktree per attempt dengan base accepted tercatat; checkpoint bukan accepted:
  `supervisor.start_attempt/_commit`; `test_attempt_gets_own_worktree_*`,
  `test_checkpoint_is_not_a_candidate_or_accepted`.
- Initial empty commit sebagai base teknis: `GitBroker.init_project`;
  `test_init_creates_empty_base_on_accepted`.
- Sandbox tanpa `.git`; broker memvalidasi path/symlink dan hanya menulis ref
  attempt; hook/helper/filter tidak dijalankan: `fsutil.py`, `gitbroker.py`;
  `test_fsutil.py`, `test_gitbroker.py` (hook repo + global config tidak
  dieksekusi, accepted/ref attempt lain tidak bergerak),
  `test_hostile_sandbox_content_is_rejected_and_nothing_moves` (symlink absolut/
  berantai, FIFO, `.git`, hook, oversize) serta versi container nyata
  `test_planted_symlink_fifo_and_git_dir_from_real_container_are_rejected`.
- Kandidat menunjuk SHA, base SHA, scope version: record `candidates/*.json`;
  `test_submit_candidate_commits_on_attempt_ref_only`.
- Build/target manifest (build digest, manifest revision, toolchain/image ID,
  config, fixture/migration, dependency digest; write-once): `build_target`;
  `test_target_manifest_records_*`, `test_rebuild_of_same_sha_is_a_new_target`.
- Manifest memvalidasi install/build/test/start, toolchain, port, fixture:
  `manifest.py`; `test_manifest.py`.
- Tanpa secret provider/DB kontrol/repo asli/Docker socket; batas filesystem,
  waktu, resource, jaringan: `sandbox.py`; `test_sandbox_has_no_git_secret_socket_
  network_or_root`, `test_command_timeout_*`, `test_memory_and_pid_limits_*`,
  `test_target_shell_cannot_reach_git_metadata_or_other_runs`.
- Tool broker mengotorisasi role/project/ticket/version/attempt/lease:
  `supervisor.authorize` (identitas dari spec/state persisten, bukan argumen);
  `test_credential_must_match_run_*`, `test_role_permissions_*`,
  `test_stale_generation_*`, `test_cancelled_attempt_cannot_submit_*`,
  `test_tampered_manifest_*`.
- Stop mengarsipkan bukti dahulu dan menghormati ownership:
  `test_stop_archives_evidence_before_cleanup_*`, `test_cancel_while_tool_is_
  active_*`, `test_cleanup_respects_ownership_labels` (preview, run lain, dan
  supervisor lain tidak disentuh), `test_reap_orphans_*`.
- Verifikasi tiket: build/run target referensi React/Vite nyata (npm ci, build,
  `node --test` 2 pass, kandidat, target, smoke health, rebuild SHA sama →
  target baru): `test_reference_target.py`.

Keterbatasan setelah perbaikan R2:
- `allow_install_egress` kini hanya mengizinkan supervisor mengunduh tarball HTTPS
  dari registry.npmjs.org dan memverifikasi integrity SHA-512. Semua container,
  termasuk install, selalu `--network none`; npm ci berjalan offline dengan
  lifecycle scripts dimatikan. Private registry, dependency Git/local, project
  `.npmrc`, dan override environment runner ditolak untuk MVP.
- Kuota disk bind mount sandbox belum diterapkan (batas entry/byte diperiksa saat
  sinkronisasi, tmpfs dibatasi). Batas memori/CPU/PID/waktu diterapkan.
- Expiry/heartbeat lease tetap menunggu job DB DEV-004/010. R2 menambahkan
  serialisasi operasi, pembatalan generation lama, pemeriksaan ulang kredensial,
  serta test interleaving cancel/renew ketika command aktif.
- Setelah crash, `reap_orphans` memerlukan rekonsiliasi status/generation run;
  active run dengan generation sama tidak otomatis dianggap yatim. Full process
  crash recovery belum diuji. Stop kini menunggu evidence command, menyimpan
  snapshot uncommitted yang valid, dan mengulang arsip parsial sebelum cleanup.
- Git config repo dimiliki supervisor; global/system config dan hook dinonaktifkan.
  Integrator accepted ref baru ada pada DEV-012; ref writes per project sekarang
  diserialkan untuk mencegah false positive pada commit paralel.
- Diuji di macOS/Docker Desktop. Linux bind-mount UID belum diuji; Windows tidak
  didukung harness workspace (flock/dir_fd).
- Tidak ada Hermes/model/provider yang dipakai; tidak ada klaim terkait DEV-006.

Blocker/sisa: tidak ada blocker tersisa untuk DEV-005; batas harness tercatat di atas.
Handoff R2: file baru tercantum di atas; periksa `git status` untuk file baru,
serta tracked diff README/backlog. Baseline Git: `324acda`. Cara menjalankan di README. Fokus R2
sesuai DEVELOPMENT-WORKFLOW: broker/path/symlink, metadata Git tidak writable,
ref lain, mounts/network/resource, cleanup ownership. Tiket berikutnya sesuai
dependency: DEV-006 setelah R2 (butuh provider/key nyata; tanpa itu BLOCKED) atau
DEV-002.
Review: REVIEWED — R2, Codex, 4 Oktober 2026. Sembilan temuan diperbaiki langsung
sesuai instruksi pengguna. Bukti terbaru:
[docs/reviews/DEV-005-R2.md](./docs/reviews/DEV-005-R2.md).
Tambahan hasil review: `dependencies.py`, `test_review_regressions.py`, perbaikan
broker/fs/lifecycle/build/runner, tests Docker/reference target, README, dan report.
Gabungan hasil terbaru per test ID: 103 test unik lulus (81 non-Docker, 22 Docker).
Suite penuh sempat 100 passed/1 failed karena read timeout registry pada rebuild;
setelah bounded retry, recheck terdampak 32 passed; filesystem/regressions 38 passed.
Tidak ada container test tersisa. Perubahan belum di-commit/push dalam sesi review.

**Tujuan:** agent dapat mengubah dan menjalankan kode di workspace terisolasi.

**Scope:** managed repository untuk proyek baru, worktree supervisor/source snapshot
sandbox per attempt, Git broker, runner manifest React/Vite, container, dan trusted
supervisor minimum. Tidak menunggu DEV-004: harness standalone memakai scoped run
spec/manifest persisten dengan identitas, ownership/generation, batas resource,
dan provenance. Harness bukan scheduler/approval produk. Wiring DB/job di DEV-010;
onboarding sumber pengguna DEV-013, integrasi accepted DEV-012.

**Acceptance criteria:**

- Setiap attempt memakai worktree tersendiri dengan base accepted yang tercatat;
  checkpoint pekerjaan gagal tidak dianggap sebagai accepted code.
- Supervisor membuat initial empty commit sebagai base teknis proyek baru;
  foundation code buatan developer tetap membutuhkan approval/review/QA/UAT.
- Sandbox hanya mendapat source tanpa `.git`/common Git metadata. Diff/commit/
  checkpoint melalui broker yang memvalidasi path/symlink dan ref attempt; hanya
  integrator mengubah accepted. Host tidak menjalankan hook/helper/filter repo.
- Kandidat menunjuk immutable SHA, base SHA, scope version, dan artefak relevan.
- Build/target manifest minimum mencatat build digest, manifest revision,
  toolchain/image/config/fixture/migration identity untuk evidence berikutnya.
- Manifest memvalidasi install/build/test/start, toolchain, port, dan fixture.
- Kode target tidak mendapat secret provider, kontrol DB, repo asli, atau Docker
  socket. Batas filesystem, waktu, resource, dan akses jaringan diterapkan.
- Tool broker mengotorisasi role/project/ticket/version/attempt/lease; hanya
  supervisor tepercaya mengelola container dan operasi kontrol.
- Stop mengarsipkan bukti yang tersedia sebelum cleanup; ownership mencegah
  cleanup salah terhadap preview atau attempt lain.

**Verifikasi:** build/run target referensi dan percobaan akses terlarang, path
escape, process timeout, serta cleanup pada run yang sudah berhenti. Shell target
gagal mengubah accepted/ref attempt lain/config/hooks/metadata proyek lain;
commit broker yang sah berhasil dan attempt cancelled ditolak.

## DEV-006 — Spike runtime nyata dan keputusan adapter

### Catatan pengerjaan
Status: DONE (4 Oktober UTC / 5 Oktober WIB 2026)
Review R3: REVIEWED — Claude, dikonfirmasi pengguna 2026-10-05;
catatan [DEV-006-R3](./docs/reviews/DEV-006-R3.md).

Hasil akhir: Hermes 0.21.5 pada commit pin dijalankan melalui embed `AIAgent`,
OpenRouter/Qwen gratis, broker DEV-005, relay terukur dan journal SQLite scoped.
Managed candidate `d016e5a4de82144eec0a007e8160ac282f0935a3`: cappuccino,
quantity/total interaktif dan hapus row pada nol sesuai jawaban operator.
12/12 target node tests dan build nyata lulus; acceptance runner terpisah 4/4
mandatory browser tests lulus. Seeded bug mengabaikan quantity: 2 pass/2 fail.
Pengguna mencoba artefak target `8ae0c116…` dan mengonfirmasi semuanya berjalan.

Start/stream, satu klarifikasi/duplicate answer, checkpoint restart, stale fences,
actual call/tool/duration caps serta stop process group/container dengan child
Node diuji. Satu candidate record; accepted ref proyek fitur tetap base.
Rebuild SHA sama, config dan base berbeda memperoleh target/evidence baru.
Canary technical-lead memakai home/context/allowlist berbeda; memory/auxiliary
yang tidak digunakan disabled. Keputusan: pakai Hermes embed; final contract
enam operasi di `docs/decisions/runtime.md`, product DB/job wiring tetap DEV-010.

Scope fitur: 37 model requests / 66 tools / 322.39 detik aktif. Batas 32/80
tercapai; pengguna eksplisit menambah 16/40, menjadi 48/120 tanpa reset usage.
Semua scope/probes 47 requests/73 tools; 41 receipts melaporkan USD 0 dan 6
receipts 429 unknown, sehingga total biaya unknown. Cohere gratis dicoba tetapi
gagal menggunakan path relatif; Qwen kembali dipakai atas jawaban pengguna.
Checkpoint pertama dipromosikan supervisor untuk QA setelah 429; Hermes
generation 9 menjalankan gates dan final handoff idempotent kandidat sama.

Verifikasi akhir WSL: `python -m unittest discover -s tests/runtime_spike -v`:
35 passed (sebelum review kode; 39 setelahnya, lihat di bawah). DEV-005 full suite setup WSL sebelumnya 103 passed/0 skipped tetap
berlaku (workspace code tidak berubah). Rebuild acceptance pertama incomplete
akibat race container; operation lock/join cleanup diperbaiki dan invocation baru
lulus. Raw evidence lama dipertahankan, bukan diganti menjadi pass.

File hasil: `apps/backend/app/runtime_spike/**`, `tests/runtime_spike/**`,
`contracts/dev006/**`, `.env.example`, README/backlog, docs decision/spike dan
`docs/spikes/DEV-006-results.json`. Evidence lokal: `data/dev006/evidence/`,
review diff tracked+untracked `data/dev006/review.patch`, full journal/build/source
di `/root/aiagent-dev006/experiment`. Pemetaan semua tujuh AC dan reproduksi:
`docs/spikes/DEV-006.md`. Known limits: POSIX-only standalone harness, checkpoint
fallback bukan native continuation/arbitrary mid-tool exactly-once, free quota
tidak dijamin. R3 berikutnya; tidak mulai tiket lain atau commit/push/deploy.

Review kode Claude (2026-10-05; self-check pendukung, bukan independent R3).
Bug diperbaiki di harness, tanpa mengubah hasil eksperimen yang sudah tercatat:
(1) relay memetakan error tool biasa (KeyError argumen hilang, UnicodeDecodeError
`read_file`) ke 409, yang membuat worker meng-interrupt seluruh run; kini 400
`tool_error`, hanya AdmissionError tetap 409. `read_file` decode `errors=replace`.
(2) Kegagalan stream setelah header terkirim menulis respons HTTP kedua; kini
koneksi ditutup saja. Hash hasil tool dihitung dari output yang benar-benar dikirim.
(3) `stop` memakai generation session.json; setelah crash antara answer dan save,
stop gagal stale dan tidak me-revoke. Kini memakai generation journal.
(4) `restart` menjalankan gates dan checkpoint commit sebelum memvalidasi status;
restart yang ditolak bisa menggeser attempt ref. Validasi kini lebih dulu.
(5) Kegagalan transport (status failed) me-revoke tanpa `stop_run`; kini cleanup
container/arsip ikut dijalankan. (6) Preview `HEAD` melewati cek Host/credential.
(7) `assert` invariant identitas pada verify hilang di `python -O`; kini eksplisit.
Empat regression test baru gagal pada kode lama dan lulus sesudahnya. WSL
`unittest discover -s tests/runtime_spike`: **39 passed**; Windows preflight 13
passed/1 skip. Eksperimen nyata tidak diulang; perbaikan menyentuh jalur error.

Pengecekan lanjutan Codex (2026-10-05; self-check, bukan independent R3):
diff perbaikan diperiksa dan 39 tes WSL/13 Windows + 1 skip dikonfirmasi.
Masih ada race cleanup transport: reader bisa menandai failed dan worker keluar
sebelum loop pemantauan berjalan, sehingga flag revoked tidak terpasang dan
`stop_run` terlewati. Kini status failed setelah join reader juga me-revoke dan
menjalankan cleanup. Regression test dengan Git/SQLite nyata serta process/
transport doubles gagal sebelum perbaikan (`stop_run` 0 calls) dan lulus sesudahnya.
Suite WSL akhir **40 passed**; README dan laporan spike diselaraskan. Bukti
eksperimen/model/acceptance terdahulu dipertahankan, tidak dijalankan ulang.
Smoke check `PYTHONPATH=. python -O ../../data/dev006/check-review.py`:
preview HEAD valid 200, Host salah/Authorization/Cookie 403, query 400;
invariant identitas tetap menolak input salah pada interpreter optimized.
`git diff --check` dan secret scan handoff lulus; patch handoff diperbarui.
Perubahan review tetap unstaged; tidak ada commit/push.

Finalisasi 2026-10-05: pengguna mengonfirmasi review Claude sudah oke dan
menginstruksikan commit/push. R3 dicatat REVIEWED berdasarkan konfirmasi itu;
self-check Codex tetap dicatat terpisah. Status saat sesi sebelumnya belum
commit/push adalah catatan historis. Perubahan DEV-006 beserta perbaikan review
digabung dalam commit pada branch master, lalu push ke origin sesuai instruksi.

### Riwayat persiapan (sebelum eksperimen nyata)

Pelaksana/sesi: Codex, sesi Windows 2026-10-04
Rencana singkat: periksa API/versi Hermes resmi, siapkan preflight reproducible
dan rencana eksperimen dengan evidence per AC. Gunakan broker/sandbox DEV-005;
jangan membuat adapter dari asumsi atau mengganti bukti nyata dengan fake.
File hasil: `apps/backend/app/runtime_spike/{__init__,preflight}.py`,
`hermes-pin.json`, `apps/backend/tests/runtime_spike/**`, `.env.example`,
`README.md`, `docs/decisions/runtime.md`, `docs/spikes/DEV-006.md`,
`docs/spikes/hermes-source-inspection.json`.
Verifikasi aktual:
- Python 3.12.10/Git 2.46.0.windows.1; backend venv dan requirements.lock
  berhasil diinstal. `python -m unittest tests.runtime_spike.test_preflight -v`
  dari backend: **11 passed** (test doubles untuk preflight, bukan model/runtime).
- `python -m app.runtime_spike.preflight --env-file ../../.env.local
  --docker-bin <Docker Desktop docker.exe> --report ../../data/dev006/preflight.json`:
  report **blocked**, provider/model/key ready; host/engine/image/runtime belum ready.
- `git diff --check`, compile package/test runtime_spike, dan check-ignore
  config secret/data: lulus. Pemeriksaan secret pada tracked/untracked diff: bersih.
- Atas instruksi pengguna, Docker Desktop **4.93.0** dan Microsoft.WSL
  **2.7.13** berhasil diinstal. CLI Docker **29.8.1** tersedia; engine HTTP 500.
  `wsl --status`/install menyatakan perubahan perlu reboot. Tidak reboot otomatis.
- Pengguna memilih OpenRouter. Config lokal gitignored; GET `/api/v1/key`
  authenticated HTTP **200**, **0 model calls**. Model awal
  `qwen/qwen3.8-27b:free` diverifikasi ada/tools di katalog; inference belum diuji.
- Hermes release `v2026.9.24`, commit `f97608f178d1ffeca59860195ab7da295f7c8e5f`,
  package `0.21.5`: pemeriksaan source statis saja; tidak diklaim installed/executed.
Evidence/keputusan: `docs/decisions/runtime.md` berstatus PENDING,
`docs/spikes/DEV-006.md` memuat pemetaan tujuh AC, rencana nyata/caps dan handoff.
Report tersanitasi serta diff tracked+file baru: `data/dev006/` (gitignored).
Blocker sesi awal (teratasi pada recheck di bawah): restart Windows untuk aktivasi WSL/Virtual Machine
Platform, lalu distro/engine Linux dan Hermes. Broker bridge, accounting/journal
persisten, fitur nyata/commit/tests, acceptance runner/seeded bug, operator UAT,
stream/stop/restart/klarifikasi dan isolasi harus diimplementasikan/dibuktikan
sesudahnya. Provider tersedia; fake/preflight tidak menutup DEV-006.
Handoff: lanjut eksperimen nyata DEV-006 mengikuti `docs/spikes/DEV-006.md`;
DEV-010 masih menunggu tiket ini. Tidak mulai tiket lain atau commit/push/deploy.
Review: NOT_REVIEWED — R3 penuh belum siap tanpa bukti eksperimen nyata.

Recheck setelah pengguna restart (sesi lanjutan 2026-10-04): WSL2 aktif,
`docker version` merespons client/server 29.8.1 dan engine `linux/amd64`,
`docker info --format '{{.OSType}}'` = `linux`. Blocker reboot sudah teratasi.
Ubuntu 26.04.1 dan integrasi Docker dipasang. Python 3.13.16 (default Ubuntu
3.14.4 tidak cocok) serta backend/Hermes venv terpisah tersedia di filesystem
Linux. Source Hermes terpin di `/root/aiagent-dev006/hermes-source`, import
`AIAgent` 0.21.5 berhasil. Preflight WSL exit 0, **10/10 prerequisites ready**.
Bug symlink interpreter venv pada preflight diperbaiki (`absolute`, bukan
`resolve`); **12 unittest passed** di WSL. Suite workspace aktual **103 passed,
0 skipped**, 160.60 detik, dengan sandbox dan React/Vite install/build/test/smoke.
Evidence: `data/dev006/{preflight-wsl.json,workspace-wsl.xml,workspace-wsl-tests.log}`.
Scope recheck ini tidak memanggil model; seluruh eksperimen/AC yang belum
terbukti tetap pending dan tiket masih IN_PROGRESS. Reboot bukan blocker lagi.

Review lanjutan (Claude, 2026-10-04, review kode persiapan; bukan R3 penuh):
dua bug preflight diperbaiki. (1) Check `python` memakai interpreter backend,
bukan venv Hermes; rentang pin `>=3.11,<3.14` kini divalidasi pada interpreter
`--hermes-python` (bagian `hermes_install`, report `hermes_python`), backend cukup
3.11+. (2) Probe membuang `DOCKER_HOST`/`DOCKER_CONTEXT`/`DOCKER_CONFIG`/TLS/
`XDG_RUNTIME_DIR`, sehingga bisa memeriksa engine berbeda dari yang dipakai
sandbox DEV-005; selector itu kini diteruskan (secret provider tetap dibuang).
Regression test ditambah: WSL **14 passed**, Windows **13 passed, 1 skipped**
(symlink). Preflight WSL ulang: exit 0, 10/10 ready, `hermes_python` 3.13.16.
Status tetap IN_PROGRESS; eksperimen model belum dijalankan.

Sesi eksperimen (Codex, 2026-10-04, instruksi pengguna "gas"):
rencana: embed API `AIAgent` Hermes terpin dalam subprocess/home terisolasi,
tools registry khusus yang hanya memanggil supervisor, relay provider terukur,
journal SQLite standalone per scope, lalu kandidat fitur cappuccino/keranjang,
runner Playwright independen dan seeded bug. File utama pada `runtime_spike/`,
test terfokus, suite `contracts/dev006/`, dan report di `docs/spikes/DEV-006.md`.
Transport gateway belum dipilih final; source embed memberi batas tools eksplisit.
Approval scope eksperimen berasal dari instruksi pengguna, bukan approval produk.

**Tujuan:** membuktikan executor bisa mengerjakan satu perubahan nyata sebelum
investasi GUI penuh.

**Scope:** coba Hermes dengan provider gratis/murah yang tersedia; verifikasi
versi, API/CLI resmi, tool support, dan isolasi context. Adapter aplikasi mengikuti
kemampuan yang dibuktikan, bukan nama method yang diasumsikan ada di Hermes.
Gunakan harness standalone DEV-005; tidak menunggu board, DB domain lengkap,
atau scheduler production. Catat scope/persetujuan operator eksperimen sebagai
input; jangan mengklaim seluruh workflow produk sudah terimplementasi.

**Acceptance criteria:**

- Satu tugas nyata menghasilkan diff/commit dan test yang benar-benar dijalankan;
  catat model ID, runtime version, konfigurasi, tool calls, durasi, dan biaya/usage
  yang tersedia. Jika provider tidak melaporkan biaya, tulis tidak tersedia.
- Fitur vertikal kecil (contoh item menu/total keranjang) dicoba operator dari
  artefak yang memperoleh evidence. Acceptance runner terpisah menangkap bug
  total sengaja ditanam; manifest target dan suite/results dicatat.
- Start, output streaming, stop, dan behavior setelah restart diuji; kemampuan
  lanjut sesi atau input aktif dicatat apa adanya, beserta fallback bila perlu.
- Satu klarifikasi dan restart tidak menggandakan pekerjaan; accounting model
  calls/tools serta finite caps terbukti, bukan hanya repair counter. Perubahan
  base atau build/config menghasilkan target/evidence baru tanpa approval lama.
- SOUL/context runtime terisolasi per project/role/attempt; memory tambahan yang
  tidak dipakai dinonaktifkan dan scheduler ganda tidak dijalankan.
- Simpan keputusan di `docs/decisions/runtime.md`: dipakai/tidak, bukti, batasan,
  serta kontrak adapter final. Hanya satu runtime diimplementasikan untuk MVP.
- Ketiadaan key/provider dicatat `BLOCKED`; fake tests tidak menutup tiket ini.

**Verifikasi:** sertakan log tersanitasi, artifact test, candidate SHA, stop test,
dan hasil recovery. Jangan memasukkan key atau kredensial ke dokumentasi.

### Perbaikan berdasarkan perbandingan dua demo — 2026-10-06

Status perubahan: DONE (implementasi 2026-10-07). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: implementasikan prioritas audit two-demo-comparison-2026-10-06.md.
Rencana: diagnosis selector dari runner tepercaya + repair suite/target tanpa
mutasi aplikasi; retry request provider dengan accounting/lease; policy demo
persisten; projection working context; prefetch reference dependency terverifikasi.
File terkait: contracts/verification, pipeline/runtime/scheduler/transcript/setup,
runtime_spike/relay, config/http, agents/qa, keputusan agents/pipeline.
Verifikasi: pemeriksaan statis dan konsistensi kontrak. Tidak menambah/menjalankan
tes kecuali diminta pengguna; layanan/demo tidak dijalankan otomatis.
Hasil: runner memberikan diagnosis strict selector; QA hanya memperbaiki
visibility selector dengan satu alert terlihat/ID unik, membuat suite+target
baru dan mengantrekan QA ulang pada build sama. Semua IDs/UAC/actions/values
dipertahankan; kasus ambigu lain gagal eksplisit di QA. Legacy failed keys tidak
memperoleh retry hanya karena key scheduler berubah. Infra incomplete memakai
retry job; kategori/evidence failed outcome dipersist dan ditampilkan di UI.
Relay/structured request mencoba gateway transient dengan jeda 5/15/30,
accounting baru setiap request, cancel/lease checks, tanpa replay partial stream
atau tool. Source mutation menyimpan checkpoint tanpa mengubah Git refs, latest
checkpoint dipin, log snapshot lama tidak menahan semua salinan source selamanya.
Projection mempertahankan read working set 16000 karakter di luar enam messages
terbaru, digest/range menggantikan observasi/argument mutasi lama.
Policy demo persist saat create dan diwariskan chat/pipeline/reply/setup/
onboarding/release/prefetch; .env.example=0, .env.local ignored=1. Dua demo idle
lama juga diberi policy sesuai izin unlimited sebelumnya, usage/histori tetap.
Reference prefetch terpisah mengisi SHA512 cache tanpa npm/kode host; failure
warming tidak memblokir proyek, directory/recovery memakai owner durable.
Verifikasi akhir: AST 26 file Python, import 23 modul, TypeScript --noEmit,
git diff --check lulus; 37 file perubahan diperiksa tanpa credential/runtime data.
Tes regresi/browser/provider
tidak ditambahkan/dijalankan; BE/FE/worker tetap berhenti. Known limits dan
skenario regresi: docs/decisions/pipeline.md bagian perbaikan dua demo. Perbaikan
selector otomatis masih konservatif; outage setelah retry tetap needs_human,
prefetch race bisa mengunduh miss sama, benefit checkpoint/projection belum
diukur pada demo ulang. Implementasi belum commit/push atau review independen.

### Informasi dependency pada board — 2026-10-06

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: tampilkan alasan tiket Siap belum dikerjakan, cek unduhan QA.
Rencana: DTO board/detail menampilkan dependency belum terpenuhi dari DB;
kartu/detail memberi alasan dan tautan upstream. Scheduler/approval tetap sama.
Hasil: board/detail memakai dependency_waits dari DB, notice membedakan menunggu
acceptance, integrasi, revalidasi dan upstream dibatalkan; tautan membuka tiket
upstream. File: http/queries.py, contracts/api/types.ts, DependencyNotice.tsx,
Board.tsx, Ticket.tsx, style.css. Verifikasi: AST/import queries, TypeScript
--noEmit, git diff --check lulus; API health ok setelah restart backend.
Tes regresi/browser tidak ditambahkan/dijalankan; pengguna belum meminta tes.
Observasi demo test 2: QA-plan mencatat 115 cache hits/115 paket, downloaded_bytes
0. Dependency #2 sudah satisfied setelah #1 accepted; notice memang kosong.
Developer #2 ternyata berhenti karena pool pipeline default 200000 total_tokens.
Sesuai izin unlimited demo sebelumnya, project pipeline budget_limits dan peer
caps diubah ke null tanpa reset usage/histori. Archive run-6e25f57a69c8 diverifikasi
checksum/provenance/base; 7 file dipulihkan ke checkpoint persisten, kemudian
extend_budget membuat retry 4e527f9cfcd84a1697eb373547850aab. Status running,
checkpoint dibawa dan unlimited_budgets true. Default proyek lain tetap finite.

### Optimasi runtime berdasarkan audit demo catatan — 2026-10-06

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: implementasikan temuan audit yang disetujui pengguna.
Hasil: write_file/create/replace/delete dan edit_file exact-match memakai CAS digest
pada operation/state lock; read/diff dipaginasi, submit message maxLength 2000.
Relay developer memproyeksikan completed tool exchanges usang ke digest/metadata,
mempertahankan source terbaru, scope/feedback/decisions/user/system dan transcript
asli. Canary original tetap diperiksa sebelum projection. Review TL merangkum
lock graph/flags/hash dan gate, mengarsipkan full diff dengan ID stabil saat
resume; summary yang tidak membantu atau format lock custom memakai diff asli.
Output schema duplikat dihapus. Cache tarball supervisor memverifikasi SHA-512
setiap hit, penulisan atomik/ownership/NOFOLLOW/lock/eviction; target tetap offline.
Metric projection/review/progress/tool dan cached_tokens ditambahkan.
File hasil: pipeline/source_tools.py, transcript.py, review_context.py;
workspace/dependency_cache.py; wiring runtime/relay/Hermes/supervisor/sandbox/Git,
agent facade/runtime/instructions; keputusan agents/pipeline dan laporan audit.
Verifikasi aktual: AST 16 file Python, import modul, git diff --check lulus.
Tidak menambah/menjalankan tes regresi/model berbayar/rebuild/demo baru. Worker
idle dihentikan tertib dan direstart memakai perubahan; BE/FE tetap berjalan.
Batas verifikasi: correctness CAS/paging/projection/cache concurrency/eviction
belum melalui tes regresi; penghematan token/waktu belum diukur pada demo ulang.
Kompatibilitas tool lama dipertahankan hanya untuk fake foundation driver.
Handoff: docs/decisions/pipeline.md bagian optimasi; review independen belum
tersedia, perubahan belum commit/push.

### Audit efisiensi demo catatan — 2026-10-06

Status audit: DONE (audit saja). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: audit token, panggilan developer, konteks, tool dan waktu demo catatan.
Hasil: docs/audits/notes-demo-efficiency-2026-10-06.md; rincian 46 calls developer,
rekonsiliasi usage Job dengan relay, 33 calls dengan transcript, pertumbuhan
context 7967→40670 input/call, full-file replacement yang dipakai sebagai partial
edit, kegagalan tool transport, 74,2% review diff lockfile, tool wait dominan.
Sumber: usage DB, context snapshots, conversation retry, job relay logs dan
command evidence. Tiga failed TL calls unknown, transcript attempt developer
awal tidak tersedia; tidak menganggap angka runtime final sebagai total relay.
Verifikasi: laporan dihitung dari evidence lokal dan git diff --check; tidak
menjalankan tes/model berbayar/rebuild. Runtime tidak diubah. Sisa: implementasi
optimasi tool/context/cache dan pengukuran ulang belum dikerjakan.

## DEV-007 — Soul, context, model client, dan pesan antar-agent

### Fallback model OpenRouter eksplisit — 7 Oktober 2026

Status perubahan: `DONE` (implementasi; review independen belum dilakukan). Permintaan pengguna: DeepSeek tetap utama;
GLM 5.3 Flash hanya cadangan saat provider/model utama gagal. Rencana: konfigurasi
`fallback_models` per role, routing yang sama untuk structured client dan relay
Hermes, catat model respons aktual serta alasan quota, lalu restart worker.
File relevan: `agents/models.example.json`, `app/agents/models.py`,
`app/runtime_spike/relay.py`, `app/pipeline/{hermes,relay}.py` dan keputusan agents.
Tidak mengubah approval, histori usage, atau budget demo.

Hasil: `fallback_models`/`allow_provider_fallbacks` divalidasi per role;
structured client dan relay mengirim daftar OpenRouter yang dipilih supervisor.
Relay menolak model utama berbeda dan menghapus rute cadangan dari input runtime.
Model respons nyata tercatat dalam log/metadata. Accounting tetap satu reservasi
per request aplikasi, dan setiap retry HTTP punya reservasi sendiri; final error
retry mengikat reservasi terakhir agar detail/Retry-After tidak hilang.
Helper bersama: `apps/backend/app/provider_routing.py`.

Verifikasi aktual: AST parse kelima file Python valid; impor model registry,
relay/Hermes/ProductAdmission dan validasi registry lokal berhasil;
`agents/models.example.json` valid JSON; `git diff --check` bersih. Worker lama
berhenti tertib, job dikembalikan ke antrean, worker baru PID 34962 aktif.
Job Developer tiket #2 generation 25 memakai primary DeepSeek dan fallback GLM;
dua respons provider nyata `complete` tercatat sebagai DeepSeek sekitar 14.50 WIB.
Tes regresi tidak ditambahkan/dijalankan karena pengguna meminta implementasi
dan aktivasi konfigurasi, bukan menjalankan tes. Failover 429 ke GLM belum diamati
di run baru; native routing mengikuti kontrak resmi OpenRouter, bukan klaim hasil
simulasi. Fallback tidak menjamin pulih dari putus koneksi lokal atau limit akun.

### Output tanpa cap aplikasi untuk demo — 2026-10-06

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: hapus cap output aplikasi pada semua role demo sesuai instruksi user.
Hasil: max_output_tokens=null memakai maksimum provider dari metadata resmi
OpenRouter, cache 5 menit, pada structured client dan Hermes; budget finite
proyek lain tetap dihormati. Tidak ada ceiling registry 32768 yang arbitrer.
File: agents/models.py, pipeline/hermes.py, models.example.json, docs/decisions/agents.md;
konfigurasi lokal semua 4 role null (gitignored). Metadata yang tidak tersedia
menjadi error eksplisit; adapter provider selain OpenRouter belum mendukung null.
Verifikasi aktual: sintaks/import/registry dan git diff --check; metadata publik
mengonfirmasi maksimum provider PO/QA 65536 dan TL/developer 943718. Demo pool
semua limit null/unlimited_budgets=true; tiket tetap UAT dan tidak ada job aktif.
Tidak menambah/menjalankan tes regresi atau panggilan model berbayar untuk perubahan ini.
Timeout/transport/lease/approval tetap berlaku. Review independen belum dilakukan.

### Streaming dan diagnosis respons provider — 2026-10-06

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: diagnosis error TL `provider returned a non-text answer` dan gunakan
streaming. Rencana: SSE Chat Completions terkonfigurasi per role, progress tanpa
isi reasoning/secrets, diagnosis finish reason/type/usage, accounting respons
gagal bila tersedia, serta retry review kandidat yang sama setelah fix.
File: agents/models, konfigurasi contoh/lokal, docs keputusan dan log.
Tidak menyimpan raw respons provider atau menerima reasoning sebagai verdict.
Hasil: role memiliki opsi stream dan reasoning_effort tervalidasi; SSE dibatasi
bytes/deadline, menerima comments/multi-line/usage terminal, memerlukan DONE dan
finish reason, serta menolak length/tool_calls/filter/error/jawaban kosong.
Content parts teks dinormalisasi. Usage gagal yang tersedia tetap diakumulasi;
usage tidak tersedia tetap unknown. Progress hanya counts, tanpa raw reasoning
atau fragmen teks/secret. Struktur JSON/izin/lease/approval tetap ditegakkan.
Verifikasi aktual: sintaks/import dan parse model registry lulus; `git diff --check`
lulus. Tidak menambah/menjalankan tes regresi. Percobaan provider nyata atas
kandidat demo yang sama: job `bebb339bb86641b1b28d712dbfb2b8fa` menjelaskan
error lama — finish_reason=length, answer_chars=0, completion_tokens=4096;
usage/cost kali ini tercatat. API metadata resmi model DeepSeek v4.1 Flash
mengonfirmasi default reasoning high, supported max/high/low. Local TL config
(gitignored) diubah stream=true/max_output_tokens=8192/reasoning_effort=low;
contoh tracked mengaktifkan stream dan cap 8192 tanpa mengasumsikan effort cocok
untuk model contoh lain. Worker direstart; retry resmi
`d278ed5bffb341b28cdee3639a4ebe6f` accepted dalam **23,9 detik**, finish stop,
answer_chars=2526, output_tokens=6012. Kandidat tetap
`4f16cedabc2a479f82e2e83984d8fc23`; tiket kemudian **UAT** melalui pipeline.
Keterbatasan: SSE interruption/cancel/error/malformed/byte bound dan kompatibilitas
provider lain belum melalui tes regresi. Effort low dan cap lebih besar sama-sama
berubah; keberhasilan tidak mengukur pengaruh streaming sendiri. Progress hanya
di log, tidak menampilkan token reasoning/jawaban parsial sebagai verdict. Belum
commit/push; review independen belum dilakukan.

### Penyempurnaan empat peran — 2026-10-06

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment pengguna: perbarui seluruh soul setelah diskusi tentang tiket terlalu
kecil dan putaran kerja berulang. Rencana: selaraskan empat `SOUL.md` dan
`instructions.md` agar PO menggabungkan satu alur pengguna, lead memberi rencana
proporsional dan feedback konkret, developer menjaga scope serta memakai feedback
repair terbaru, dan QA menguji perilaku tanpa menambah persyaratan DOM.
File: `agents/{po,technical-lead,developer,qa}/{SOUL,instructions}.md`,
`docs/decisions/agents.md`, dan log ini. Perubahan adalah kebijakan prompt;
tidak mengganti scheduler, kontrak output, approval, atau bukti QA independen.
Hasil: PO memilih sedikit tiket dengan batas scope yang jelas dan alasan split
di field kontrak existing; contoh daftar belanja menjadi satu tiket dengan empat
perilaku. Lead membedakan blocker dan saran, developer memakai feedback terbaru
dan menjaga scope, QA memilih assertion perilaku serta membedakan defect aplikasi,
suite, dan runner. Instruksi teknis rutin tidak lagi meminta klarifikasi otomatis.
Verifikasi aktual: dari `apps/backend`, `.venv/bin/python -c` memanggil
`app.agents.souls.load_agents()` — **4 peran berhasil dimuat**, UTF-8/ukuran/secret
guard loader lulus dan digest terbentuk; seluruh file di bawah batas 16 KiB.
`git diff --check` — **lulus**. Konsistensi role, field output, tools, approval,
dan mandatory QA ditinjau melalui self-check diff, bukan review independen.
Tidak menambah/menjalankan tes atau inference provider karena assignment tidak
meminta tes. Perubahan prompt belum membuktikan jumlah tiket keluaran model nyata
atau pengurangan durasi. Worker memuat definisi saat runtime dibuat; perlu start
baru agar memakai versi ini. Snapshot lama dan tiket disetujui tetap histori.
Layanan tetap dihentikan. Handoff: diff delapan file peran, dokumen keputusan,
dan log ini; evaluasi berikutnya perlu brief kecil/besar, scope repair, serta
kegagalan selector/runner dengan provider nyata. Belum commit/push pada assignment ini.

### Review Claude Batch 3 DEV-007/008/009 — 2026-10-06

Status perbaikan: DONE (implementasi). Pelaksana: Codex. Review perbaikan: NOT_REVIEWED.
Tujuh temuan masih berlaku pada head awal `b3ac9c9` dan diperbaiki:

1. `Threads.ensure_reply_jobs` memakai satu query kandidat pending dengan join
   origin, NOT EXISTS reply, status waiting untuk input request, serta filter
   needs_reply/category/penerima/scope. Pesan lama yang sudah punya reply tidak
   dimaterialisasi dan tidak lagi menghasilkan query tambahan per pesan per tick.
   Enqueue tetap memeriksa ulang eligibility dalam transaksi tulis dan memakai
   idempotency key asli; tidak mengubah histori atau membangkitkan scope stale.
3. `ContextBuilder._history` menyaring proyek, tiket (termasuk NULL untuk konteks
   proyek), runtime log dan kind/intent langsung di SQL. Ringkasan tetap diambil
   dan diverifikasi dengan digest sumber; urutan dan batas konteks dipertahankan.
   Filter log bersama dipindah ke persistence, digunakan API dan context builder.
4. Append chat tidak memakai revision proyek. Schema menerima expected_revision
   opsional untuk klien lama tetapi mengabaikannya; Chat/AskPo tidak mengirimnya.
   Receipt idempotent atomik, validasi proyek/tiket/task, auth dan CSRF tetap berlaku.
5. SSE CLOSED atau parse/handler gagal menutup stream lama, memeriksa sesi,
   mengambil snapshot dan membuka stream dari cursor snapshot. 401 memicu login;
   kegagalan sementara di-retry dengan delay 1–10 detik. Stop membatalkan timer;
   callback stream lama dan recovery selesai setelah stop diabaikan.
6. UAT memilih `candidate.preview.verification_id` dengan status passed dan digest
   target yang cocok, bukan urutan verification array. Pin yang tidak tersedia
   tidak diganti dengan bukti lain; backend tetap memvalidasi approval/evidence.
7. Retry-After numerik harus finite; nilai invalid/nonfinite memakai 30 detik,
   kemudian di-clamp 1–3600 detik sebelum ProviderLimiter menerima nilai tersebut.
8. Refresh membuang hasil yang lebih lama dari respons yang sudah diterapkan,
   bukan semua yang lebih lama dari permintaan terbaru. Epoch proyek dan pilihan
   tiket dicek agar respons dari proyek/tiket lama tidak menimpa detail baru.

Temuan extend budget ganda sudah ditutup Batch 1 (`2c2f497`): parent yang punya
retry ditolak sebelum cap diubah; idempotency key yang sama mengembalikan retry
yang sama. API memakai jalur queue tersebut sehingga tidak memerlukan fix duplikat.
File hasil: `apps/backend/app/agents/{threads,context,models}.py`,
`apps/backend/app/persistence/messages.py`, `apps/backend/app/http/{application,
schemas,queries}.py`, `apps/web/src/api/client.ts`, `apps/web/src/workspace.tsx`,
`apps/web/src/components/{Chat,Ticket}.tsx`, `contracts/api/{openapi.json,requests.ts}`,
`docs/decisions/{agents,api,gui}.md`, dan log ini.
Verifikasi aktual:
- `apps/backend/.venv/bin/python` dengan `ast.parse` — **7 file Python OK**.
- Dari `apps/backend`: `.venv/bin/python -m app.http.contract` — **exit 0**;
  kontrak OpenAPI/TypeScript diregenerasi tanpa menjalankan server/lifespan DB.
- `node_modules/.bin/tsc -p apps/web/tsconfig.json --noEmit` — **exit 0**.
- `git diff --check` — **lulus**; self-check diff, bukan review independen.
Tes regresi tidak ditambah/dijalankan karena permintaan lanjutan tidak secara
eksplisit meminta tes. Tidak ada benchmark riwayat besar, browser reconnect/401,
uji concurrency, atau inference provider pada sesi ini; aplikasi/worker tetap mati.
Handoff: diff tracked file di atas. Regresi berikutnya perlu memeriksa query count
riwayat besar, pemulihan reply setelah crash/cancel/revisi, chat saat revision proyek
berubah dan retry identik, SSE CLOSED/401/parse/snapshot gagal/stop, UAT dua receipt
passed dengan urutan acak, Retry-After nan/inf/negatif/besar, serta refresh yang
overlap terus dan pergantian proyek/tiket. Tidak menutup checkpoint R5 dari self-check.

### Catatan pengerjaan
Status: DONE (fake/contract checks; F1–F7 review diperbaiki Codex, 2026-10-05)
Pelaksana/sesi: Claude (Sonnet 5.5), sesi Windows + WSL 2026-10-05
Rencana: SOUL + instruksi per peran, tools per peran dengan otorisasi dari identitas run, model client per
role (fake berlabel + adapter chat-completions), kontrak output terstruktur PO/lead, context builder berlapis
dengan batas token dan snapshot/hash, thread dan input request antar-agent, runtime terstruktur di atas
Supervisor DEV-004. Dependency DEV-002 dan DEV-004 DONE/REVIEWED.

File hasil: `agents/{po,technical-lead,developer,qa}/{SOUL,instructions}.md`, `agents/models.example.json`,
`apps/backend/app/agents/{__init__,redaction,souls,outputs,models,threads,tools,context,runtime,wiring}.py`,
`apps/backend/tests/agents/{__init__,conftest,test_souls_outputs,test_models,test_tools,test_context,
test_threads,test_runtime,test_wiring}.py`; perubahan kecil kompatibel: `app/workers/queue.py` (label `:fake`,
`recipient` pada `request_input`, `verify`, `set_context`), `app/workers/supervisor.py` (`maintenance` hook),
`app/domain/service.py` (`idempotency_key` pada `create_ticket`/`propose_scope`), `app/worker.py`
(`--runtime structured`), `.env.example`, `.gitignore` (`agents/models.json`), `README.md`,
`docs/decisions/agents.md`.

Pemetaan AC ke bukti (`apps/backend/tests/agents/`):
- AC1 konteks berlapis + batas token + snapshot/hash per run: `test_context` (urutan layer, snapshot artifact
  ber-hash di job dan dipin, pemangkasan pesan terlama + gaps, ContextTooLarge, isolasi proyek, secret, rebuild
  setelah restart, stabilitas prefix, referensi repo/pesan panjang); `test_runtime` (konteks == yang dilihat model).
- AC2 ringkasan tidak menghapus histori, proposal bukan keputusan, transkrip tidak diduplikasi:
  `test_a_valid_summary_stands_in_for_trimmed_messages_without_a_gap`, `test_a_summary_that_no_longer_matches...`,
  `test_only_accepted_decisions_enter_project_knowledge`, `test_proposals_in_the_ticket_thread_are_labelled...`,
  gap `runtime_transcript` pada manifest.
- AC3 proposal PO tervalidasi, keluaran lead terstruktur, tools sesuai role: `test_souls_outputs` (kontrak,
  siklus, duplikat, extra field), `test_runtime` (breakdown, revisi, rencana lead), `test_tools` (matriks role,
  tanpa tool approval, NotWired eksplisit).
- AC4 pesan dev->lead tersimpan, jawaban terarah kembali ke attempt valid, broadcast/log tidak memicu:
  `test_a_directed_question_is_persisted_before_its_reply_job_exists`, `test_notes_logs_and_broadcasts_never_wake_a_soul`,
  `test_developer_waits_lead_answers_and_only_the_valid_attempt_resumes`, `test_a_reply_cannot_trigger_further_work`.
- AC5 input request ber-ID/scope/penerima/attempt/generation/status, jawaban idempotent, duplicate/setelah
  cancel/setelah revisi scope: `test_the_persisted_request_shows...`, `test_duplicate_answers_resume_exactly_once`,
  `test_an_answer_after_cancel_stays_history...`, `test_scope_revision_cancels_the_waiting_attempt...`,
  `test_waiting_survives_a_worker_restart`, `test_a_crash_between_the_question_and_its_reply_job_is_reconciled`,
  `test_clarification_waits_for_the_user_then_resumes_with_the_answer`.
- AC6 model per role, timeout, output invalid, usage/cost, redaction, klarifikasi terlihat: `test_models`
  (konfigurasi per role, usage unknown bukan nol, panggilan gagal tetap terhitung, adapter HTTP terhadap stub),
  `test_runtime` (timeout retry satu kali, request ditolak, quota -> waiting_quota, output invalid -> repair
  lalu gagal terlihat, secret tidak ke pesan/artifact/hasil, budget menghentikan repair call).
- AC7 fake berlabel, tanpa key tidak memblokir, resume dari persistence: `test_a_fake_provider_under_a_real_label...`,
  `test_wiring`, `test_resume_rebuilds_from_persistence_after_a_restart`; semua event/hasil job membawa `fake=true`.

Verifikasi aktual (dari `apps/backend`):
- WSL suite backend lengkap `/root/aiagent-dev002-venv/bin/python -m pytest -q`: **583 passed**, 222 detik (Docker
  nyata, tanpa skip). Windows `.venv/Scripts/python.exe -m pytest tests/agents tests/domain tests/persistence
  tests/workers -q`: **432 passed, 8 skipped** (symlink + test process group POSIX). Domain baru: **130 tests**
  (context 18, models 24, runtime 21, souls/outputs 23, threads 20, tools 20, wiring 4).
- Stabilitas: suite agents dijalankan berulang (15x berturut-turut, 15/15 lulus) setelah memperbaiki lima
  sumber flaky: empat race di test (reply job yang jalan lebih cepat dari asersi) dan satu bug produk (urutan pesan,
  lihat Temuan).
- Mutation check (11 mutasi, dipulihkan setelahnya), semuanya tertangkap: policy tool tidak ditegakkan, identitas dari
  argumen, scope belum disetujui diterima, semua proposal keputusan dianggap accepted, tanpa verifikasi lease
  sebelum menyimpan hasil, pembuatan tiket tidak idempotent, output provider tidak diredaksi, siapa pun boleh
  menjawab request peran, reply memicu kerja lagi, label fake tidak dicek, urutan pesan mengabaikan seq.
- CLI di WSL: `python -m app.worker --runtime structured` start, mencetak peringatan UNVERIFIED, berhenti tertib
  (SIGTERM, exit 0). Catatan: `app.config` memuat `.env.local`, jadi key di sana ikut terbaca.

Temuan saat pengerjaan (diperbaiki): urutan pesan memakai `created_at` lalu ID acak sehingga timestamp kembar
(resolusi jam Windows) bisa mengacak percakapan di konteks, kini `created_at`, thread, `seq` dengan regresi
deterministik; hasil model disimpan tanpa memeriksa lease lagi sehingga hasil attempt yang dicabut saat model
berpikir masih tersimpan, kini lease diverifikasi ulang sebelum efek apa pun; `hash()` Python (acak per proses)
sempat dipakai untuk kunci klarifikasi, diganti sha256 stabil; mode fake memakai konfigurasi model nyata.

Keterbatasan: hanya fake/contract checks, adapter chat-completions diuji terhadap server stub dan **belum
diverifikasi** terhadap provider nyata (DEV-010/015); developer/QA lewat Hermes (DEV-010), tool workspace/harness
`NotWired`; keputusan accepted disimpan sebagai pesan (penulisan ke `docs/decisions` di clone managed menunggu
integrasi); belum ada peringkas otomatis; estimasi token = karakter/4. Detail: `docs/decisions/agents.md`.
Known issue yang sudah ada (bukan dari tiket ini): `tests/workers/test_processes.py::test_dead_worker_recovery_revokes_workspace_keeps_logs_and_preserves_other_run`
(DEV-004) flaky sekitar 1 dari 14 run di WSL: asersi isi log run yang crash kadang berjalan sebelum baris log tertulis.
Laju yang sama diukur pada kode HEAD murni (1/14) dan pada working tree ini (1/14); tidak diubah karena di luar scope DEV-007.
Handoff R5: diff = file tracked yang berubah + file baru di atas (belum di-stage; lihat `git status`). Cara
menjalankan ada di README bagian "Agen, konteks, dan model (DEV-007)".
Review: NOT_REVIEWED, self-check implementer; R5 (DEV-007/008/009) menunggu independent review.
Tiket berikutnya sesuai dependency: DEV-008 (DEV-003/004/007 selesai).

Review independen Codex (2026-10-05): **NEEDS_FIX**, tidak menutup R5.
Snapshot staged aktual: `25bce5a2e406afa43490b641bea0ca84508ab01d`, baseline HEAD `a4ea386`.
Laporan lengkap: `docs/reviews/DEV-007-review.md`. AC4/5/6 belum terpenuhi, maka DEV-007
kembali IN_PROGRESS dan DEV-008 menunggu fix/recheck.
- P1: fence run tidak atomik dengan penulisan; responder yang dicancel setelah facade verify
  tetap me-resume developer, dan lead plan bisa ditulis setelah pencabutan.
- P1: output lead `needs_user` menjawab/me-resume developer, tanpa pertanyaan ke user.
- P2: key tool memakai job ID baru saat retry, sehingga propose_ticket menggandakan tiket.
- P2: key pesan hasil stabil tetapi generation/context attachment berubah; retry setelah message
  ditulis gagal dengan IdempotencyConflict.
- P2: tools menyimpan secret dummy tanpa redaksi; manifest snapshot juga menyimpan secret dalam path ref.
- P2: counter prompt_tokens tidak dijumlahkan queue ke total saat total provider missing.
- P2: crash sesudah directed message commit kehilangan reply job; reconciler hanya menangani input_request.
Verifikasi reviewer: Windows suite tracked **432 passed, 8 skipped**; WSL agents **130 passed**;
9 probe expected behavior gagal di Windows dan WSL, dengan DB/artifact nyata dan fake berlabel.
Probe/output di gitignored `data/dev007/`; implementasi, tests tracked, dan staging tidak diubah.
Suite WSL lengkap/Docker/provider nyata dan laju flaky DEV-004 tidak dijalankan ulang pada review awal ini.

Perbaikan review oleh Codex (2026-10-05), sesuai instruksi pengguna:
- Fence identity/lease/scope di transaksi yang menyimpan efek; responder harus memiliki capability run.
  Pemeriksaan izin dan answer/resume atomik. Tidak memberi tool approval atau setter status.
- `needs_user` membuat request/event pengguna; developer tetap waiting dan hanya user answer valid
  membuka resume. Eskalasi pesan nonblocking juga terlihat sebagai pertanyaan pengguna.
- Root job berasal dari parent chain DB, bukan pemotongan key. Output tervalidasi disimpan sebelum
  efek dan direplay saat retry, tanpa mengganti proposal/snapshot asal atau menambah model call.
  Payload pesan tetap exact-match; provenance penulisan pertama dipertahankan.
- Jawaban pengguna diwariskan ke retry, context reference tetap dipin selama cleanup dan retry queued.
- Redaksi meliputi args/result tools, Threads, checkpoint/metadata, manifest snapshot, provider/model hasil.
- Normalisasi prompt/input token menegakkan cap total dari lower bound yang diketahui; unknown tetap terlihat.
- Outbox reconciler mencakup seluruh pertanyaan directed, dengan lane/scope asal dan cancellation guard.
File fix: `app/agents/{effects,context,models,redaction,runtime,threads,tools,wiring}.py`,
`app/workers/{queue,supervisor}.py`, `app/persistence/pins.py`; regresi permanen
`tests/agents/test_review_regressions.py` (25 kasus), penyesuaian fixture identitas di `test_threads.py`.
AC1/3/4/5/6 dilengkapi regresi F1–F7; AC2/7 tetap tercakup suite agents lama.
Verifikasi aktual dari `apps/backend`:
- Snapshot index awal `25bce5a2…`: 9 kasus utama kembali **9 failed** dengan sebab bug asli;
  isolasi via git archive di gitignored `data/dev007/staged-baseline`, tanpa mengubah index.
- Suite backend lengkap WSL `python -m pytest -q --tb=short`: **606 passed**, 206.35 detik, Docker nyata.
  Run ini mendahului dua kasus final (eskalasi nonblocking dan pin cleanup).
- Verifikasi final Windows `.venv/Scripts/python.exe -m pytest tests/agents tests/domain tests/persistence
  tests/workers -q --tb=short`: **457 passed, 8 skipped**, 56.17 detik; skip symlink/POSIX.
- Verifikasi final WSL `python -m pytest tests/agents tests/persistence tests/workers -q --tb=short`:
  **362 passed**, 44.68 detik, termasuk seluruh 155 kasus agents dan perubahan pin/queue/supervisor.
- `git diff --check` dan `git diff --cached --check` bersih. Provider nyata tidak dipanggil.
Status implementasi kembali DONE. Review awal tetap NEEDS_FIX historis; perbaikan ini self-check,
**menunggu re-review independen**, bukan penutupan R5. Handoff: `git diff` + file baru dari `git status`,
laporan `docs/reviews/DEV-007-review.md`. Implementasi awal staged tetap dipertahankan; fix belum
di-stage, commit, atau push. Tiket berikutnya secara dependency: DEV-008; tidak dikerjakan dalam scope ini.

Verifikasi developer sesudah fix (laporan pengguna, 2026-10-05): F1–F7 benar/layak;
25 regresi lulus, 23 gagal pada kode index sebelum fix dan dua kasus pengaman lulus.
WSL agents/domain/persistence/workers **465 passed**. Windows **456 passed, 8 skipped,
1 failed** pada flaky DEV-004 `test_a_crashing_runtime_is_retried_once_and_usage_accumulates`;
developer mengukur 2/30 gagal pada HEAD dan 4/30 pada tree sekarang. Tidak ada perubahan kode.
Nonblocker: snapshot checkpoint menduplikasi artifact di runtime_ref (optimasi berikutnya);
DEV-008 perlu mengenali request `orphaned` asal sebagai sudah dieskalasi melalui relasi request/event.
Detail di laporan review. Pengguna mengizinkan commit/push DEV-007; ini tidak menutup R5
atau mengubah self-check menjadi independent review.

**Tujuan:** empat peran memiliki konteks persisten dan komunikasi yang bermakna.

**Scope:** empat SOUL.md, template tugas dan tools per peran, model client
terkonfigurasi, context builder, structured outputs PO/lead, persisted threads.

**Acceptance criteria:**

- Konteks memuat brief, scope disetujui, keputusan accepted, pesan relevan, dan
  referensi repo/artefak dengan batas token; snapshot/hash dicatat per run.
- Ringkasan tidak menghapus histori asli; proposal belum diterima tidak menjadi
  keputusan authoritative. Transcript runtime tidak diduplikasi tanpa batas.
- PO menghasilkan proposal tiket/UAC/dependency tervalidasi; lead menghasilkan
  rencana atau keputusan teknis terstruktur dengan tools sesuai role.
- Pesan dev ke lead disimpan dalam thread, memicu jawaban terarah, dan kembali
  ke attempt yang masih valid. Broadcast/log tidak otomatis memicu semua soul.
- Input request mempunyai ID, scope, recipient attempt/generation, status, dan
  jawaban idempotent. Balasan duplicate/after cancel/after scope revision tetap
  histori dan tidak memajukan attempt basi; resume baru mengambil jawaban valid.
- Model/provider dapat diatur per role; timeout, output invalid, usage/cost,
  redaction secret, dan kebutuhan klarifikasi terlihat.
- DONE tiket ini berdasarkan fake/contract checks yang diberi label. Ketiadaan
  key tidak memblokir DEV-008/009. Percakapan PO dan handoff nyata wajib DEV-015;
  DEV-006 membuktikan runtime nyata, bukan otomatis kualitas semua role.
  Resume mengambil context dari persistence, bukan RAM sebelumnya.

**Verifikasi:** structured output invalid, batas context, pemulihan thread,
otorisasi tools, serta request/reply dengan attempt yang sudah dibatalkan.
Uji duplicate answers, restart waiting, revisi scope, dan akumulasi usage.

## DEV-008 — API aplikasi, autentikasi lokal, dan SSE

### Catatan pengerjaan
Status: DONE. Pelaksana: Codex, 2026-10-05. Review Claude selesai; perbaikan diverifikasi Codex (R5 terbuka).
Rencana: API factory dengan dependency DB/domain/queue; session lokal persisten dan
credential runtime terpisah; receipt command atomik untuk revision/idempotency;
query board/detail/message/run/evidence; SSE replay dari events dengan refresh snapshot;
kontrak TypeScript dan pengujian HTTP, concurrency/restart, serta capture header browser preview.
File rencana: `app/http/`, `app/api.py`, migration/model persistence pendukung,
`tests/http/`, `contracts/api/`, browser checks, dan `docs/decisions/api.md`.
Scope tetap DEV-008; pipeline/preview/release execution yang belum tersedia ditandai
NotWired, tidak disimulasikan sebagai hasil nyata. Belum ada commit/push untuk tiket ini.

Hasil: API/auth/session runtime/receipt atomik, query board/detail/chat/run/evidence,
SSE persisten dan kontrak TypeScript telah dibuat. Pemetaan tujuh AC, file baru,
perintah verifikasi dan known issues: [DEV-008-handoff](docs/reviews/DEV-008-handoff.md).
Keputusan dan endpoint: [API](docs/decisions/api.md).

Verifikasi: HTTP akhir 48 lulus di Windows dan WSL; suite backend lengkap WSL
652 lulus termasuk Docker (sebelum empat tambahan tes terakhir, yang kemudian lulus
pada HTTP suite akhir). Build frontend dan browser HTTP/Chromium lulus. Capture
localhost preview membuktikan Cookie/Authorization/CSRF absen, Origin preview
OPTIONS ditolak 403; native Last-Event-ID reconnect dan stream logout teruji.
Suite Windows luas masih menemukan flaky DEV-004 crash/retry yang sudah diketahui;
hasil lengkap dan failure run awal dicatat pada handoff, tidak diklaim hijau.
Belum di-stage/commit/push. Tidak menjalankan provider nyata atau membuka R5 sebagai
reviewed. Tiket berikutnya sesuai dependency: DEV-009, tidak dikerjakan di scope ini.

Review DEV-008 oleh Claude (Opus 5.5, 2026-10-05): **NEEDS_FIX** pada review awal, bukan penutupan R5. Kontrol akses
(Host/Origin/CSRF, pemisahan cookie dan bearer runtime, receipt atomik, SSE) tidak ditemukan celah. Temuan, semuanya
direproduksi dengan DB/artifact asli lalu diperbaiki oleh reviewer atas instruksi pengguna: (P1) stop gagal 409 karena
heartbeat menaikkan revision job, kini tanpa `expected_revision`; (P2) stop pada run tidak aktif melapor sukses, kini 409;
(P2) log runtime tercampur di daftar pesan dan detail tiket; (P2) log runtime masuk ke prompt model (konteks DEV-007);
(P3) `board` kuadratik terhadap jumlah job; (P3) respons guard tanpa header pengerasan; (P3) client TS melempar
`SyntaxError` untuk error non-JSON. Regresi baru: 8 backend (gagal di tree awal) dan 1 browser. Setelah fix: Windows
513 passed/8 skipped, WSL backend lengkap 664 passed, build dan Chromium 3 passed. DEV-008 tetap **DONE** dengan perbaikan
yang **menunggu re-review independen**. Detail, reproduksi, observasi (tanpa throttling login, tanpa GC session/receipt,
tanpa paging mundur pesan) dan pemetaan AC:
[docs/reviews/DEV-008-review.md](./docs/reviews/DEV-008-review.md).

Recheck perbaikan reviewer oleh Codex (2026-10-05): R008-01–07 dibaca ulang dan
tidak ditemukan blocker untuk commit. Windows `tests/http` + `tests/agents/test_context.py`
74 passed, build frontend lulus, Chromium 3 passed, diff checks lulus. Suite lengkap
tidak diulang; 513 passed/8 skipped Windows dan 664 passed WSL di atas adalah hasil
Claude. Pengguna mengotorisasi commit/push DEV-008 setelah recheck. Ini verifikasi
perbaikan, bukan penutupan independent review R5. Kontrak DEV-009: stop body `{}`,
tanpa `expected_revision`; key yang sama dipakai untuk retry command yang sama.

**Tujuan:** UI mendapat kontrak command/query dan event yang konsisten.

**Scope:** REST untuk project, tiket, proposal, approval, chat, feedback, run,
artefak; SSE untuk aktivitas; autentikasi pengguna lokal dan kontrak tipe frontend.

**Acceptance criteria:**

- Endpoint command memanggil domain service dengan user/runtime identity yang
  tepercaya; expected revision/idempotency mencegah mutation ganda atau basi.
- Endpoint query menyediakan snapshot board/detail, log dan evidence terkait.
- SSE replay memakai cursor/Last-Event-ID; cursor kedaluwarsa memicu refresh
  snapshot. Final message persisten setelah token stream selesai.
- Reconnect tidak menggandakan message/job/approval; error terstruktur terlihat.
- Kontrol API, CORS, dan session disiapkan untuk origin terpisah dari generated
  preview; aplikasi target tidak memperoleh hak pengguna pada kontrol API.
- Implementasikan host/session policy arsitektur §10, local control 127.0.0.1,
  preview localhost; cookie host-only tanpa Domain, HttpOnly/SameSite, exact Origin
  dan CSRF pada mutations. Runtime credential berbeda dari cookie browser.
- Input answer command memvalidasi request ID/scope/generation dan idempotency.
  Waiver baseline adalah command pengguna, bukan status setter.

**Verifikasi:** API integration tests untuk autentikasi, stale commands,
reconnect/replay, dan penolakan akses dari origin preview. Browser/header capture
preview uji membuktikan session kontrol tidak terkirim; CORS test saja tidak cukup.

## DEV-009 — GUI board, chat PO, dan review scope

### Catatan pengerjaan
Status: DONE setelah perbaikan review Codex. Pelaksana awal: Claude (Sonnet 5.5), 2026-10-05.
Review Codex menemukan regresi pada intent approval, fencing editor, dan payload UAT.
Rencana perbaikan: ikat konfirmasi/checklist ke identitas scope/target; pertahankan revision
draf dan evidence kandidat lengkap; uji retry command, input chat, brief, dan SSE.
File: komponen GUI, workspace/client, `tests/web-browser/review-*`, laporan review.
Rencana: GUI React di `apps/web/src` memakai `ApiClient` DEV-008 (tanpa state lokal sebagai
sumber kebenaran): login code lokal, daftar/buat proyek + brief, board per phase yang
dimuat dari snapshot dan diperbarui lewat SSE (`watch`), drag/drop hanya memanggil
`priority`, panel detail tiket (scope version, UAC, dependency, pesan, kandidat/bukti,
blocker, label fake), chat PO (breakdown/revise/note) dengan diff proposal accept/reject,
approval batch scope yang eksplisit, daftar run/activity (waiting_input/quota, stop dengan
body `{}` tanpa `expected_revision`), serta tampilan conflict/error terstruktur.
File rencana: `apps/web/src/**`, `tests/web-browser/**` + `playwright.web.config.ts`,
update `tests/smoke/health.spec.ts`, `docs/decisions/gui.md`, handoff DEV-009.
Scope tetap DEV-009: tanpa kantor Three.js, preview (DEV-011), UAT/release execution.
Belum ada commit/push.

Hasil: GUI login/proyek/brief, board dengan drag/drop prioritas dan approval batch, detail
tiket (scope version, diff usulan PO, dependency, kandidat/bukti/UAT/waiver), chat PO dan
aktivitas run (stop dengan body `{}` tanpa `expected_revision`) dibuat. Pemetaan AC ke bukti,
perintah, dan known issues: [DEV-009-handoff](docs/reviews/DEV-009-handoff.md). Keputusan:
[GUI](docs/decisions/gui.md).

Verifikasi (Windows): `npm run build` lulus; `playwright.web.config.ts` 9 tes lulus (diulang
3x = 27 lulus): 3 melawan API/scheduler/runtime nyata dengan FakeProvider berlabel (brief ->
proposal -> revisi tolak/terima -> edit -> approval batch -> reload, conflict 409, stop), 6 dengan
respons API tiruan sesuai kontrak (quota/input/evidence hilang/UAT/waiver/layar 3 ukuran).
Smoke DEV-001 5 lulus dan browser DEV-008 3 lulus. Uji mutasi stop-dengan-revision menggagalkan
tes. Bug yang ditemukan tes: `Run.result` null membuat render crash (diperbaiki, tipe kontrak
diperbarui, ditambah `CrashGuard`). Pytest tidak dijalankan ulang: backend tidak berubah.
Keterbatasan: tanpa UI release (DEV-014), PO/model nyata belum diverifikasi (DEV-015), state quota/
input hanya diuji lewat respons tiruan. Belum commit/push; tiket berikutnya DEV-010 tidak dikerjakan.

Review Codex 2026-10-05: verdict awal **NEEDS_FIX**, tiket sempat dibuka IN_PROGRESS.
15 kelompok temuan diperbaiki: intent approval scope/batch dan UAT/checklist dipin;
revision draf scope/brief dibekukan; deskripsi/revert dipertahankan; evidence UAT termasuk
preview smoke; retry 5xx memakai key yang sama; pertanyaan blocking masuk chat dan dijawab
ke run yang tepat; gap cursor/reconnect diperbaiki; balasan handoff dan perubahan availability
artefak terlihat; log generation 0, batas repair 3, serta diff historis diperbaiki.
Laporan: [DEV-009-review](docs/reviews/DEV-009-review.md).
Verifikasi akhir Windows: build lulus, GUI **29 lulus** (9 awal + 20 regresi; 5 tes API nyata,
24 respons tiruan), smoke **5 lulus**, browser DEV-008 **3 lulus**, diff whitespace bersih.
Probe kode staged menunjukkan UAT 409 sebelum fix -> 200 setelah fix dan draf lama
menimpa update dengan 200 sebelum fix -> 409 setelah fix. Fixture UAT memakai receipt
sintetis berlabel contract fixture, bukan QA nyata/provider nyata. Pytest backend tidak
dijalankan ulang karena kode backend produk tidak berubah. Implementasi kembali DONE;
review perbaikan menunggu re-review independen, R5 belum ditutup. Perbaikan belum di-stage;
tidak ada commit/push.

**Tujuan:** pengguna bisa memberi brief dan review tiket sebelum coding.

**Scope:** proyek/brief, board, detail tiket/UAC, chat PO, diff proposal,
batch approval, daftar run/activity, blocker, dan indikator fake/real.

**Acceptance criteria:**

- Brief coffee shop dapat diubah menjadi proposal profile/menu/transaksi;
  pengguna berdiskusi, mengedit, menerima/menolak revisi, lalu approve scope.
- Board membaca backend. Drag/drop mengubah prioritas atau command yang diizinkan;
  approval scope/UAT/release memakai tindakan eksplisit.
- Detail menampilkan scope version, dependency, pesan, bukti, dan status pekerjaan.
- Waiting input/quota, manual-pending, baseline waiver, target/build identity,
  dan artefak unavailable terlihat; fake output tidak dilabeli QA nyata.
- Streaming/reconnect/reload menjaga konteks dan menunjukkan conflict/error.
- UI usable di ukuran desktop/laptop tanpa membutuhkan kantor Three.js.

**Verifikasi:** walkthrough brief → proposal → revisi → approval dan reload;
uji browser untuk alur utama, termasuk conflict revision dan fake label.

## DEV-010 — Pipeline lead/developer/QA dengan bukti test

### Kontrak fixture dan triage QA — 2026-10-07

Status perbaikan: DONE (implementasi dan recovery demo). Review: NOT_REVIEWED.
Scope: fixture UI bernama diekspansi supervisor sebelum suite canonical dipin;
planning wajib memilih mode select secara eksplisit; runner mencatat opsi
native dropdown secara bounded. Repair binding mengambil label dari input
fixture asli yang cocok tepat satu enabled option, bukan ID dugaan. Model hanya
memilih indeks. Assertion, UAC, source, build dan approval tidak dilonggarkan.
Diagnosis application wajib memetakan UAC otomatis dan kutipan source shipped
nyata serta melewati guard selector/action/CSV sebelum meminta repair developer.
Static option yang dinyatakan eksplisit dan dikutip dalam source boleh tetap
menghasilkan application diagnosis bila hilang di DOM; attribution ambiguous
tetap QA. Runner upgrade pada target review_approved di QA membuat target baru,
tanpa mengonsumsi jatah koreksi suite atau memindahkan approval lama.

File: `app/pipeline/{contracts,qa_policy,qa_repair,runtime}.py`,
`contracts/verification/acceptance.py`, soul/instruksi QA, arsitektur dan docs
`qa-policy.md`/`qa-browser-dsl.md`.
Verifikasi aktual: AST semua modul pipeline/runner, import PipelineRuntime/
QaPlan/QaOptionRepair melalui `.venv/bin/python`, `git diff --check`, dan
konsistensi serialization/digest suite persisten — semuanya OK. Tes regresi
unit tidak ditambah/dijalankan karena tidak diminta. Named fixture planning dan
seluruh cabang guard baru belum memiliki bukti regresi otomatis; recovery
dropdown/repin runner diuji melalui demo nyata yang sebelumnya diizinkan.

Bukti Mini CRM #4: retry diagnosis `74f795f5966e4e38b9c5f05e131e7601`
menghasilkan target runner baru; verification failed dengan DOM options nyata
`92d08c83b0354b07a8b32fbdfc8be1a3`; diagnosis/repair binding
`dd9fefafe2344293bb973cfdf2c4bfac` mengubah select value 1/2 menjadi
label Customer 1/Customer 2 yang berasal dari input asli. Model diagnosis+
binding memakai 2 calls, 30.818 token, active_s 6,27, cost_usd 0,008292
sesuai usage provider. Target baru `128289d0a4f442239704cbeb4929a1f4`,
verification `540abaa8535f426a83cdbc49224cc6b2`: kandidat 2/2 passed,
baseline 2/2 failed karena fitur dashboard/export belum ada. Full verification
active_s 35,78. Tiket masuk UAT; repair_cycles tetap 1, source commit tetap
`73d6b233858abd0709cce5e7e75f5f41e96dd829`; accepted #1/#2/#3 tidak disentuh.
Hasil konkret ini tidak membuktikan seluruh kemungkinan suite/runner bebas
error. Kasus tanpa binding unik/bukti memadai tetap QA failed, bukan auto-pass.

Handoff: periksa `git diff` untuk mapping fixture→canonical pin, preflight
selection, opsi DOM→binding terbatas, guard application→request_changes dan
runner refresh→target baru. Jalankan worker pipeline; bila ingin regresi unit,
file acuan existing `tests/pipeline/test_contracts.py`, `test_harness.py` dan
`test_product_regressions.py`. Tidak ada
independent review yang diklaim.

### Pemulihan suite QA Mini CRM — 2026-10-07

Status perbaikan: DONE (implementasi dan demo). Review: NOT_REVIEWED.
Scope: koreksi selector jQuery `:contains()` dan setup yang hilang pada browser
context terisolasi, tanpa mengubah source, assertion, UAC atau approval. Tambah
preflight selector dan recovery otomatis yang hanya menyalin setup dari prefix
tes yang sudah lulus pada target yang sama. Suite/target dan bukti lama tetap
immutable; jalankan ulang baseline/kandidat untuk tiket demo #2 dan #3.
File rencana: `app/pipeline/{contracts,qa_policy,qa_repair,runtime}.py`,
instruksi QA dan dokumen keputusan. Restart worker setelah perubahan siap.
Hasil implementasi:
- Preflight planning baru menolak pseudo-class jQuery, tanpa menolak string
  atribut yang hanya berisi teks `:contains`. Legacy visibility yang terbukti
  syntax error menjadi visibility dan assertion substring case-sensitive dengan
  expected asli; tidak mengganti expected dari output aplikasi.
- QA memilih indeks setup dari prefix tes yang lulus di target yang sama.
  Supervisor menyalin input/tindakan asli dan mempertahankan seluruh langkah,
  assertion, ID, UAC dan purpose. Setup duplikat ditolak; pembukaan detail setelah
  reload wajib ditempatkan tepat sebelum failed_step, bukan sebelum seluruh tes.
- Retry membaca diagnosis tersimpan yang masih cocok dengan kandidat/target/
  verification. Perubahan prompt tidak meminta diagnosis baru yang kemudian
  bentrok dengan diagnosis lama. Tidak reset usage atau memindahkan approval.
- Snapshot planning QA mempunyai ruang minimum 16384 token agar instruksi/schema
  dan dependency #4 muat; policy usage demo tetap unlimited seperti sebelumnya.

Verifikasi aktual:
- AST/import empat modul Python OK dan `git diff --check` bersih.
- Probe terhadap bukti authoritative historis: recipe membuka detail setelah
  reload diterima; recipe menggandakan setup ditolak; seluruh langkah/kriteria
  asli tetap identik. Selector invalid ditolak dan teks atribut quoted diterima.
- Demo #3: runner browser nyata **3/3 passed**, baseline **3 failed** karena
  kontrol fitur belum tersedia, repo gate passed. Verification
  `27993dacdf6347e7b1ac79c08a6bf135`; pengguna kemudian menerima tiket tersebut.
- Demo #2: awalnya selector berhasil dikoreksi otomatis, tetapi proposal model
  setup yang salah menggandakan data. Guard diperketat. Setelah #3 diterima,
  platform mengembalikan #2 ke development untuk base baru; recipe operator
  berizin pengguna memulihkan suite dengan Edit setelah reload, melalui job
  persisten `c618484a6bac4867a870446cea6c952f`, tanpa overwrite bukti lama.
  Developer dan TL menjalankan kandidat baru pada accepted base #3. Runner
  browser nyata **2/2 passed**, baseline **2 failed** karena kontrol interaction
  belum tersedia, repo gate passed. Verification `10690a2523574e5e81323a29cbdde910`;
  tiket #2 masuk UAT, menunggu keputusan pengguna.
- #4 melewati planning yang sebelumnya gagal karena snapshot 8000 token; worker
  tetap menjalankan pipeline proyek. Restart worker dilakukan dengan shutdown
  tertib; backend/frontend tetap berjalan.

Keterbatasan: tidak menambah/menjalankan suite regresi platform; verifikasi tugas
ini memakai probe terkait bukti dan browser demo nyata. Auto-repair konservatif
bukan dukungan semua selector/setup; proposal yang belum valid tetap failed.
Context overflow Hermes yang muncul pada attempt developer lama dicatat sebagai
histori; perubahan ini tidak mengaktifkan kompresi Hermes atau menghapus batas
fisik konteks provider. Retry developer dengan suite benar berhasil membuat
kandidat baru. Handoff: diff empat modul, instruksi/SOUL QA, arsitektur dan
`docs/decisions/qa-policy.md`; recipe lokal gitignored
`data/repair-mini-crm-qa-plan.py` dan artefak audit DB. Belum commit/push.

### QA ringan dan diagnosis sebelum repair — 2026-10-07

Status: DONE (implementasi 2026-10-07). Review: NOT_REVIEWED.
Rencana: policy QA ringan pada konteks PO/TL/QA, preflight persisten sebelum
development, pemetaan automated/manual terlihat di detail/UAT, serta diagnosis
failure sebelum meminta repair aplikasi. Harness/gates/pin/approval tetap wajib;
mode UAC yang disetujui tidak direlabel otomatis. Tidak membuat batas token baru.
File: agents, app/pipeline, app/agents/runtime.py, app/http/queries.py,
contracts/api/types.ts, apps/web/src/components/Ticket.tsx dan docs keputusan.
Tes belum diminta; tidak ditambah/dijalankan. Pemeriksaan statis setelah perubahan.
Hasil: policy lightweight diberikan ke PO sebelum scope approval dan ke TL/QA
saat planning. Planning baru memvalidasi coverage/action/schema dan menolak
mandatory case yang hanya menduplikasi manual UAC; seluruh-manual tetap smoke
dengan assertion. Receipt preflight mencatat scope/suite/capability dan mapping.
Tidak memangkas suite historis atau mengganti mode UAC yang sudah disetujui.
API detail dan UI menampilkan pembagian otomatis/checklist UAT; checkbox tetap
kosong dan divalidasi backend sebelum acceptance.
Failure yang belum dapat dikoreksi melalui fakta DOM menjadwalkan diagnosis
QA persisten dengan key verification/target. Retry diagnosis memakai evidence
yang sama. Kontrak memerlukan attribution/expected/observed/reason untuk semua
failed test; hanya application fault meminta repair dengan bukti dan counter
biasa. Test/unknown/infrastructure tetap failed di QA. Setelah diagnosis test,
CSV-escaped tokens dapat dikoreksi sempit dari input fill asli, tanpa mengambil
actual sebagai expected, lalu target/baseline/kandidat diverifikasi ulang.
Receipt diagnosis dipakai ulang pada retry; approval, mandatory execution,
generation/lease, cleanup, budgets/usage kumulatif dan batas suite repair tetap.
Verifikasi aktual: AST 7 file Python dan import runtime/scheduler/policy/query/
kontrak/agent lulus; schema diagnosis dan suite dapat di-inline; TypeScript
`node node_modules/typescript/bin/tsc --noEmit -p apps/web/tsconfig.json` dan
`git diff --check` lulus. Tidak menambah dependency atau migrasi DB.
Keterbatasan: tes regresi/browser/provider dan recovery job diagnosis belum
dijalankan. Pemeriksaan statis tidak membuktikan ketepatan diagnosis model atau
pengurangan waktu/token. Gap yang nyata, failure campuran dan test fault di luar
koreksi sempit masih memerlukan intervensi; tidak ada auto-pass/manual downgrade.
Handoff: policy baru `app/pipeline/qa_policy.py`, contract/dispatch/runtime/
repair, public DTO/query dan UI, souls/instruksi PO/TL/QA, serta
`docs/decisions/qa-policy.md` dan spesifikasi/keputusan yang terkait.
Layanan tidak sedang berjalan saat perubahan dibuat; implementasi berlaku saat
API/worker dimulai kembali. Tidak menjalankan job model atau demo baru, dan
commit/push belum diminta untuk assignment ini.

### Diagnosis expected CSV QA — 2026-10-07

Status: DONE (implementasi 2026-10-07). Review: NOT_REVIEWED.
Rencana: jelaskan parsed CSV cells pada schema/instruksi QA, tolak kekeliruan
escaping yang dapat ditelusuri ke input tes saat planning, dan simpan diff
expected/actual yang dibatasi ukuran. Mismatch CSV menunggu diagnosis di QA,
tanpa langsung meminta perubahan aplikasi. Koreksi demo memakai input tes,
artefak suite/target baru dan eksekusi ulang; bukti lama tidak ditimpa.
File: contracts/verification/acceptance.py, pipeline/contracts.py,
pipeline/qa_repair.py, pipeline/runtime.py, instruksi QA dan keputusan pipeline.
Tes regresi tidak diminta; tidak ditambah/dijalankan. Verifikasi demo ulang
termasuk tindakan pemulihan yang disetujui pengguna.
Hasil: schema/capability revisi 3 dan instruksi QA membedakan parsed cells dari
raw CSV. Planning memeriksa expected terhadap input fill, tanpa membaca actual.
Runner menyimpan maksimal delapan diff sel (200 karakter/nilai); mismatch
parsed CSV tetap failed di QA untuk diagnosis, tidak mengubah expected otomatis.
Koreksi demo hanya dua sel dari input asli; seluruh test IDs/UAC/actions tetap.
Suite baru `2d8a6809d044492f9d6a7b7966bb57a5`; kandidat baru
`f73c122d30cd41ffa6376f8dba555c1c`, verification
`dec349a5918b4b6aa0d5f00c2ab3856d`: browser 2/2 passed, baseline 2/2 failed,
smoke passed, tanpa missing/skipped. Download 124 byte SHA256
`35a24e3c7769a52dc168656166ae286531c2bc67489cfbd00b009c04fda61192`
sama persis dengan run gagal sebelumnya. Kode aplikasi CSV tidak berubah;
developer menambahkan file scratch/probe yang tidak dipakai aplikasi pada kandidat.
Worker dihentikan tertib dan direstart; job developer yang sudah berjalan
dilanjutkan dengan suite/konteks koreksi, kemudian review dan QA baru. Counter
repair tetap 2 dan usage/histori tidak direset. Status Accepted teramati setelah
QA; implementer tidak memberi approval UAT/release atau memindahkan accepted ref.
Checks aktual: AST 4 file Python, import 3 modul, schema inline, TypeScript
`tsc --noEmit -p apps/web/tsconfig.json`, dan `git diff --check` lulus.
API health ok, frontend HTTP 200, worker pipeline siap.
Keterbatasan: belum ada tes regresi baru untuk jalur mismatch/truncation/planning;
run demo ini membuktikan koreksi suite dan eksekusi CSV, bukan seluruh DSL.
Mismatch yang belum terdiagnosis tetap memerlukan diagnosis QA; tidak ada
auto-repair expected atau auto-approval. Pengguna meminta commit/push hasil ini
pada 2026-10-07; pendekatan QA ringan masih diskusi dan belum diimplementasikan.
Handoff: diff sembilan file (runner, kontrak, routing, UI, instruksi dan docs);
review independen belum dilakukan.

### Setup proyek baru otomatis dan recovery cleanup lokal — 2026-10-06

Catatan operasional lanjutan: log Docker menunjukkan VM otomatis berhenti karena
idle 30 detik, lalu startup berulang menahan command developer. Timeout idle
Docker Desktop lokal diubah 30 → 3600 detik untuk demo dan Desktop direstart
tanpa reset data; nilai awal disimpan di file gitignored
`data/docker-idle-demo-backup.json`. Tidak mengubah setting Docker host lain.
Job developer `b00b5079abe741fab5af25cc2a71ca3a` kemudian stopped karena
200578 total tokens melewati default 200000. Sesuai instruksi unlimited demo
sebelumnya, budget pool demo catatan dan policy pool baru diperbarui melalui
`JobQueue.extend_budget(unlimited_budgets=True)` dengan authorization/event user;
usage lama tidak dihapus. Arsip milik job/generation/base diverifikasi SHA256
terhadap ARCHIVE-MANIFEST, 9 file source dipin sebagai checkpoint sebelum retry
`de004a38e4b34aa6b953d2626d04fca2` dibuat dalam transaksi yang sama.
Scope, approval, batas repair, target QA dan accepted ref tidak diubah.

Status perubahan: DONE (implementasi). Pelaksana: Codex. Review: NOT_REVIEWED.
Assignment: proyek baru tidak memerlukan konfigurasi runner manual; pulihkan demo
catatan yang tertahan setelah QA planning. Rencana: job setup persisten tanpa
model menyiapkan runner React/Vite referensi dan empty Git base di worker;
rekonsiliasi menemukan proyek baru belum disiapkan. Approval scope tetap wajib.
Supervisor mencoba cleanup ulang untuk attempt lokal yang terbukti sudah selesai,
tanpa menunggu proses worker mati. Penyebab demo aktual: `docker ps timed out`
saat cleanup QA; arsip runtime sudah berhasil, bukan bug pemeriksaan arsip.
File: pipeline setup/wiring, worker, HTTP create project, broker Git, supervisor,
README/dokumen keputusan dan log. Tidak menambah/menjalankan tes karena assignment
tidak meminta tes; gunakan sintaks/import dan observasi layanan/demo nyata.

Hasil: `pipeline/setup.py` membuat job persisten `project_setup` saat create
project API (satu transaksi receipt) dan saat maintenance menemukan proyek baru
lama belum siap. Manifest referensi, base Git kosong, event dan completion memakai
identitas job/generation; publikasi DB atomik. Retry initialization hanya menerima
bare repo kosong/satu initial empty commit; tidak mengubah accepted code/ref lain.
Repo existing dan runner lengkap tidak ditimpa; approval scope/QA/UAT/release tetap.
Supervisor menyimpan bukti thread lokal selesai untuk mencoba ulang cleanup,
memeriksa owner/host/generation dan proses sebelum melepas ledger/slot.
Verifikasi aktual: `ast.parse` **5 file Python OK**; import setup/HTTP/supervisor/
broker serta parsing manifest referensi **lulus**; `git diff --check` **lulus**.
Docker Desktop sempat tidak merespons socket/image inspect; direstart tanpa reset
data. API `/health` **ok**, frontend tetap aktif, worker baru mendaftarkan runtime
`project-setup`. Demo `25f7b5e9019646e0b13908fa59fa8b59`: cleanup job QA
`6bfb9a03563a4531bce2a1daf94aa0b9` dipulihkan melalui recovery resmi,
`cleanup_pending=false`, `needs_human=false`; tiket #1 masuk **development** dan
job developer **running**. Histori dan artefak dipertahankan.
Keterbatasan: tidak membuat proyek dummy di DB pengguna untuk menguji setup;
jalur create-project baru, crash setup dan retry cleanup dalam worker yang sama
belum melalui tes regresi/integrasi. Recovery demo memakai worker baru dengan
owner lama sudah mati; bukan bukti cabang recovery owner masih hidup. Keberhasilan
developer/QA/UAT akhir demo belum diklaim. Backend/worker direstart untuk memuat
kode; FE tetap berjalan. Handoff: diff file di atas termasuk file setup baru;
review independen belum dilakukan. Belum commit/push pada assignment ini.

### Review Claude Batch 4 DEV-010/011 — 2026-10-06

Status perbaikan: DONE (implementasi). Pelaksana: Codex. Review perbaikan: NOT_REVIEWED.
Lima temuan dikonfirmasi pada head awal `743ede7` dan diperbaiki:

1. Domain `request_changes` menulis message repair_feedback dengan kandidat,
   scope, cycle dan provenance dalam transaksi yang sama dengan invalidasi dan
   perubahan phase. Berlaku untuk user UAT, lead dan QA; `_reject` tidak lagi
   membuat pesan duplikat. Workspace memilih feedback terbaru untuk proyek/tiket/
   scope melalui SQL, memeriksa identitas kandidat superseded, lalu memulihkan
   byte kandidat itu atau rebase ke accepted base saat ini. Task implement memuat
   reason/candidate/message ID; konteks juga menerima system repair/rebase intent.
2. Drift runner/base pada review/QA memakai `_reject`: phase kembali development,
   candidate lama superseded, feedback tercatat, dan job selesai secara atomik.
   Repair berikutnya memerlukan build target, review, QA dan UAT baru. Counter,
   budget kumulatif, batas repair dan keputusan pengguna tetap ditegakkan.
3. Direktori `.verification`/`.hermes` mendapat descriptor resource sebelum dibuat,
   owner/generation/allocation marker, pemeriksaan UID/mode/path, serta finalizer
   producer. Cancel menghentikan proses tanpa menghapus file yang masih dipakai;
   finally producer mengarsipkan lalu cleanup sebelum thread melepas slot.
   Reconciler membersihkan container/workspace dahulu, direktori sementara terakhir.
   Log transport/conversation Hermes menjadi artefak beredaksi dan attachment
   pesan log (pin persisten); worker config/bearer/private home tidak diarsipkan.
   Label proses diperiksa sebelum cleanup Hermes, termasuk jalur lease revoked.
   Arsip gagal/ownership salah menahan cleanup; bukti kandidat/QA/UAT tidak dihapus.
4. `build_target` menolak symlink output sebelum membuat target manifest. Packing
   bundle berada di dalam jalur build_error sehingga commit tetap dicatat sebagai
   kandidat gagal build dengan handoff, bukan error submit generik berulang.
5. Root socket preview diperiksa dengan lstat: real directory, UID supervisor,
   tanpa akses group/other. Root yang tidak memenuhi syarat ditolak, bukan chmod
   otomatis. Pemeriksaan berlaku sebelum launch dan sebelum teardown socket.

Catatan stdout gate adalah batas otoritas yang sudah didokumentasikan; kelulusan
QA tetap membutuhkan evidence runner terpisah, bukan TAP target.
File hasil: `apps/backend/app/domain/service.py`, `apps/backend/app/agents/context.py`,
`apps/backend/app/pipeline/{files,hermes,runtime,wiring,workspace}.py`,
`apps/backend/app/workers/runtime.py`, `apps/backend/app/workspace/supervisor.py`,
`apps/backend/app/preview/service.py`, `docs/decisions/{pipeline,preview}.md`, dan log ini.
Verifikasi aktual:
- `apps/backend/.venv/bin/python` dengan `ast.parse` pada file berubah + baru —
  **10 file Python OK**.
- Dari `apps/backend`, `.venv/bin/python` meng-import sepuluh modul terkait —
  **10 modules OK**, tanpa menjalankan layanan/DB atau inference.
- `git diff --check` — **lulus**. Self-check mencakup diff dan file baru `files.py`;
  tidak diklaim sebagai independent review atau verifikasi perilaku.
Tes regresi tidak ditambah/dijalankan karena permintaan lanjutan tidak secara
eksplisit meminta tes. Docker, provider, BE/FE/worker tidak dijalankan.
Keterbatasan: tidak menyapu direktori runtime lama tanpa marker/resource; artefak
diagnostik yang dipin tetap memakai disk. Log/config >64 MiB ditolak dan menahan
cleanup agar bukti tidak hilang. Job runner drift yang sudah gagal pada versi lama
memerlukan retry operator; histori/blocker DB tidak diubah oleh patch repository.
Handoff regresi: UAT reject setelah feedback lead lama (reason dan tree kandidat
terbaru), rollback/duplicate request, runner drift di review dan QA, cleanup
success/cancel/crash dengan pin/credential redaction, symlink build, serta root
socket foreign UID/mode/symlink pada launch dan teardown. Checkpoint R6 tetap terbuka.

### Semua budget demo tanpa batas — 2026-10-06
Status: DONE (scope konfigurasi demo dan resume). Pengguna meminta semua budget unlimited khusus demo.
Rencana: otorisasi eksplisit untuk model/tool calls, waktu aktif, token total dan
job output cap null; registry provider tetap menentukan ukuran respons teknis.
Pertahankan usage, leases, cleanup, idempotency, serta approval. Terapkan policy
hanya project demo, hilangkan batas siklus repair demo, lalu resume #1.
File: workers/queue, http schemas/application, pipeline Hermes/relay, domain
repair guard dan keputusan pipeline. Tes regresi belum dijalankan.
Hasil: `unlimited_budgets:true` membutuhkan keputusan user pada command; semua
cap job menjadi null. Project demo menerapkan policy ke pool baru, job existing
memakai policy yang diotorisasi dengan event user:local, dan empat tiket demo
memiliki unlimited_repairs. Usage/counter/history tidak direset. API menampilkan
repair_limit null; OpenAPI/requests.ts dihasilkan ulang dengan
`python -m app.http.contract`. Batas teknis model registry per respons, timeout,
lease/sandbox dan approval tetap berlaku; default proyek lain tetap finite.

Suite #1 diperbaiki sebagai artefak baru `40b1a2c9e66c4deabaf8e7ecc9c85231`,
digest `cef2abd8d0c86b116da10bde05c9e63170a7012cb26dd20b05b28ef938de8d0d`:
dua assert_text membaca `.item-name`, bukan seluruh baris beserta Delete.
Suite/report lama tetap histori; belum dianggap QA lulus, target baru wajib.
Retry API budget `6868427c813f439c86d78688d68dd003` gagal karena #4 sudah
diterima user dan base accepted maju, sementara checkpoint #1 masih base kosong.
Perbaikan `pipeline/workspace.py` menjaga checkpoint lama sebagai attachment
histori, menolak scope berbeda, dan memakai jalur repair/rebase pada base terbaru.
Tidak meng-overlay snapshot lama ke accepted tree atau memindahkan approval.

Retry operator `5bd35bbc15464a9bae7e7f0456def67f` benar **running** memakai
DeepSeek V4.1 Flash pada base accepted `bcdc0bb12bc9cc079057fd0bee1d209db323cad2`.
Observasi 03:07 WIB: 4 model calls, 11 tool calls, 47.835 token; total histori
#1 mencapai 204 model calls tanpa berhenti pada cap lama 200. Checkpoint lama
`149a30969a784cf5bd733b8672d34762` tetap tersimpan, ditandai obsolete pada retry.
Worker/API direstart tertib; #4 tetap accepted. `git diff --check` lulus.
Tes regresi tidak ditambah/dijalankan (permintaan konfigurasi/resume demo);
QA/UAT #1 belum lulus dan independent review NOT_REVIEWED. Handoff: diff
queue, HTTP schemas/application/queries, pipeline Hermes/relay/workspace,
runtime_spike relay, domain repair guard, kontrak API, log ini dan keputusan
pipeline. Receipt operator ignored. Perubahan belum commit/push.

### Budget total token demo tanpa batas — 2026-10-06
Status: DONE (scope konfigurasi/command). Pengguna secara eksplisit
meminta budget token unlimited, model DeepSeek V4.1 Flash, dan resume demo.
Izin repair terpisah sudah tercatat: #1 cycles 5/8, #4 cycles 4/7. Kedua job
latest stopped total_tokens, bukan repair limit. Rencana: parameter otorisasi
pengguna pada budget command untuk total_tokens=null, scope peers memakai policy
sama, histori tetap, fresh retry/lease. Project demo menurunkan policy null ke
pool baru #2/#3; default project lain tetap finite. Model/tool/time/output caps
tetap berlaku. Model developer/lead konfigurasi lokal ignored sudah DeepSeek.
File: workers/queue, http schemas/application, pipeline scheduler, docs.
Tidak mengubah accepted candidate/QA/UAT/release. Independent review NOT_REVIEWED.
Hasil: API budget command `unlimited_total_tokens: true`, `additions: {}`
diterapkan ke job latest yang token-exhausted, cleanup selesai, masih eligible.
Keputusan null diberlakukan pada peers dalam pool scope; policy project demo
`pipeline.budget_limits.total_tokens=null` untuk pool baru #2/#3, diubah dengan
apply_change/revision + event actor user:local. Default project lain unchanged.
Command nyata localhost/auth/CSRF/idempotency berhasil pada #1/#4; hasil retry
`928d9965f85149c5ab5d690ed557f98b` / `85a0b02c63274c9f90944505e9d201af`.
Pemeriksaan state sebelum/sesudah: usage identik, total_tokens null, caps lain
identik. Receipt operator tersimpan ignored, model konfigurasi ignored.
API dan worker direstart, dua job benar-benar diklaim. Review DeepSeek #1
selesai 8,56 detik, output 2.234 tokens, request_changes pada kandidat lama;
developer #4 runtime metadata terakhir DeepSeek V4.1 Flash, #1 repair antre.
`git diff --check` pass. Tes regresi belum dijalankan untuk perubahan command
ini (assignment konfigurasi/resume; tidak ada permintaan tes baru). Ini bukti
operasi nyata, bukan independent review atau keberhasilan produk/QA/UAT.
Handoff R10: diff enam file tracked di atas; belum commit/push.

### Perbaikan reload acceptance — 2026-10-06
Status: DONE (scope fix), assignment monitoring platform. Suite baru #4
mengklaim UAC-10 restore localStorage, tetapi hanya add/assert tanpa reload.
DSL lama tidak punya reload. Rencana: aksi reload tanpa selector/value, guidance
QA untuk persistence, actual browser fixture yang membedakan state persisten
dari memory-only. Suite/runner/target baru wajib; jangan relabel evidence lama.
Scope developer baru #1/#4 sudah selesai tetapi ditolak (placeholder tests /
selector mismatch); cycles #1 5/5, #4 4/4, tidak diperpanjang lagi. Budget #1
naik satu kali +200.000 menjadi 1.400.000, #4 tidak memakai tambahan budget.
File: pipeline contracts/runtime, runner, QA instructions, tests/docs;
independent review NOT_REVIEWED.
Hasil: reload tanpa selector/value; elemen lain tetap wajib selector. Runner
memakai page.reload(networkidle), response health 200, storage dalam context
test tetap dipertahankan. QA guidance mewajibkan assertion sesudah reload.
- `pytest tests/pipeline/test_contracts.py tests/pipeline/test_keyboard_acceptance.py
  tests/pipeline/test_review_context.py -q`: **47 passed**, 29,89 detik, Mac.
  Actual browser fixture: fill literal newline failed; press Enter + reload
  passed, memory-only yang kehilangan data sesudah reload failed. Eksekusi
  mandatory tetap dihitung, nonce/suite/target berbeda, runner mismatch ditolak.
- `git diff --check`: pass. Model tidak dipakai dalam fixture ini; bukan QA
  produk atau independent review. Handoff R9: diff contracts/runtime/runner,
  QA instructions, docs, tests contracts dan keyboard_acceptance.
Monitoring nyata sampai tidak ada job aktif: rencana baru #1/#4 9,94 / 11,63
detik; developer #1 retry budget 78,24 detik, #4 91,44; review 2,41 / 2,87;
QA #4 10,93 detik. #1 ditolak karena tes placeholder true===true; #4 gagal
locator data-test-id yang belum diimplementasikan. Extra cycle yang diizinkan
terpakai; tidak auto-retry atau memperpanjang lagi. #2/#3 tetap menunggu #1.
Perubahan platform ini belum commit/push. Worker baru memuat runner reload;
API/web tetap hidup, produk berhenti pada blocker bounded repair.

### Perbaikan keyboard acceptance — 2026-10-06
Status: DONE (scope fix), assignment monitoring latency dan platform.
Review nyata #1/#4 sesudah context fix selesai 2,86 / 2,69 detik. QA #1
gagal dalam 9,88 detik: suite Enter memakai fill `Enter Key Item\\n`, bukan
keyboard event. DSL lama tidak punya press walau UAC meminta Enter. Tambahkan
aksi press dengan daftar tombol terbatas, instruksi QA, kontrak dan actual
browser regression; jangan mengubah suite/receipt/approval lama. Runner digest
berubah sehingga wajib suite/target/QA baru. Repair #1 4/4, #4 3/3; AGENTS.md
mewajibkan keputusan pengguna untuk extension. Tidak otomatis menaikkan caps.
File: pipeline contracts/runtime, trusted acceptance runner, QA instructions,
tests/docs. Review: NOT_REVIEWED.
Hasil: DSL press dengan 12 tombol bernama, schema memuat daftar valid; trusted
runner memakai Playwright locator.press. Instruksi QA menjelaskan fill vs press.
Tes aktual (Mac, Homebrew Git pada PATH):
- `pytest tests/pipeline/test_contracts.py tests/pipeline/test_keyboard_acceptance.py
  tests/pipeline/test_review_context.py -q`: **40 passed**, 20,52 detik.
- Browser terisolasi fixture Enter: fill literal `\\n` failed dengan executed=1;
  press Enter passed dengan executed=1, assertion nama item dan input kosong.
  Suite/nonce berbeda, stale runner digest ditolak sebelum run. Tidak memakai
  model atau mengubah approval produk. `git diff --check` pass.
Handoff R8: diff contracts/runner/runtime, instruksi QA, docs, tests contracts
dan file baru `tests/pipeline/test_keyboard_acceptance.py`; NOT_REVIEWED.
Status demo: #1 repair 4/4 sesudah suite Enter yang salah; #4 repair 3/3 sesudah
review menolak file JSX yang masih import library test tidak terpasang. Review
menyebut package/lock memuat library itu, tetapi pembacaan aktual tidak menemukan
Vitest/testing-library dalam keduanya; import ada hanya di src/App.test.jsx.
Tidak mengulang developer tanpa keputusan pengguna untuk repair/budget. Histori,
kandidat dan hasil QA gagal tetap tersedia; bukan keberhasilan produk/UAT.

### Perbaikan konteks technical review — 2026-10-06
Status: DONE (scope fix). Assignment: pantau latensi/demo dan perbaiki
bug platform. Kandidat #1/#4 lolos repo gate, tetapi technical review gagal
`ContextTooLarge`: ~22.887/~22.586 token untuk full diff/lockfile/gates, limit 8.000.
Rencana: context review terpisah dibatasi 32.768 tanpa mengubah builder bersama,
scope/diff/evidence lengkap, ContextRefused/TooLarge menjadi permanent refusal
agar tidak membuang transient retry. Budget/repair/evidence produk tidak direset.
File: `app/agents/runtime.py`, `app/pipeline/runtime.py`, tes review/context,
docs keputusan. Review: NOT_REVIEWED. Retry operator sesudah fix memakai API
domain queue dan user authorization dari assignment ini, tanpa tambahan caps.
Hasil: context review per-call 32.768, builder chat/plan tetap 8.000. Scope,
full diff dan gate report tidak dipotong. Refusal context permanen, tidak
diklasifikasikan sebagai crash transient. Handoff R7: diff runtime structured/
pipeline, docs, `tests/pipeline/test_review_context.py` (baru), dan regression
large diff dalam product loop; independent review NOT_REVIEWED.
Verifikasi aktual di Mac (Homebrew Git pada PATH):
- `pytest tests/pipeline/test_review_context.py tests/agents/test_context.py
  tests/agents/test_runtime.py tests/pipeline/test_gates.py
  tests/pipeline/test_review_regressions.py -q`: **67 passed**, 7,00 detik.
- `pytest tests/pipeline/test_product_loop.py::test_actual_build_and_browser_pass_with_fake_model_cannot_advance_uat -q`:
  **1 passed**, 16,72 detik. Docker/build/browser nyata, model/driver FAKE;
  full diff >8.000 token diterima review, label fake tetap menahan UAT.
- Replay context kandidat immutable #1/#4 tanpa call model atau mutasi artefak:
  estimasi **24.264 / 24.266 token**, cap 32.768, kedua repo gate passed.
  Ini membuktikan context muat, bukan kualitas kandidat atau QA nyata.
Worker idle dihentikan SIGINT tertib sebelum memuat fix; review operator retry
menggunakan kandidat/evidence yang sama. Budget dan repair counter tetap.

### Perbaikan Node TAP bertingkat dan feedback tool — 2026-10-06
Status: DONE (scope fix). Assignment: investigasi demo berulang ditolak
walaupun tiga Node tests lulus, hingga repair limit tercapai. Evidence kandidat
`5c42754589e9446ea7c72e12a59b8f1e`: summary tests/pass=3, adapter lama hanya
menghitung satu result suite top-level. Rencana: hitung test bertingkat terpisah
dari suite, pertahankan pemeriksaan skipped/empty/summary/exit serta fingerprint
baseline; tampilkan gate saat run test dan arahkan argumen write ke patch_file.
File: `app/pipeline/{gates,runtime}.py`, instruksi developer, docs keputusan.
Worker dihentikan tertib agar retry tidak menghabiskan budget selama diagnosis.
Tidak reset usage, repair counter, candidate, atau evidence; empat slot dibatalkan
sesuai keputusan pengguna untuk demo satu slot. Review: NOT_REVIEWED.
Hasil pembacaan ulang artefak demo, tanpa mengubah receipt/evidence produk:
- stdout SHA `c75dfc4d...9639cf` (Node suite tiket #4): `passed`, discovered=3,
  executed=3, passed=3; ID ketiga test mencakup nama suite.
- stdout SHA `f0440763...cbc82c` (echo tiket #1): tetap `incomplete`, semua count 0.
- `git diff --check`: pass.
Pengguna memilih "yang paling safe dan terbaik" setelah ditawarkan tes dan satu
siklus tambahan. Tes regresi diizinkan; command API `repair-authorizations` #1
menambah limit 3 → 4, cycles tetap 3, blocker hilang, usage/evidence tidak direset.
File verifikasi: `tests/pipeline/test_gates.py` dan fixture stdout Node 22.20.0
`tests/pipeline/fixtures/node-tap-suite-passed.txt` (artefak demo asli tanpa secret).
Perintah aktual (apps/backend; Homebrew Git pada PATH):
- `pytest tests/pipeline/test_gates.py tests/onboarding/test_waivers.py
  tests/pipeline/test_review_regressions.py tests/pipeline/test_contracts.py -q`:
  awal 37 passed, 2 failed karena directive `# SKIP/TODO` memakai spasi sesudah #.
  Regex diperbaiki; recheck **39 passed**. Termasuk nested suite/test, ID per suite,
  gagal/skip/todo, duplicate, hook failure, echo, dan fingerprint baseline lama.
- `pytest tests/workers tests/domain/test_evidence.py tests/pipeline/test_scheduler.py -q`:
  **128 passed**, Mac, tidak membuktikan Linux/WSL sesudah perubahan.
- `pytest tests/pipeline/test_product_loop.py tests/pipeline/test_product_regressions.py -q`:
  **15 passed** dalam 173,23 detik; Docker/build/browser dengan model FAKE berlabel.
Total recheck akhir **182 passed**. Handoff R6: diff enam file tracked plus fixture
baru di atas; setup normal tidak berubah, worker dimuat ulang untuk membaca fix.
Pemetaan bukti: nested counts/ID dan failure metadata pada test_gates; fingerprint
waiver pada test_waivers; gate/removal/stale/cancel/repair pada product regressions;
model/tool/budget/cleanup tetap terfence pada workers/domain tests. Review: NOT_REVIEWED.
Known issues: gate repo hanya bukti minimum; browser QA tetap terpisah. Tidak
menyamakan tiga tes hijau dengan bukti UAC aplikasi. Model/pipeline belum diulang
dengan fix ini saat penutupan implementasi; hasil demo tetap menunggu run produk.
Tidak commit/push atau merubah receipt kandidat lama.

### Perbaikan bootstrap proyek baru — 2026-10-06
Status: DONE (scope fix). Pelaksana: Codex; assignment pengguna memperbaiki developer
yang berulang mencari source kosong dan gagal install karena lockfile belum ada.
Rencana: bootstrap lockfile React/Vite dari katalog pinned yang dibatasi, tool
developer dengan lease/izin tetap, konteks new-project dan QA selector contract,
tes install/build/commit dari empty base serta regresi existing/stale/security.
File relevan: `app/pipeline/{runtime,workspace}.py`, `contracts/bootstrap`,
`agents/{developer,qa}/instructions.md`, tests pipeline dan dokumentasi keputusan.
Budget run produk tidak direset/diperpanjang otomatis. Review fix: NOT_REVIEWED.
File hasil tambahan: `app/pipeline/bootstrap.py`, `contracts/bootstrap/react-vite/{package.json,package-lock.json}`,
`tests/pipeline/test_bootstrap.py`, `docs/decisions/pipeline.md`. Bootstrap tersedia
hanya untuk developer proyek new dengan accepted tree kosong; range, dependency
di luar katalog, workspace/override/peer/optional root ditolak. Lock masuk snapshot
attempt lewat broker terfence, lalu commit/diff dan dependency digest target. Tidak
ada source fitur yang disuntik ke accepted, npm host, atau egress kode target baru.
Verifikasi aktual (apps/backend, prefix PATH=/Users/23061535/homebrew/bin:$PATH):
- `./.venv/bin/python -m pytest tests/pipeline/test_bootstrap.py -q`: real Docker
  empty-base generate-lock/install/test/build/commit/rebuild passed; 12 tests passed,
  satu fixture existing gagal karena repo_ref belum diisi. Fixture diperbaiki.
- `./.venv/bin/python -m pytest tests/pipeline/test_bootstrap.py -q -k 'not install_build_test'`:
  terakhir **14 passed, 1 deselected**, termasuk existing/nonempty accepted lock
  preservation, revoked attempt, QA role denial, versi/source dependency di luar
  katalog dan root resolution overrides. Tes Docker pertama tidak diulang karena
  perbaikan berikutnya hanya pada fixture/tests existing; kode build tidak berubah.
- `./.venv/bin/python -m pytest tests/pipeline/test_scheduler.py tests/pipeline/test_contracts.py
  tests/pipeline/test_review_regressions.py tests/workspace/test_manifest.py tests/agents/test_tools.py -q`:
  **58 passed**. `./.venv/bin/python -m pytest tests/pipeline/test_product_loop.py -q`:
  **6 passed**, Docker/build/browser dengan model FAKE berlabel, bukan bukti provider nyata.
- `git diff --check`: pass. Worker pipeline direstart memakai konfigurasi yang sama.
Handoff R6: diff/file baru di atas, setup normal tidak berubah. Bootstrap memakai
katalog reference React/Vite 4 dependency langsung; dependency/stack tambahan belum
dikualifikasi. Linux/WSL dan inference developer nyata belum diulang sesudah fix.
Run Daftar Belanja stopped budget_exhausted tetap stopped, usage/evidence tidak
di-reset. Melanjutkan produk memerlukan budget extension terbatas dari pengguna;
ini tidak memblokir selesainya implementasi fix yang diminta. Review: NOT_REVIEWED.

### Catatan pengerjaan
Status: DONE. Pelaksana: Codex, 2026-10-05. Review: NOT_REVIEWED.
Rencana: scheduler pipeline berbasis ticket/job DB; lead plan/review dan QA plan terstruktur;
adapter Hermes dengan relay/reservation/input/resource ownership produk; workspace broker
terfence oleh lease DB; build/target/evidence immutable dan acceptance runner terpisah.
Uji fitur/seeded bug nyata, report kosong/skipped/palsu, stale scope/generation, recovery,
repair/budget caps serta waiver fingerprint spesifik. Tidak mulai DEV-011/012/013.
File rencana: `apps/backend/app/pipeline/**`, worker/agent hooks, `contracts/verification/**`,
`tests/pipeline/**`, konfigurasi contoh, keputusan pipeline dan handoff review.
Provider testing memakai izin model murah hingga total USD 10 dari sesi sebelumnya;
limit request/token tetap finite dan biaya dicatat. Belum commit/push.

**Tujuan:** tiket approved menghasilkan kandidat yang diuji, bukan klaim agent.

**Scope:** technical plan, developer execution, technical review, QA planning,
verification harness, evidence mapping ke UAC, serta loop repair.

**Acceptance criteria:**

- Scheduler menjalankan tahapan sesuai domain dengan context/tools tiap role;
  review ditolak atau QA gagal menghasilkan feedback dan bounded repair.
- Harness minimum DEV-005/006 dihubungkan ke production job/domain DB; otorisasi,
  generation, usage, dan input requests berasal dari persistence produk. Tidak
  ada scheduler/approval paralel dari manifest percobaan.
- Harness mencatat command, exit code, environment, fixture, log, checksum, dan
  screenshot/trace bila relevan. Model tidak menetapkan pass tanpa evidence.
- Acceptance suite tersimpan di luar mount yang bisa ditulis developer dan
  dijalankan terhadap kandidat immutable. Perubahan repo tests tetap direview.
- QA menunjuk immutable verification target dan suite digest. Acceptance E2E
  runner terpisah dari target; invocation/config/mandatory test IDs dikendalikan
  verification service. Target tidak bisa menulis authoritative report.
- Harness mencatat discovered/executed/passed/failed/skipped. Nol test, mandatory
  skipped/missing, report invalid/palsu, coverage otomatis hilang → incomplete/
  failed. Repo/unit gates yang mengimpor target tidak menggantikan E2E wajib.
- UAC otomatis dipetakan ke bukti; UAC manual menjadi checklist pengguna.
- Test fitur/bug memakai base pembanding bila relevan; regresi boleh green pada
  base dan kandidat. Environment failure dibedakan dari bug aplikasi.
- Kandidat sengaja rusak ditolak; hasil stale atau scope berubah tidak maju UAT.
- Default required checks pass. Waiver pengguna hanya menutup fingerprint failure
  baseline yang tepat; UAC tiket dan infrastructure failure tetap mandatory.
  Failure baru dengan total sama tetap gagal; waived tampil eksplisit.

**Verifikasi:** fitur nyata, seeded bug, script test kosong, skipped tests,
report palsu, regression, harness failure, scope revision, dan repair/run caps.
Uji baseline failure berubah serta production wiring; label fake tetap jelas.

### Hasil implementasi dan handoff

Implementasi: `apps/backend/app/pipeline/**`, `contracts/verification/**`,
`apps/backend/tests/pipeline/**`, `examples/dev010/**`, adapter transaksi persistence,
serta hook worker/ToolFacade/Hermes/workspace/supervisor. Konfigurasi contoh dan
instruksi ada di `docs/decisions/pipeline.md`; diff/file baru, mapping AC, cara
menjalankan, dan batas review ada di `docs/reviews/DEV-010-handoff.md`.

Pemetaan AC:

1. Scheduler job DB: technical plan → QA plan → developer → lead review → verification;
   `test_scheduler.py`, penolakan lead dan broken-browser loop membuktikan bounded
   repair/needs_human dengan cumulative usage.
2. Admission/lease/generation/input berasal dari product queue/domain; `test_admission.py`,
   checkpoint→reply→resume, revoked credential dan expired-owner recovery regressions.
3. Logs command/stdout/stderr/exit/env/checksums serta browser screenshot/trace disimpan
   sebagai artifact. Target/build/suite/fixture/migration/image identities immutable dan dipin.
4. QA DSL/suite supervisor tidak ter-mount ke developer; lead melihat repo-test diff;
   candidate gate menolak mandatory baseline test yang hilang.
5. Runner browser terpisah, network-none target, plan readonly runner-only, report
   stdout runner dengan exact nonce/target/suite/IDs; forgery/multiple frames ditolak.
6. Empty/skipped/missing/invalid counts dan missing automated coverage incomplete;
   repo test memakai flat Node TAP dan tidak menggantikan browser acceptance.
7. Dua UAC fitur/satu UAC bug terpetakan ke bukti nyata; manual UAC tetap checklist
   pengguna di domain/API/GUI yang sudah ada, tanpa approval model.
8. Feature/bug qualified: passed kandidat, failed base. Dua baseline-green regression
   cases membuktikan green base diperbolehkan untuk regression, ditolak untuk feature.
9. Broken candidate, stop/scope revision sesudah harness, revoked workspace, crash
   sesudah atomic publication serta recovery tidak menghasilkan stale UAT.
10. Exact user fingerprint waiver terlihat `waived`; same-count changed failure,
    test ID berbeda, incomplete dan infrastructure failure tetap ditolak.

Kualifikasi nyata OpenRouter + Hermes pinned dengan DB/domain/job produk:
`feature-05` mencapai UAT (24 model calls, 43 tools, 150.247 tokens, $0,0322168,
1 repair, 2 browser tests passed kandidat/failed base). `bug-01` mencapai UAT
(37 calls, 68 tools, 194.530 tokens, $0,068536, browser 1/1 dan repo tests 2/2).
Bug run melewati restart/checkpoint/input dan explicit extension +16 calls, memakai
32 calls GPT-4.1 mini dan 5 GPT-4.1; penggunaan lama tidak direset. Total enam
percobaan termasuk empat awal yang gagal **$0,12668808**, unknown kosong pada summary.
Evidence IDs/checksums/counts/suite/baseline ada di `docs/spikes/DEV-010-results.json`;
report authoritative/DB/artifact besar tetap private. Dua run berakhir UAT, tidak ada
approval UAT pengguna, release, export atau deployment. Fixture bootstrap bukan
onboarding repo existing/integrator produk.

Verifikasi final: WSL full suite `cd apps/backend; python -m pytest -q` **718 passed**,
Docker nyata, tanpa skip, 330,60 detik (termasuk seluruh **54** pipeline cases).
Pipeline standalone sebelumnya **53 passed** sebelum tambahan Stop; product regressions
**9 passed**, lalu seluruhnya tercakup run final. Dockerfile runner publik berhasil
dibangun dan dipakai test. Windows portable **551 passed, 9 skipped**, 70,19 detik
(workspace/runtime_spike/dua file product Docker-POSIX di-ignore, POSIX/symlink skip);
pipeline standalone **38 passed, 3 skipped**. Satu warning upstream Starlette/httpx.
`git diff --check`, compileall pipeline/adapter/contoh, `node --check` server serta
JSON/evidence-summary/secret-pattern checks lulus. Frontend tidak dijalankan ulang
karena tidak ada perubahan frontend/HTTP contract.
Container pipeline tersisa 0; sisa dari run test cleanup awal yang gagal dibersihkan
berdasarkan owner/mount run yang tepat, tanpa menyentuh container lain.

Batas: static React/Vite + flat Node TAP, stateless fixture/migrations none; trusted
internal static server 4173 `/`, belum lifecycle preview DEV-011. Cap request/tool/time/
token finite, belum USD cap otomatis. Qwen final belum qualified; percakapan PO/pilot
nyata tetap DEV-015. Rebuild image/config membutuhkan target/QA baru. Reviewer R6
independen belum dilakukan, status **NOT_REVIEWED**. Implementasi sudah staged;
catatan final backlog/handoff masih di working tree. Belum commit/push dan tidak
memulai DEV-011/012/013.

Review DEV-010 oleh Claude (Opus 5.5, 2026-10-05): **NEEDS_FIX** pada review awal, bukan penutupan R6. Pemisahan
runner/target, report authoritative, publikasi atomik, dan pemeriksaan ulang domain tidak ditemukan celah. Temuan,
direproduksi lalu diperbaiki reviewer atas instruksi pengguna: (P1) job pipeline berbagi budget key dengan chat PO
`revise`, sehingga pipeline mewarisi caps chat (8 call/120 detik) atau chat selama development ditolak `QueueError`.
Kini ada budget pool `pipeline` terpisah; reply lead mewarisi pool pengirim. (P3) penolakan lead/QA membatalkan job
yang mempublikasikannya; kini job diselesaikan atomik dengan marker completion. Regresi: 4 baru. Sesudah fix: WSL+Docker
722 passed, Windows 554 passed/12 skipped. Observasi O1–O6 (purpose QA menentukan diskriminasi base, tiket macet sesudah
QA incomplete, waiver signature sangat ketat, dll.):
[docs/reviews/DEV-010-review.md](./docs/reviews/DEV-010-review.md).

Recheck perbaikan Claude oleh Codex (2026-10-05): R010-01/02 diterima, tidak ada
blocker tambahan pada diff fix; O1–O6 tetap terdokumentasi dan R6 belum tertutup.
WSL/Docker pipeline+domain+workers+agents **394 passed**; review regressions dengan
tambahan pengaman extension **5 passed WSL**, **4 passed/1 skipped Windows**.
Extension mempertahankan spending pipeline tanpa menaikkan cap chat. Dua hasil
kualifikasi nyata dicocokkan dengan DB/artifact privat (usage, target/suite/counts/
coverage/checksums), tanpa provider requests baru. Pengguna mengotorisasi commit/push
setelah recheck; suite lengkap 722 tetap hasil run reviewer, bukan run ulang Codex.

## DEV-011 — Preview kandidat dan feedback UAT

### Perbaikan path socket preview macOS — 2026-10-06

Status: DONE (scope perbaikan preview demo). Path socket demo memakai temporary directory macOS yang
mencapai 105 byte dan ditolak sebelum container dibuat. Rencana: pilih fallback
`/tmp` jika path default melewati 100 byte; pertahankan direktori socket privat,
isolasi container, serta identitas build/QA. File: `app/preview/service.py`,
`docs/decisions/preview.md`. Buka ulang preview kandidat UAT melalui command yang
ada setelah worker memuat perbaikan. Tes regresi belum dijalankan.
Percobaan reopen menemukan error kedua: Docker Desktop macOS menolak chmod socket
dengan EINVAL. Server preview tepercaya di `contracts/verification/preview-server.cjs`
akan mengatur izin saat socket dibuat memakai umask, tanpa chmod sesudah listen.
Percobaan berikutnya membuktikan socket shared filesystem tidak dapat dihubungi
dari host (ConnectionRefusedError). `app/preview/proxy.py` dan `service.py` kini
memakai relay Node tepercaya via Docker exec hanya pada macOS; socket tetap di
tmpfs container tanpa jaringan. Guard host/credential/Origin, batas koneksi dan
cleanup tetap dipakai; Linux/WSL memakai transport Unix langsung.

Hasil aktual: reopen melalui API menghasilkan preview
`0e43482c389449a6b27038fcde9789ba` berstatus **ready**, error kosong. Target tetap
`b12ad96540f8e74fb1c4b076d14ebe933e114322666e24acef477ba912f0dac6`
untuk kandidat tiket #4 `0d25f19c42294e948bbd8569315a9b2e`; tidak rebuild atau
mengganti bukti QA. HTTP `http://localhost:5180/` 200 (385 bytes), asset JS
`/assets/index-D3BMSkHh.js` 200 (145656 bytes); container preview berjalan.
Worker dihentikan/restart secara tertib untuk memuat perubahan; job/history/usage
dipertahankan. `git diff --check` lulus. Tes regresi tidak ditambah/dijalankan
(permintaan saat ini diagnosis dan pemulihan demo); manual UAT belum dilakukan.
Keterbatasan: Linux/WSL belum diverifikasi ulang; throughput relay belum diukur.
Handoff: diff `preview/{service,proxy}.py`, `preview-server.cjs`, keputusan preview
dan log ini; perbaikan belum commit/push. Review independen: NOT_REVIEWED.

### Catatan pengerjaan
Status: DONE. Pelaksana: Claude (Sonnet 5.5), 2026-10-05. Review Codex menemukan bug dan memperbaikinya;
perbaikan menunggu re-review independen, R6 belum ditutup.
Rencana: tabel `previews` (migrasi 0005) sebagai antrean/state lifecycle yang dimiliki supervisor, terpisah dari
job execution; `PreviewService` di worker (start/stop/switch/reopen/reconcile, satu preview aktif) menjalankan
bundle build teruji dari artifact (digest dicocokkan dengan target) dalam container `--network none` dengan
`preview-server.cjs` pada unix socket, dan proxy TCP loopback `localhost:PREVIEW_PORT` milik supervisor
(target tanpa jaringan sama sekali, tidak ada host gateway); smoke health sebelum `ready`; API start/stop/query +
event `preview.*`; GUI di detail kandidat (status, target/build digest, fixture, link tab terpisah, checklist);
pin cleanup untuk artifact yang dipakai preview aktif; preview kandidat yang di-supersede ditutup.
File rencana: `apps/backend/app/preview/**`, `persistence/models.py` + `migrations/versions/0005_previews.py`,
`http/{application,queries,schemas}.py`, `contracts/api/**`, `contracts/verification/preview-server.cjs`,
`worker.py`, `apps/web/src/components/Ticket.tsx`, `tests/preview/**`, `tests/web-browser/**`,
`docs/decisions/preview.md`, handoff. Tidak mulai DEV-012/013. Belum commit/push.

Hasil: preview on-demand dari bundle build teruji (container `--network none`, server pada unix socket, proxy loopback
`localhost:PREVIEW_PORT` milik supervisor), satu preview aktif, switch/stop/reopen/recovery, preview kandidat yang
diganti ditutup, pin cleanup, API start/stop/get + event `preview.*`, panel GUI di detail kandidat. Pemetaan AC ke bukti,
perintah, dan known issues: [DEV-011-handoff](docs/reviews/DEV-011-handoff.md). Keputusan:
[preview](docs/decisions/preview.md).

Verifikasi: WSL+Docker suite backend lengkap 742 passed (sebelum satu tes tambahan; `tests/preview` 15 passed sesudahnya);
Windows 555 passed/14 skipped; GUI 30 passed; browser preview (API+PreviewService+Docker nyata) 4 passed; smoke 5 dan
browser DEV-008 3 passed. Uji mutasi `--network bridge` menggagalkan tes isolasi; eksperimen kontrol membuktikan host
`127.0.0.1` menerima cookie sedangkan `localhost` tidak. Keterbatasan: hanya stack statis stateless tanpa migrasi,
satu worker per host, tanpa HTTPS/VPS (DEV-017), QA fixture adalah contract fixture, tidak ada model/provider nyata.
Belum commit/push; DEV-012/013 tidak dikerjakan.

Review Codex 2026-10-05: verdict awal NEEDS_FIX, empat temuan diperbaiki atas instruksi pengguna.
R011-01 P1: proxy mentah menerima alias host kontrol dan credential; kini HTTP divalidasi sebelum socket target,
Host persis localhost, credential ditolak, dan satu request per koneksi. R011-02 P2: cleanup gagal saat start/recovery
meninggalkan starting tanpa retry; kini stopping dengan error, pin dipertahankan dan retry berjalan. R011-03 P2:
switch tetap memulai preview baru walau cleanup lama belum selesai; kini requested menunggu. R011-04 P2: error
transport inspect setelah remove dianggap container hilang; kini harus terbukti engine tersedia dan objek tidak ada.
10 regresi baru semuanya gagal di modul asli dari index dan lulus setelah fix. Browser ditambah navigasi alias host
dengan cookie kontrol nyata (403), dan hitungan container fixture dibatasi supervisor sendiri agar suite tidak saling
mengganggu. Verifikasi terkait WSL+Docker 215 passed; Windows HTTP/persistence 184 passed/2 skipped; build lulus,
GUI 30 passed, browser preview 4 passed. Suite backend lengkap WSL+Docker 753 passed tanpa skip (365.56 detik);
regresi review + lifecycle setelah penyesuaian shutdown terakhir 20 passed. Tidak ada container preview tersisa.
Laporan: [DEV-011-review](docs/reviews/DEV-011-review.md). Fix belum di-stage/commit/push; tidak ada panggilan model.

**Tujuan:** pengguna langsung mencoba tiket setelah QA.

**Scope:** satu preview aktif on-demand, lifecycle preview, fixture/data testing,
UI akses preview/checklist, feedback dan pencatatan persetujuan exact candidate.

**Acceptance criteria:**

- Target yang memenuhi gates/automated UAC dan smoke health tersedia untuk UAT,
  dengan manual checklist/waiver terlihat. Reopen menjalankan artefak teruji dengan
  config/fixture definition yang sama; container tidak wajib selalu hidup.
- Rebuild membuat build record/verification target baru dengan QA/UAT baru,
  walaupun SHA sama. Artefak hilang unavailable; reset fixture sesuai manifest
  bukan target baru, perubahan definisi/config yang memengaruhi UAC adalah target baru.
- Switch preview membersihkan resource miliknya; approval tidak bergantung pada
  proses preview yang terus hidup. Ownership terpisah dari job execution.
- Preview dan kontrol UI berbeda origin; kredensial/database testing terisolasi.
- Host berbeda sesuai §10, akses awal tab terpisah. Header capture memastikan
  target tidak menerima cookie kontrol; mutation ke kontrol ditolak dan jaringan
  target tidak dapat mengakses control plane/host gateway.
- Migration diperiksa dari kosong dan upgrade data base jika stack memerlukannya.
- Feedback memicu repair dan kandidat baru; accept mengacu candidate/scope,
  target/build digest, dan evidence IDs yang dilihat. Integrasi oleh DEV-012.
- Commit/build/evidence target dipin; switch/cleanup tidak menghapus artefak yang
  dirujuk approval/release atau kandidat aktif.
- Tiket independen tetap dapat dikerjakan selama pengguna melakukan UAT.

**Verifikasi:** start/stop/switch/reopen preview, health failure, feedback repair,
dan migration relevan; UI memakai target/evidence yang dicatat approval.
Uji rebuild SHA sama dengan dependency/config berubah, unavailable artifact,
pin cleanup, cookie header, dan request jaringan/mutation kontrol terlarang.

## DEV-012 — Integrasi accepted, dependency, dan recovery Git/DB

Review Codex selesai: verdict awal NEEDS_FIX, tiket sempat dibuka kembali IN_PROGRESS lalu DONE setelah fix.
R012-01: finalisasi/report/pin kini satu transaksi dengan evidence ID pada operasi. R012-02: urutan recovery memakai
approval immutable dan menunggu operasi pending pemilik ref. R012-03: ref accepted hilang menjadi blocker dengan
bukti. R012-04: recovery memeriksa base DB serta ancestry fast-forward. R012-05: provenance sebelum UAT tidak lagi
menjadi DTO operasi kosong. Sembilan regresi baru semuanya gagal sebelum fix masing-masing dan lulus setelahnya.
Verifikasi reviewer: integration/domain awal 118 passed, sesudah tujuh fix-regression 125 passed; integrasi final
24 passed (termasuk sembilan regresi). Suite terkait integration/domain/persistence/http/pipeline/workers WSL+Docker:
450 passed, 1 gagal pada flaky DEV-004 `test_dead_worker_recovery_revokes_workspace_keeps_logs_and_preserves_other_run`
(baris log spawn belum tertulis saat worker dibunuh); rerun terpisah 1 passed. GUI 31 passed, build lulus.
Suite backend lengkap dan Windows tidak diulang. Laporan [DEV-012-review](docs/reviews/DEV-012-review.md).
Patch reviewer menunggu re-review independen; R7 belum ditutup. Belum stage/commit/push patch reviewer.

### Catatan pengerjaan
Status: DONE. Pelaksana: Claude (Opus 5.5), 2026-10-05. Review Codex selesai, patch menunggu re-review independen.
Rencana: integrator milik supervisor (hook worker, bukan job execution) memproses operasi integrasi `pending` hasil
`accept_uat` di bawah lock proyek + lock ref broker: validasi ulang approval/target/evidence/base di domain, lalu
fast-forward `refs/heads/accepted` dengan compare-and-swap; finalisasi Accepted/event/dependency di DB sesudahnya.
Rekonsiliasi dari ref aktual: masih expected -> update; sudah target -> finalisasi saja; tip integrasi lain yang
diketahui DB -> kandidat basi kembali ke development; tip tak dikenal -> diblokir dengan bukti, tanpa reset/adopsi.
Base maju membuat kandidat tiket lain (technical review/QA/UAT) di-supersede dan developer me-rebase diff kandidat ke
tip baru (kandidat, review, QA, UAT baru). Revert kandidat upstream mencatat perubahan kontrak sehingga downstream
diblokir sampai revalidasi. Fault injection sebelum/sesudah update ref, accept ganda/bersamaan, ref divergence,
UAT base basi, penulisan ref tak berizin.
File rencana: `app/integration/**`, `domain/service.py`, `pipeline/{scheduler,workspace,wiring}.py`, `worker.py`,
`http/queries.py`, `contracts/api/types.ts`, GUI tiket, `tests/integration/**`, `docs/decisions/integration.md`,
handoff. Tidak mulai DEV-013/014. Belum commit/push.

Hasil: integrator supervisor (`app/integration/integrator.py`) dengan preflight domain, CAS fast-forward, rekonsiliasi
dari ref aktual (resume, recovered, stale base, blocked dengan bukti tanpa reset/adopsi), finalisasi yang men-supersede
kandidat base lama dan memproses revert, rebase diff kandidat oleh developer, job development per base, status
integrasi di API/GUI. Pemetaan AC, perintah, dan known issues: [DEV-012-handoff](docs/reviews/DEV-012-handoff.md).
Keputusan: [integrasi](docs/decisions/integration.md).

Verifikasi: WSL+Docker suite backend lengkap 768 passed; Windows 555 passed/15 skipped; GUI 31, preview browser 4,
smoke 5, browser DEV-008 3 passed; build lulus. Uji mutasi `_base_advanced` dan adopsi ref asing menggagalkan tes.
Keterbatasan: operasi blocked butuh operator, revalidasi dependency otomatis belum ada, kontrak otomatis hanya revert,
rebase via `git apply`, satu integrator per host. Belum commit/push; DEV-013/014 tidak dikerjakan.

**Tujuan:** fitur yang diterima menjadi base pekerjaan berikutnya secara konsisten.

**Scope:** internal accepted ref, antrean integrasi serial, lock proyek,
compare-and-swap ref, pending operation, dan penanganan kandidat stale base.

**Acceptance criteria:**

- Accept mempersist pending operation dengan expected/target SHA, kemudian
  fast-forward accepted ref secara bersyarat dan finalisasi DB/event/dependency.
- Validasi scope/candidate, verification target/evidence approval, current base,
  dan ownership integrator. Metadata Git tetap di supervisor; broker developer
  tidak dapat menulis accepted ref meskipun lease coding masih valid.
- Git dan SQLite tidak dianggap atomic; restart merekonsiliasi operation dari
  ref aktual. Divergence memblokir dengan bukti, tanpa blind reset.
- Accepted hanya dicatat setelah integrasi berhasil; dependency baru terbuka
  sesudah itu, bukan setelah QA pass atau klik accept saja.
- Base berubah saat UAT menghasilkan kandidat baru beserta technical review,
  QA, dan UAT baru. Approval lama tetap histori, tidak dibawa otomatis.
- Retry command tidak menggandakan integrasi; cancel/revision saat Integrating
  menunggu rekonsiliasi sebelum mutation lanjutan.
- Dependency pin accepted version/candidate/integration SHA dan contract state;
  perubahan kontrak terbaru memblokir downstream sampai revalidasi yang diperlukan.

**Verifikasi:** fault injection sebelum/sesudah ref update, accept berulang,
dua accept bersamaan, ref divergence, UAT stale base/build, unauthorized ref
write, dan revalidasi dependency setelah tiket perubahan/revert.

## DEV-013 — Onboarding repository existing

### Catatan pengerjaan

Status: DONE. Pelaksana/sesi: Codex, 2026-10-05. Review: REVIEWED untuk DEV-013 setelah review
Claude (Sonnet 5.5) dan recheck perbaikannya oleh Codex. Checkpoint R8 keseluruhan belum ditutup.
Rencana: onboarding persisten melalui job DB; import Git independen tanpa hooks/config/alternates sumber,
laporkan dirty status; patch hanya atas pilihan eksplisit dengan pin SHA/provenance. Validasi manifest,
instruksi dan batas stack; install/build/test/start baseline di sandbox; hubungkan API/GUI/context/pipeline.
File relevan: `app/onboarding`, `workspace/gitbroker.py`, `worker.py`, HTTP/contracts, GUI dan tests onboarding.
File hasil: `app/onboarding/{requests,source,runtime,__main__}.py`, workspace baseline builder,
worker registration, HTTP/contracts/GUI/context/lead plan, per-failure gate/waiver,
tests onboarding/HTTP/browser, `examples/dev013/{qualification,summary}.py`,
[onboarding decision](docs/decisions/onboarding.md), [handoff R8](docs/reviews/DEV-013-handoff.md)
dan [evidence summary](docs/spikes/DEV-013-results.json).
Verifikasi implementasi awal (sebelum perbaikan reviewer):
- WSL `python -m pytest tests -q`: 794 passed, 1 failed, 0 skipped (488.28s). Failure di
  `test_stop_terminates_an_inflight_browser_runner_without_waiting_for_its_timeout`: concurrent
  cleanup masih melihat container Docker sedang dihapus. Rerun test terpisah: 1 passed (4.37s).
  Tidak mengklaim full suite seluruhnya hijau; harness cleanup itu tidak diubah DEV-013.
- Final WSL `python -m pytest tests/onboarding tests/http/test_onboarding.py tests/agents/test_context.py -q`:
  38 passed (113.95s), termasuk Docker nyata, latest source/filter hardening, explicit patch,
  blocked/failed baseline, stale publication, crash rollback/replay, recovery ownership dan exact waiver.
- Source safety final `tests/onboarding/test_source.py`: 10 passed, termasuk local line-ending policy
  tanpa refresh index; context/source recheck sebelum itu 27 passed.
- `npm run build`: passed. `npx playwright test --config playwright.web.config.ts`: 32 passed (18.4s).
- `git diff --check`: passed. OpenAPI/requests regenerated; HTTP contract checks passed.
Evidence/keputusan: kualifikasi menggunakan source fixture dari kandidat terverifikasi DEV-010,
dirty working tree, Hermes/OpenRouter nyata. Fitur receipt → review → QA browser separate →
test-user UAT → production integrator Accepted. 14 model calls, $0,0176504 reported, usage unknown kosong.
Source inventory termasuk `.git`/refs/config/index/dirty files unchanged. QA kandidat 1/1 pass;
feature test gagal di base. Approval fixture bukan UAT manual pengguna dan bukan penutupan DEV-015.
Blocker/sisa: tidak ada AC implementasi tersisa menurut implementer. Batas stack static React/Vite,
public npm lockfile/flat Node TAP/migrations none, source Git lokal; partial import butuh inspeksi operator.
Full-suite Docker cleanup race awal telah diperbaiki reviewer; lihat laporan dan recheck di bawah.
Independent review DEV-013 telah dilakukan; checkpoint R8 keseluruhan belum ditutup.
Handoff: pemetaan keenam AC, diff termasuk files baru dan cara reproduksi ada di handoff; artifact
authoritative private tetap di WSL. Pengguna mengotorisasi commit/push sesudah recheck; tidak ada deployment.
DEV-014 adalah tiket berikutnya; tidak dimulai.

Review DEV-013 oleh Claude (Sonnet 5.5, 2026-10-05): **NEEDS_FIX** pada review awal, bukan penutupan R8. Isolasi sumber
(hook/fsmonitor/filter tidak berjalan, repo asli utuh, clone tanpa alternates) tidak ditemukan celah. Temuan, direproduksi
lalu diperbaiki reviewer atas instruksi pengguna: (P1) fingerprint waiver per failure tidak pernah cocok pada keluaran
`node --test` nyata (baris:kolom, frame internal Node, dan baris rencana `1..N` ikut dihitung; tes sintetis tidak
menangkapnya); (P1, harness DEV-010) pembersihan container bersamaan gagal "owned container remains" pada 18 dari 25
ronde nyata, penyebab kegagalan full suite yang dicatat handoff, kini 0/25 (juga di `PreviewService`); (P2) baseline
yang diblokir tidak dapat di-onboard lagi setelah sumber diperbaiki, kini import tak teraktivasi diarsipkan; (P2) daftar
perubahan lokal tak terbatas disimpan di baris proyek, kini dibatasi 200 entri dengan digest lengkap; (P3) dua modul tes
memutus koleksi suite Windows. Sesudah fix: WSL+Docker 804 passed, Windows 557 passed/20 skipped, GUI 32 passed. DEV-013
tetap **DONE**; saat handoff reviewer, perbaikan **menunggu re-review independen**. Detail dan observasi O1-O8:
[docs/reviews/DEV-013-review.md](./docs/reviews/DEV-013-review.md).

Recheck 2026-10-05 oleh Codex terhadap diff perbaikan Claude: **REVIEWED (DEV-013 saja)**; tidak ditemukan blocker
baru. Codex adalah implementer awal, tetapi bukan penulis lima fix tersebut; ini re-review fix reviewer, bukan klaim
independent review ulang seluruh implementasi oleh implementer. Tree final: WSL+Docker `python -m pytest tests -q`
**804 passed**, 0 failed/0 skipped (516,34s); Windows dengan dua direktori POSIX dikecualikan **557 passed, 20 skipped**
(75,82s); GUI **32 passed** (23,5s); `npm run build` dan `git diff --cached --check` lulus. Cleanup PreviewService
bersamaan diuji terpisah dengan Docker nyata: **8/8 ronde lulus**, tidak ada container milik percobaan tersisa.
Ringkasan kualifikasi cocok dengan DB/report privat; delapan artefak ter-pin lolos checksum dan ref accepted cocok
kandidat. Provider berbayar tidak diulang; approval fixture tetap bukan UAT manual. Fixtures TAP mempertahankan
spasi diagnostik asli melalui `.gitattributes` yang scoped ke dua file tersebut. O1-O8 tetap follow-up, R8 belum
ditutup, DEV-014 belum dimulai. Commit/push ke `origin/master` mengikuti instruksi pengguna setelah checks ini.

**Tujuan:** platform mendukung repo pengguna tanpa mengubah repo asli.

**Scope:** managed clone independen, deteksi baseline/toolchain, runner manifest,
repo instructions dalam policy, serta flow patch eksplisit untuk perubahan lokal.

**Acceptance criteria:**

- Clone memiliki refs/config/hooks sendiri dan tidak berbagi object alternates
  dengan repo sumber; worktree attempt hanya dibuat di managed clone.
- Dirty changes repo asli dilaporkan, tidak di-reset/stash/copy diam-diam;
  pengguna dapat memilih snapshot/patch eksplisit bila diperlukan.
- Install/build/test/start baseline diverifikasi; failure yang sudah ada dicatat
  terpisah, lalu lead menetapkan rencana kerja yang sesuai.
- Default required checks pass. UI dapat meminta waiver pengguna untuk failure
  baseline berdasarkan test ID/signature/environment/baseline SHA/scope. Agent
  tidak memberi waiver; failure baru, UAC tiket, dan infrastructure failure ditolak.
- Manifest dan instruksi repo divalidasi dalam batas izin; stack unsupported
  menunjukkan blocker konkret, bukan klaim dukungan semua stack.
- Satu fitur baru sampai QA/UAT/Accepted pada repo existing tanpa perubahan
  working tree, refs, atau konfigurasi repo asli.

**Verifikasi:** pakai repo hasil pilot sebagai sumber existing; bandingkan status,
refs, dan config sumber sebelum/sesudah, termasuk sumber dengan dirty changes.
Uji baseline waiver yang cocok dan failure baru dengan jumlah kegagalan yang sama.

## DEV-014 — Release yang dibekukan dan verifikasi gabungan

### Catatan pengerjaan
Status: DONE. Pelaksana awal: Claude (Sonnet 5.5), 2026-10-05. Review independen awal Codex: NEEDS_FIX; tujuh temuan diperbaiki dan diverifikasi. Fix reviewer menunggu re-review independen, bukan klaim penutupan R8.
Rencana: `POST /projects/{id}/releases` membekukan accepted tip dan scope (tiket Accepted yang belum masuk release
approved/exported sebelumnya) sebagai job `release` DB; selama freeze aktif integrator tidak menggeser tip (accept
baru tetap tercatat dan masuk release berikutnya). Runtime release (worker, Docker) membangun commit beku dalam
sandbox, menjalankan repo gate dan regression browser gabungan dari suite QA tiket yang termasuk pada satu target
release (build/config/toolchain/fixture identity), lalu menerbitkan draft release + receipt `release_verification`
(atau release `failed` bila regression gagal). Approval release (pengguna) mem-pin target/evidence dan checklist UAC
manual; tip beku boleh sudah dilewati tip baru (riwayat tip). Export lokal eksplisit (patch + git bundle, tanpa push);
export ditolak bila HEAD repo sumber berubah dari baseline onboarding; sinkronisasi gabungan membangun kandidat
pengganti (patch rilis diterapkan ke HEAD sumber baru) dengan diff, tiket terdampak, regression, dan approval baru.
Approved bukan deployed. API/GUI panel release, tes fault/restart.
File rencana: `app/release/**`, `domain/service.py`, `integration/integrator.py`, `workspace/supervisor.py`,
`onboarding/source.py`, `worker.py`, HTTP/contracts, GUI Releases, `tests/release/**`, `docs/decisions/release.md`,
handoff. Tidak mulai DEV-015. Belum commit/push.

Hasil: freeze accepted tip dan scope (integrator menahan tip selama job release), verifikasi gabungan pada satu target
(build bersih, repo tests, regression browser gabungan dari suite QA tiap tiket), draf release dengan receipt dan bukti
terpin atau `failed` bila regression gagal, approval pengguna yang mem-pin target/evidence dan checklist UAC manual,
tip beku tetap sah sesudah tip maju (riwayat tip), ekspor lokal patch + Git bundle tanpa push/PR/deploy (ditolak bila
repo sumber bergerak), sinkronisasi gabungan menjadi satu release pengganti dengan tiket terdampak dan checklist baru,
API/GUI tab Release. Pemetaan AC, perintah, dan known issues: [DEV-014-handoff](docs/reviews/DEV-014-handoff.md).
Keputusan: [release](docs/decisions/release.md).

Verifikasi: WSL+Docker suite backend lengkap 832 passed (sebelum dua tes tambahan; `tests/release tests/domain tests/http
tests/integration` 220 passed sesudahnya, termasuk 15 tes Git/Docker nyata di `tests/release`); Windows 572 passed/21
skipped; GUI 35 passed; browser preview 4, smoke 5, browser DEV-008 3 passed; build lulus. Uji mutasi: menghapus penahanan
integrator dan menghapus riwayat tip menggagalkan tesnya. Bug yang ditemukan tes: receipt sinkronisasi diparse dari semua
artefak verification termasuk screenshot biner (kini hanya laporan JSON). Keterbatasan: repo tests wajib lulus penuh pada
release, basis sumber setelah sinkronisasi hanya berlaku untuk release pengganti, tanpa tag/deployment, stack statis
stateless. Belum commit/push; DEV-015 tidak dikerjakan.

Review Codex 2026-10-05: tujuh fix mencakup regression seluruh fitur accepted saat freeze, ekspor patch onboarding dari source SHA, retry sinkronisasi setelah kandidat dibuat, review teknis manusia yang mem-pin dua diff, drift source saat ekspor, discard selama sync, dan eviction riwayat tip. Laporan: [DEV-014-review](docs/reviews/DEV-014-review.md). WSL backend 841 passed sebelum dua guard domain terakhir; WSL targeted 241 passed setelah guard discard sebelum fallback tip; final domain release/HTTP 17 passed di WSL dan Windows setelah seluruh edit. Windows full 573 passed/21 skipped, domain/HTTP 174 passed/2 skipped; GUI 36 passed; build, kontrak dan whitespace lulus. Paid provider/UAT manusia tidak dijalankan pada review. Angka implementer sebelumnya adalah catatan historis. Pengguna mengotorisasi commit/push dan lanjut DEV-015; checkpoint R8 tetap terbuka sampai bukti pilot serta review fix lengkap.

**Tujuan:** full release berasal dari accepted build yang benar-benar diuji.

**Scope:** release scope/milestone, freeze accepted tip, integration/regression,
manual checklist, approval release, dan artefak export lokal.

**Acceptance criteria:**

- Release mengunci scope dan SHA accepted tip; tiket Accepted setelah freeze
  masuk release berikutnya. Tidak memilih subset commit sembarangan dari tip.
- Bukti regression/integration dan UAC manual mengacu release verification target
  yang sama: SHA, build/config/toolchain/fixture identity, serta evidence IDs.
- Release approval berbeda dari scope/UAT, mem-pin target/evidence; rebuild atau
  build/config berubah membutuhkan target dan approval release baru.
- Export branch/patch tersedia sebagai tindakan eksplisit; push, PR, dan deploy
  tidak terjadi otomatis. Release approved tidak ditampilkan sebagai deployed.
- Jika base tujuan export berubah, revalidasi dan approval kandidat baru sesuai
  perubahan; jangan mengubah repo sumber pengguna otomatis.
- Sinkronisasi gabungan membuat satu release candidate/target pengganti dengan
  diff dan checklist UAC terdampak, technical review/QA/regression dan UAT gabungan
  pada approval release baru. Approval tiket lama tetap histori. Perubahan UAC
  harus melalui revisi/tiket baru dan scope approval sebelum release pengganti.

**Verifikasi:** freeze lalu menerima tiket lain, regression gagal, approval SHA
salah, perubahan build/config, export lokal, sinkronisasi gabungan/reapproval,
serta penyimpanan approval/target/evidence setelah restart.

## DEV-015 — Pilot end-to-end dan panduan operasional

### Catatan pengerjaan

Status: DONE. Pelaksana: Codex, 2026-10-05. Baseline `b12d11f` (DEV-014 telah push).
Rencana: backup/restore offline dengan inventory, validasi refs/artefak dan fencing recovery; pilot coffee fixture
melalui PO/provider dan pipeline nyata dengan caps/usage kumulatif; preview, feedback, dependency, repo existing,
restart, restore dan release; runbook, `docs/pilot-report.md` serta handoff AC. Approval eksperimen adalah test-user
eksplisit pada DB terisolasi dan tidak diklaim UAT manual pengguna. Tidak memulai DEV-016/017 atau deployment.
File: `app/recovery/**`, tests recovery/release, `examples/dev015/**`, instruksi QA, README, keputusan recovery,
runbook, pilot-report dan handoff. Asumsi produk: React/Vite stateless, fixture profil/menu/cart tanpa pembayaran nyata,
konfigurasi provider yang telah dikualifikasi, finite caps. Scope display menu diperjelas melalui revisi PO/test-user
setelah assertion QA ambigu dan lead menolak penghapusan harga; bukti gagal dan biaya scope lama dipertahankan.

**Tujuan:** membuktikan workflow utuh sebelum menambah visual dan hosting.

**Scope:** skenario coffee shop, tambah fitur pada repo existing, recovery drill,
setup provider murah, serta dokumentasi biaya dan batas dukungan.

**Acceptance criteria:**

- Alur nyata brief → review scope → approval → development → review → QA →
  preview → feedback → UAT → integrasi → release selesai dengan bukti.
- Profile/menu/transaksi memakai fixture; pengguna dapat mencoba per tiket,
  pekerjaan independen berlanjut, dependency menunggu accepted code.
- Chat PO tetap bekerja saat developer aktif; restart tidak kehilangan konteks,
  approval, atau artifact reference; stale attempt tidak menyelesaikan tiket.
- Kandidat stale base perlu QA/UAT baru dan repo asli existing tetap utuh.
- Percakapan PO, proposal, dan handoff antar-role nyata wajib dibuktikan di sini,
  termasuk hasil implementasi DEV-007 yang sebelumnya diverifikasi memakai fake.
- Run caps, waiting input/quota, target build baru dari SHA sama, suite kosong/
  skipped/report palsu, dan baseline waiver diuji dengan evidence sesuai skenario.
- Backup/restore lokal offline mengembalikan SQLite konsisten, Git objects/refs,
  build/evidence/context dan inventory di data root baru; validasi referensi,
  rekonsiliasi generation jobs, lalu reopen artefak yang sudah diuji. Cleanup
  tidak menghapus pin kandidat/approval/release; missing data unavailable.
- README/runbook memuat setup, start/stop, recovery, lokasi artefak, konfigurasi
provider, usage/biaya aktual yang tersedia, dan keterbatasan yang diketahui.
- Laporan `docs/pilot-report.md` membedakan automated checks, manual checks,
  hasil nyata, fake fixtures, serta blocker; demo palsu tidak menutup pilot.

**Verifikasi:** simpan evidence skenario dan hasil checks yang sudah relevan;
jangan menambah test yang sekadar menyalin detail implementasi.

Hasil DEV-015: tiga tiket fixture Accepted lewat provider/role nyata; release test-user approved pada tip
`5f999b802f273e16bc9a3a71891dff3975e067eb`, 9/9 browser + 2/2 Node tests, 13 UAC dan 472 pin diaudit.
Preview reopen sesudah offline restore tetap target yang sama; drill sesudah release memulihkan 585 file/472 pin,
target/build/evidence/approval/ref tetap dan tidak ada pin unavailable. Source fingerprint termasuk `.git` utuh.
Waiting/quota/stale lease/same-SHA/empty-skipped-forged/waiver memakai automated checks dengan fake/contract label;
paid quota outage dan manual UAT manusia tidak diklaim. Pemetaan seluruh AC: [handoff](docs/reviews/DEV-015-handoff.md).
File hasil juga mencakup `app/agents/runtime.py` (JSON Schema request/repair), `app/workers/queue.py` (authorized
idempotent retry), tests regresi, instruksi PO/QA/developer dan [receipt](docs/spikes/DEV-015-results.json).
Menu/transaksi memerlukan revisi scope, satu retry PO, satu repair tambahan serta dua bounded token extensions;
seluruh histori usage/caps/failure dipertahankan. Total 148 model calls, 281 tools, 982.335 tokens, reported USD
0,2141148 termasuk run wiring gagal; invoice belum dicocokkan. Tidak ada edit kode target/suite oleh operator.
Verifikasi: WSL full 853 passed sebelum schema fix; final agents/pipeline 219 passed sesudahnya; recovery/workers/
release 87 passed. Windows full 577 passed/22 skipped sebelum schema fix, final agents/retry 160 passed sesudahnya.
GUI 36 passed, TypeScript/Vite build lulus; rincian perintah/durasi dan batas di [pilot-report](docs/pilot-report.md).
Self-check temuan/fix: [DEV-015-review](docs/reviews/DEV-015-review.md), **NOT_REVIEWED independen, R8 OPEN**.
Instruksi pengguna sesudah review Claude: commit/push DEV-015 setelah recheck lulus; tanpa export/deployment repo fixture.
DEV-016/017 tidak dimulai.

Review Claude 2026-10-05: satu bug diperbaiki (R015-A) — snapshot tidak menyalin `onboarding-import.json`, sehingga
proyek dengan baseline diblokir tidak bisa di-onboard ulang sesudah restore; kini disalin + regresi
`test_a_blocked_onboarding_import_keeps_its_provenance_across_backup_and_restore`. Windows 578 passed/22 skipped,
GUI 36 passed, build dan `git diff --check` lulus, `tests/recovery` WSL 7 passed. Pilot berbayar tidak dijalankan
ulang; UAT manual dan outage quota nyata belum ada, **R8 tetap OPEN**. Laporan:
[DEV-015-review-claude](docs/reviews/DEV-015-review-claude.md).

Recheck fix Claude oleh Codex (2026-10-05): R015-A diterima, tidak ditemukan blocker baru.
WSL `/root/aiagent-dev002-venv/bin/python -m pytest tests/recovery tests/onboarding/test_source.py
 tests/release/test_recovery.py tests/workers/test_operator_retry.py -q`: **24 passed, 12,07s**.
Review kode DEV-015 oleh Claude selesai; perbaikan reviewer diverifikasi Codex. R8 tetap OPEN sesuai batas review:
pilot berbayar tidak diulang reviewer, UAT manual dan outage provider nyata belum diverifikasi.

## DEV-016 — Kantor Three.js dari aktivitas nyata

### Catatan pengerjaan

Status: DONE. Pelaksana: Codex, 2026-10-05. Baseline `c94b027` (DEV-015 committed/pushed).
Rencana: tambah kantor React Three Fiber yang memproyeksikan run, ticket dan message dari workspace snapshot/SSE yang ada; navigasi avatar ke konteks aktual, status reconnect, fallback WebGL, toggle animasi aksesibel; board tetap selalu tersedia. Tidak membuat lifecycle/status pekerjaan baru atau data simulasi.
File: `apps/web/src/features/office/**`, `apps/web/src/components/Workspace.tsx`, style, dependencies/lockfile, browser checks, backlog dan handoff.


**Tujuan:** empat soul terlihat bekerja dan berkomunikasi dalam kantor virtual.

**Scope:** React Three Fiber/Three.js scene, empat role, visual status, interaksi
ke detail/chat/run; board tetap akses utama bila visual tidak tersedia.

**Acceptance criteria:**

- Avatar/status/aktivitas membaca snapshot dan event backend; pesan yang dibuka
  adalah thread asli, bukan percakapan simulasi untuk dekorasi.
- Klik role/tiket membuka konteks terkait. Reconnect tidak menyisakan status basi.
- Ada fallback UI dan pengaturan animasi sederhana; scene tidak menghambat board.

**Verifikasi:** walkthrough run nyata, reconnect, dan browser tanpa WebGL.

Hasil: tab Kantor lazy-load scene Three.js empat role; status diturunkan dari board runs (active/waiting/failed/terminal),
aktivitas/thread dari messages nyata. SSE/reconnect dan snapshot tetap dikelola WorkspaceProvider authoritative yang sama;
kantor tidak punya scheduler/status setter. Avatar atau daftar aksesibel memilih role; panel memuat run, tiket/message terkait;
klik tiket membuka panel detail dan klik avatar memakai konteks pekerjaan. Board tetap di layar. Toggle animasi menghormati
`prefers-reduced-motion` secara default dan preferensi lokal; WebGL gagal menampilkan daftar role/board tanpa menghambat GUI.
Fake run diberi label. Stack: `@react-three/fiber` 8.18.0 untuk React 18, `three` + types dipin; tab dipecah sebagai async chunk.
File: `apps/web/src/features/office/{Office,projection}.tsx`, Workspace, style, deps/lockfile, browser tests.
Pemetaan AC dan cara review: [handoff DEV-016](docs/reviews/DEV-016-handoff.md). Build TypeScript/Vite lulus (initial bundle
214,48 KB; lazy Office 891,60 KB minified/239,53 KB gzip, peringatan >500 KB; hanya diunduh saat tab dibuka).
Browser `npx playwright test -c playwright.web.config.ts`: **39 passed** (termasuk API/worker FakeProvider nyata SSE/reload,
klik avatar WebGL, perubahan event ke status baru, fallback saat WebGL dimatikan). Verifikasi ada fake/contract fixtures;
tidak mengklaim provider/model nyata. Review kode Claude: **REVIEWED** setelah Codex memeriksa fix R016-A;
checkpoint **R9 tetap OPEN** menunggu walkthrough provider nyata dan uji GPU/mobile. DEV-017 berikutnya.

Review Claude 2026-10-05: AC terpenuhi; satu perbaikan efisiensi (R016-A, render on-demand saat semua role idle). Codex memeriksa fix dan
mem-pin dependency baru serta memindahkan `@types/three` ke devDependencies. Verifikasi ulang dicatat di review; probe open/close tab tanpa leak.
Walkthrough provider nyata dan uji GPU/mobile tidak dilakukan; R9 belum ditutup.
Laporan: [DEV-016-review-claude](docs/reviews/DEV-016-review-claude.md).

## DEV-017 — Packaging satu VPS

**Tujuan:** aplikasi dapat bekerja saat laptop pengguna mati, bila dipilih.

**Scope:** konfigurasi satu host, volume persisten, reverse proxy/TLS, login,
preview origin, secrets, backup/restore, health checks, dan restart policy.

**Acceptance criteria:**

- Deployment assets/documentation tersedia; SQLite, artifacts, dan managed
  repos persisten. Supervisor tepercaya tetap terpisah dari sandbox target.
- Kontrol UI dan preview menggunakan origin berbeda dengan akses sesuai policy;
  endpoint kontrol tidak dibuka tanpa autentikasi.
- Backup/restore dan restart policy diverifikasi pada environment uji;
  batas resource dan biaya hosting didokumentasikan dari pilihan aktual.
- Gunakan kembali backup/retention lokal DEV-015, ditambah routing/session HTTPS
  §10, host control/preview berbeda, cookie kontrol __Host-/Secure/HttpOnly/tanpa
  Domain, preview credential tersendiri, Origin/CSRF dan network policy.
- Local mode tetap bekerja. Menyusun packaging tidak otomatis menyewa atau
  melakukan deployment ke VPS; tindakan tersebut mengikuti instruksi pengguna.

**Verifikasi:** packaging smoke check dan restore data uji. Pengujian VPS aktual
dicatat terpisah jika host/kredensial belum tersedia.

## Format catatan pengerjaan

### Perluasan DSL browser QA dan diagnosis kontrak — 2026-10-07

Status: DONE (implementasi 2026-10-07). Review: NOT_REVIEWED.
Rencana: audit operasi DSL, tambahkan select/checkbox/radio, state/attribute/text
assertions, navigasi internal, fixture upload dan download/CSV yang dibatasi.
Pisahkan operasi tes yang salah dari bug aplikasi, koreksi fill-select hanya
dengan bukti DOM spesifik dan target baru. Sertakan capability pada planning.
Pertahankan digest suite lama, identitas target/runner, bukti dan approval.
File relevan: contracts acceptance runner, pipeline contracts/harness/runtime/
qa_repair, instruksi agent, dokumentasi. Verifikasi statis; tes belum diminta.
Sesudah implementasi restart API/web/worker; tidak melanjutkan siklus repair
proyek yang sudah diblokir tanpa authorization baru.
Hasil: 28 action tervalidasi dan didispatch runner; select/checkbox/radio/state,
attribute/text, internal navigation, text upload, download exact text/CSV dan
native dialog didukung. Salah kontrol diberi action_contract. Koreksi fill-select
memerlukan bukti satu visible/enabled select dan satu opsi enabled dengan nilai
persis, target/suite baru dan eksekusi penuh; tidak menghasilkan pass langsung.
Serialisasi field baru yang kosong tidak menambah key pada suite legacy.
Runner identity mismatch ditandai infrastruktur tanpa application repair.
File hasil tambahan: `apps/web/src/components/Activity.tsx`,
`docs/decisions/{pipeline,qa-browser-dsl}.md`; instruksi QA/TL/developer selaras.
Verifikasi aktual: AST 4 file Python, import/schema 3 modul, inlined tool schema,
coverage dispatcher statis 28/28, TypeScript --noEmit, dan git diff --check lulus.
Tes regresi/browser/Docker/provider dan review independen belum dilakukan;
hasil operasional DSL baru belum terbukti. Tidak menambah/menjalankan tes.
Regresi lanjutan: digest suite lama, select single/multiple/by-label, radio/check,
wrong/absent control, mixed failure/no unsafe repair, quote/newline/BOM CSV,
disabled export, upload fixture/no host path, dialogs, URL isolation, target pins.
Handoff dan daftar keterbatasan tersedia di keputusan DSL browser.
Restart aktual: API health ok, frontend HTTP 200, worker pipeline siap; mode
unlimited demo tetap aktif. Tiket Kas #1 tetap needs_human pada batas repair.

### Perbaikan konteks TL dan bootstrap dependency — 2026-10-07

Status: DONE (implementasi 2026-10-07). Review: NOT_REVIEWED.
Rencana: generator lockfile mempertahankan hanya dependency yang dapat dijangkau
dari manifest; konteks TL menyertakan runner saat planning, kecocokan root lock,
bukti install/build dari kandidat, dan kontrak selector QA saat review.
File relevan: `app/pipeline/{bootstrap,review_context,runtime}.py`, instruksi TL,
dan `docs/decisions/pipeline.md`. Approval, kandidat lama, batas repair dan
histori proyek tidak diubah. Setelah implementasi: pemeriksaan statis,
commit/push, lalu restart API/web/worker sesuai instruksi pengguna.
Tes regresi belum diminta, sehingga tidak ditambah/dijalankan.
Hasil: closure lock dibatasi dependency yang dipilih; hash integrity katalog tetap.
Planning menerima runner; review menerima root manifest/lock, command receipts
install/build dan suite selector yang dipin target. Tidak membuat approval baru.
Verifikasi aktual: AST dan import 3 modul Python lulus; `git diff --check` lulus.
Keterbatasan: npm ci untuk lock hasil pruning baru, tes regresi/browser/provider
belum dijalankan; keberhasilan demo setelah perubahan belum dibuktikan.
Handoff/diff: 3 modul Python, instruksi TL/developer, backlog dan keputusan pipeline.
Tiket Kas yang mencapai batas repair tetap needs_human; restart tidak menghapus
blocker atau riwayat dan bukan persetujuan siklus tambahan.

Ubah status pada tabel dan tambahkan catatan di bagian ini per tiket. Jika ada
beberapa AI/percakapan, jangan mengerjakan tiket `IN_PROGRESS` yang sama tanpa
handoff; catat siapa/sesi mana yang mengerjakan. Backlog Markdown belum memiliki
lock transaksi, jadi koordinasikan assignment lewat pengguna atau orchestrator.

```text
### DEV-xxx — Catatan
Status: IN_PROGRESS / BLOCKED / DONE
Pelaksana/sesi: ...
Rencana singkat: ...
File hasil: ...
Verifikasi: perintah + hasil aktual / belum dijalankan + alasan
Evidence/keputusan: path laporan, log tersanitasi, atau keputusan penting
Blocker/sisa: ...
Handoff: konteks untuk pelaksana berikutnya
Review: NOT_REVIEWED / NEEDS_FIX / REVIEWED + checkpoint/reviewer/scope bukti
```

## Prompt untuk AI pelaksana

```text
Baca AGENTS.md, DEVELOPMENT-WORKFLOW.md, dan spesifikasi yang dirujuk.
Kerjakan DEV-001 di IMPLEMENTATION-BACKLOG.md sampai acceptance criteria terpenuhi.
Periksa kondisi repository aktual, implementasikan scope tiket, lalu jalankan
verifikasi yang sesuai. Update status dan catatan pengerjaan dengan file hasil,
perintah/hasil verifikasi, pemetaan AC-001-01–07 ke bukti, serta blocker bila ada.
Siapkan handoff code review R1, termasuk diff/file baru dan known issues.
Jangan mulai tiket berikutnya di luar perubahan pendukung yang diperlukan.
```

Untuk sesi berikutnya, ganti ID dengan tiket yang dependency-nya sudah `DONE`.
Jika ingin pekerjaan berlanjut otomatis, instruksikan eksplisit untuk melanjutkan
backlog sesuai dependency dan mencatat handoff tiap tiket.

## Roadmap yang belum dijadikan tiket implementasi

Connector Jira/Trello, banyak developer paralel, beberapa preview bersamaan,
remote runner, mobile runner, model lokal, embeddings/vector database, dan
multi-host deployment. Rincikan setelah pilot menunjukkan kebutuhan konkret.
