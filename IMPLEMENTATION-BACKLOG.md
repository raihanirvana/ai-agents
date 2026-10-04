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
| DEV-006 | Spike runtime nyata dan keputusan adapter | DEV-005 | TODO |
| DEV-002 | Persistence, migrasi, dan event | DEV-001 | TODO |
| DEV-003 | Domain tiket, versi scope, dan approval | DEV-002 | TODO |
| DEV-004 | Worker persisten, dua lane, dan recovery | DEV-003 | TODO |
| DEV-007 | Soul, context, model client, dan pesan antar-agent | DEV-002, DEV-004 | TODO |
| DEV-008 | API aplikasi, autentikasi lokal, dan SSE | DEV-003, DEV-004, DEV-007 | TODO |
| DEV-009 | GUI board, chat PO, dan review scope | DEV-008 | TODO |
| DEV-010 | Pipeline lead/developer/QA dengan bukti test | DEV-003, DEV-006, DEV-007 | TODO |
| DEV-011 | Preview kandidat dan feedback UAT | DEV-008, DEV-009, DEV-010 | TODO |
| DEV-012 | Integrasi accepted, dependency, dan recovery Git/DB | DEV-005, DEV-010, DEV-011 | TODO |
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

## DEV-011 — Preview kandidat dan feedback UAT

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
