# AI Software Development Team

Skeleton lokal untuk platform AI Software Development Team. MVP ini berisi
frontend React, health API FastAPI, dan worker terpisah dengan antrean job persisten
(DEV-004). Runtime agent nyata belum di-wire ke worker (DEV-010).

## Toolchain yang digunakan

- Node.js `22.20.0` dan npm `10.9.3` (terdeteksi saat DEV-001, 4 Oktober 2026)
- Python `3.11.6`
- Git `2.56.0`
- Dependensi frontend dipin di `package.json` dan `package-lock.json`.
- Dependensi backend dipin di `apps/backend/requirements.lock` (requirements
  dasar ada di `apps/backend/requirements.txt`).

Versi di atas adalah environment implementasi saat ini. Gunakan Node.js 20+
dan Python 3.11+ untuk checkout lain.

## Persiapan

```sh
npm ci
python3 -m venv apps/backend/.venv
apps/backend/.venv/bin/python -m pip install -r apps/backend/requirements.lock
```

Windows: gunakan `apps/backend/.venv/Scripts/python` sebagai pengganti path
`.venv/bin/python`.

Salin `.env.example` menjadi `.env.local` bila ingin mengganti konfigurasi.
Web dan API membaca file itu dari root checkout. Environment proses memiliki
prioritas tertinggi, lalu `.env.local`, lalu `.env`. Vite juga mengikuti file
mode seperti `.env.development.local`; gunakan `.env.local` untuk konfigurasi
bersama API. Restart layanan setelah mengubah konfigurasi.
File contoh berisi default lokal non-secret. Hanya variabel berawalan `VITE_`
yang diekspos ke frontend; jangan menaruh secret di sana.

## Build frontend

```sh
npm run build
```

## Menjalankan layanan lokal

Jalankan tiap proses pada terminal terpisah dari root checkout.

```sh
# Terminal 1: API pada 127.0.0.1:8000
cd apps/backend
./.venv/bin/python -m app

# Terminal 2: worker terpisah (perlu database: python -m app.persistence upgrade)
cd apps/backend
./.venv/bin/python -m app.worker

# Terminal 3: web pada 127.0.0.1:5173
npm run dev:web
```

Buka <http://127.0.0.1:5173>. API health: <http://127.0.0.1:8000/health>.
Web dan API bind ke `127.0.0.1`; konfigurasi host lain ditolak untuk control
plane lokal. Port dapat diubah melalui `WEB_PORT` dan `API_PORT`;
sesuaikan `VITE_API_BASE_URL` dengan port API. Jika `CORS_ORIGINS` diisi,
sesuaikan dengan exact origin web; jika tidak diisi, API memakai origin
`WEB_PORT` dan `WEB_PREVIEW_PORT`. Port yang terpakai membuat Vite gagal start,
bukan bergeser diam-diam. `localhost` disisihkan untuk preview aplikasi hasil
agent di tahap berikutnya.

Halaman memeriksa health berkala, dengan timeout 3 detik; pada tab aktif,
perubahan status biasanya terlihat dalam 2–5 detik tanpa reload.
Untuk mencoba production build control UI, jalankan `npm run build` lalu
`npm run preview:web` (default `127.0.0.1:5174`, dapat diubah lewat
`WEB_PREVIEW_PORT`). Jalankan API juga.

Worker/API berhenti dengan Ctrl+C (SIGINT); SIGTERM juga ditangani worker.
Tidak dibutuhkan provider key, Hermes, container engine, database, atau VPS.

## Regression smoke tests

```sh
npx playwright install chromium
npm run test:smoke
```

Test menjalankan web pada `127.0.0.1:19832` dan memock health API untuk menguji
koneksi putus/pulih tanpa reload, startup tertunda, dan respons tidak valid.
Ini menguji skeleton UI, bukan QA aplikasi hasil agent. Bukti review API dan
worker aktual ada di `docs/reviews/DEV-001-R1.md`.

## Workspace Git dan sandbox (DEV-005)

Paket `apps/backend/app/workspace/` adalah harness standalone: managed repo
Git, worktree per attempt, snapshot sumber tanpa `.git`, Git broker, sandbox
Docker, dan tool broker dengan otorisasi per run. Ini bukan scheduler produk;
run spec, generation, dan lease disuplai pemanggil dan dipersist di
`workspaces/` (gitignored). Wiring ke DB/job produk ada di DEV-010.

Prasyarat tes sandbox: Git tersedia pada PATH (toolchain diuji: 2.56.0), Docker berjalan dan image
terpin ada secara lokal (image tidak pernah ditarik otomatis oleh kode).
Harness workspace memakai `flock`/`dir_fd`: memerlukan macOS/Linux, belum
mendukung Windows. Verifikasi container sejauh ini dilakukan di macOS/Docker
Desktop dan Ubuntu WSL (103 passed/0 skipped pada setup DEV-006). Jalankan perintah berikut dari root checkout.

```sh
docker pull node:22.20.0-alpine
apps/backend/.venv/bin/python -m pip install -r apps/backend/requirements-dev.txt
git --version
apps/backend/.venv/bin/python -m pytest apps/backend/tests/workspace -m "not docker"
apps/backend/.venv/bin/python -m pytest apps/backend/tests/workspace
```

Tes `docker` di-skip dengan alasan jika daemon/image tidak ada. Tes
`test_reference_target.py` menjalankan install/build/test/smoke React/Vite nyata.
Semua container target, termasuk install, menggunakan `--network none`.
`allow_install_egress` hanya mengizinkan supervisor mengunduh tarball HTTPS dari
`registry.npmjs.org` sesuai package-lock v2/v3 dan memverifikasi SHA-512-nya.
Redirect dan proxy environment tidak dipakai. Npm kemudian mengisi cache dan
menjalankan `npm ci --offline --ignore-scripts` di container tanpa jaringan.
Untuk MVP, private registry, dependency Git/local, project `.npmrc`, dan override
environment runner belum didukung; input tersebut ditolak. Supervisor memerlukan
akses HTTPS ke registry untuk test referensi.

Stop dan renewal mencabut generation sebelum menunggu command selesai;
evidence command dan snapshot perubahan yang belum di-commit diarsipkan sebelum
cleanup. Smoke memakai artefak build yang dipin dan di-mount read-only, dengan
verifikasi digest dan image ID. Hasil review: `docs/reviews/DEV-005-R2.md`.

Batas yang masih ada: bind mount belum mempunyai hard disk quota (ukuran/jumlah
entry dibatasi saat sinkronisasi), lease expiry/heartbeat menunggu DEV-004/010,
dan recovery crash memerlukan `reap_orphans` setelah status/generation run
direkonsiliasi. Harness ini belum merupakan scheduler produk.

## Spike runtime nyata (DEV-006)

DEV-006 **DONE**, R3 **REVIEWED** (Claude, dikonfirmasi pengguna).
Catatan review: [DEV-006-R3](docs/reviews/DEV-006-R3.md). Runtime pilihan: Hermes embed
`AIAgent` subprocess dengan broker/relay supervisor dan journal scoped. Integrasi
DB/job produksi menunggu DEV-010. Eksperimen Qwen/OpenRouter menghasilkan fitur
keranjang dengan 12/12 target tests, 4/4 browser acceptance, seeded total bug
tertangkap, dan walkthrough operator pada artefak teruji. Preflight
standalone membaca pin Hermes, konfigurasi provider/model, host POSIX, engine
Docker Linux, image lokal dan instalasi source yang cocok. Exit 0 hanya berarti
prasyarat terdeteksi; bukan bukti kompatibilitas, QA atau UAT. Tidak ada model
call, instalasi otomatis, atau import kode target dalam CLI ini.

```sh
cd apps/backend
.venv/bin/python -m unittest tests.runtime_spike.test_preflight -v
.venv/bin/python -m app.runtime_spike.preflight --env-file ../../.env.local --report ../../data/dev006/preflight.json
```

Windows memakai `.venv/Scripts/python.exe`; preflight boleh berjalan di Windows,
tetapi harness workspace tetap harus dijalankan di Linux/macOS. Pada mesin sesi
ini Docker Desktop/WSL/Ubuntu sudah aktif setelah reboot, Hermes dipasang di
venv Linux, dan preflight WSL lulus. Tests workspace di WSL: 103 lulus;
runtime_spike checks: 40 lulus. Bukti nyata model/caps/stop/recovery dan batasannya
dicatat terpisah dari prerequisite checks.
Provider key hanya pada file lokal yang gitignored atau environment supervisor;
contoh nama variabel ada di `.env.example`. CLI membaca file hanya jika
`--env-file` diberikan dan tidak menginterpolasi isinya.

Hasil, reproduksi, pemetaan AC dan handoff R3:
[docs/spikes/DEV-006.md](docs/spikes/DEV-006.md),
[manifest hasil](docs/spikes/DEV-006-results.json), dan
[keputusan runtime](docs/decisions/runtime.md). CLI eksperimen/verification/preview
berada di `app.runtime_spike`; suite browser terpisah di `contracts/dev006/`.
Key, journal, homes, managed Git dan artefak tetap pada runtime data gitignored.

## Database lokal (DEV-002)

Persistence memakai SQLite (WAL) lewat SQLAlchemy dan Alembic. Database dan artefak berada di
direktori `data/` yang di-ignore Git (`DATA_DIR`, `DATABASE_PATH`, `ARTIFACT_DIR` di
`.env.example`). API dan worker belum membukanya, jadi keduanya tetap bisa start tanpa database;
command domain tersedia pada DEV-003, wiring worker/API menyusul pada DEV-004/008.

```sh
cd apps/backend
./.venv/bin/python -m app.persistence upgrade   # membuat/memigrasikan database
./.venv/bin/python -m app.persistence check     # revisi, drift skema, integrity, foreign key
./.venv/bin/python -m pytest tests/persistence -q
```

Gunakan `--db PATH` untuk database lain. Database harus berada di filesystem lokal yang mendukung
WAL (engine menolak start bila tidak). Rancangan, aturan yang ditegakkan storage, dan batasnya:
[docs/decisions/persistence.md](docs/decisions/persistence.md).

## Workflow dan approval (DEV-003)

`apps/backend/app/domain/Workflow` menyediakan command berizin untuk scope/proposal,
batch approval atomik, dependency pin/revalidasi, candidate/review/QA/UAT,
integration receipt, release approval, waiver baseline, cancel dan repair limit.
Jalankan migrasi ke head `0002` sebelum memakainya. Belum diwire ke API/GUI/worker;
Git integration nyata adalah DEV-012.

```sh
cd apps/backend
./.venv/bin/python -m pytest tests/domain tests/persistence -q
```

Actor dibuat authentication/supervisor tepercaya, bukan role dari request klien.
Kontrak receipt, izin, pemetaan AC, cara menjalankan, dan batas implementasi:
[docs/decisions/workflow.md](docs/decisions/workflow.md).

## Worker dan antrean job (DEV-004)

`python -m app.worker` sekarang menjalankan supervisor job persisten: claim/lease/generation di
database, lane interaktif dan satu slot execution, heartbeat, retry terbatas, waiting input/quota,
pembatalan yang menghentikan process group, dan recovery setelah worker mati. Database harus sudah
di revisi terbaru (`python -m app.persistence upgrade`).

```sh
cd apps/backend
./.venv/bin/python -m app.worker                 # tanpa runtime: hanya rekonsiliasi lease/quota
./.venv/bin/python -m app.worker --runtime fake  # dry run berlabel FAKE, bukan bukti QA/provider
./.venv/bin/python -m pytest tests/workers -q
```

Supervisi proses memerlukan host POSIX; di Windows jalankan worker di WSL. Runtime Hermes nyata
di-wire pada DEV-010. Rancangan dan batasnya:
[docs/decisions/workers.md](docs/decisions/workers.md).

## Agen, konteks, dan model (DEV-007)

Empat peran (PO, technical lead, developer, QA) punya `agents/<role>/SOUL.md` dan `instructions.md`.
`app/agents/` menyediakan tool per peran dengan otorisasi dari identitas run, model client per role
(timeout, usage/cost, redaction secret), context builder dengan batas token dan snapshot/hash, thread
antar-agent dengan input request yang idempotent, serta runtime terstruktur PO/lead di atas worker.

```sh
cd apps/backend
./.venv/bin/python -m pytest tests/agents -q          # provider FAKE berlabel, bukan model nyata
./.venv/bin/python -m app.worker --runtime structured  # PO/lead lewat agents/models.json (belum diverifikasi nyata)
```

Model per role: salin `agents/models.example.json` ke `agents/models.json` (gitignored). Key hanya dari
environment variable yang disebut berkas itu. Rancangan dan batasnya:
[docs/decisions/agents.md](docs/decisions/agents.md).
