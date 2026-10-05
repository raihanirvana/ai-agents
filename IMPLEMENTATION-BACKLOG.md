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
| DEV-013 | Onboarding repository existing | DEV-005, DEV-012 | TODO |
| DEV-014 | Release yang dibekukan dan verifikasi gabungan | DEV-012, DEV-013 | TODO |
| DEV-015 | Pilot end-to-end dan panduan operasional | DEV-009, DEV-011, DEV-013, DEV-014 | TODO |
| DEV-016 | Kantor Three.js dari aktivitas nyata | DEV-015 | TODO |
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

## DEV-007 — Soul, context, model client, dan pesan antar-agent

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

## DEV-016 — Kantor Three.js dari aktivitas nyata

**Tujuan:** empat soul terlihat bekerja dan berkomunikasi dalam kantor virtual.

**Scope:** React Three Fiber/Three.js scene, empat role, visual status, interaksi
ke detail/chat/run; board tetap akses utama bila visual tidak tersedia.

**Acceptance criteria:**

- Avatar/status/aktivitas membaca snapshot dan event backend; pesan yang dibuka
  adalah thread asli, bukan percakapan simulasi untuk dekorasi.
- Klik role/tiket membuka konteks terkait. Reconnect tidak menyisakan status basi.
- Ada fallback UI dan pengaturan animasi sederhana; scene tidak menghambat board.

**Verifikasi:** walkthrough run nyata, reconnect, dan browser tanpa WebGL.

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
