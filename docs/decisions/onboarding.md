# Onboarding repository existing — DEV-013

Implementasi memakai job produk `onboarding` pada lane execution. API menerima intent pengguna
dan mempersist job/event/receipt secara atomik; worker membaca sumber dan menjalankan baseline.
Tidak ada schema DB baru: metadata ada pada `projects.workflow`, job pada `jobs`, laporan dan
log pada artifacts yang dipin attachments messages. Status implementasi dan review dicatat pada
backlog/handoff; onboarding suatu proyek adalah state produk yang terpisah.

## Import dan patch

Sumber adalah **working tree Git lokal** pada host worker, bukan URL remote. Inspeksi mencatat
HEAD SHA, status porcelain (staged/unstaged/untracked; daftar tampilan dibatasi 200 entri dengan total dan digest
lengkap untuk perbandingan sebelum/sesudah), digest refs dan config. `GIT_OPTIONAL_LOCKS=0`
menahan refresh index. Git memakai environment/config supervisor: hooks, fsmonitor, external
attributes dan global/system config dinonaktifkan. Semua clean/smudge/process/required filters
di config lokal, termasuk includes, dioverride saat inspeksi; status tidak memasuki submodule.
Local `core.autocrlf` yang valid dihormati saat membandingkan working tree/index tanpa menjalankan filter.
Tidak ada repo command/model instruction yang dieksekusi di host.

Transfer melalui Git bundle HEAD → bare repo baru → accepted ref teknis. Init memakai template
kosong. Hanya objek ancestry HEAD yang dipindahkan; refs/config/hooks sumber tidak disalin,
remote/push credentials tidak didaftarkan dan object alternates tidak digunakan. Metadata Git
tetap milik supervisor; attempt worktree dan sandbox snapshot berasal dari clone managed saja.
Baseline adalah starting state, bukan penerimaan fitur atau klaim QA sudah lulus.

Perubahan lokal tidak disalin. Pengguna boleh memilih **patch eksplisit**, dengan SHA sumber wajib.
Patch disimpan sebagai artifact pengguna beserta provenance; aplikasi menerapkannya hanya di
worktree milik import, lalu membuat commit baseline dengan digest patch. Untracked files harus
diikutkan pengguna melalui patch yang mereka pilih. Repo asli tidak di-reset, stash, stage,
commit, atau diubah. Before/after source state diperiksa saat import; sumber yang berubah
menjadi blocker. Tidak ada push/export/deploy pada onboarding.

Import terserialisasi per project. Receipt import dan baseline ref memungkinkan retry baseline
menggunakan objek yang sama, hanya bila source SHA/digest patch/ref masih sesuai. Lease dicek
sebelum import dan publikasi; generation lama tidak mengaktifkan pipeline. Publikasi laporan,
pin message, state/event dan terminal job berada dalam satu transaksi SQLite. Git import berada
di luar transaksi itu. Partial staging tanpa hasil lengkap diblokir untuk inspeksi operator;
aplikasi tidak menghapus atau me-reset ref secara buta. Failed baseline dapat diminta ulang
dengan manifest diperbaiki. Bila source atau patch berubah (mis. pengguna memperbaiki lockfile sesudah blocker) dan DB
belum diinisialisasi, import sebelumnya yang punya receipt **diarsipkan** di `archive/onboarding-<request>-*`
(bukan dihapus atau di-reset) dan import baru dibuat; repo tanpa receipt, atau proyek yang sudah punya accepted tip,
tidak pernah diganti (review R8).

## Runner dan baseline

MVP mendukung static React/Vite dengan root `package.json`, `package-lock.json`, flat Node TAP,
fixture stateless dan migrations `none`. Node/npm version pada sandbox dibandingkan manifest.
Symlink/submodule, `.npmrc`, file credentials/private config dan pola credential dalam source
ditolak sebelum checkout/snapshot. Tree dibatasi 20.000 files/64 MiB. Detector ini konservatif,
bukan scanner secret universal. Stack server/database, private registry, dependency Git/local,
dan runner test framework lain belum terverifikasi; blocker menjelaskan batas tersebut.

Manifest dipilih pengguna/operator dan divalidasi parser runner yang sudah ada. Semua target
commands, termasuk version probes, install/build/test/start, berjalan di sandbox tanpa jaringan.
Install egress memakai cache tarball npm terverifikasi milik supervisor, kemudian npm offline;
source asli, provider key, control DB dan Docker socket tidak diberikan kepada target.

Baseline mencatat commands, exit/log artifacts, actual image/toolchain/config identity, discovered/
executed/passed/failed/skipped tests dan start smoke pada build immutable. Install/build/start
failure atau test incomplete/infrastructure memblokir onboarding. Repo tests yang lengkap tetapi
gagal menghasilkan `ready_with_baseline_failures`; pipeline dapat merencanakan perbaikan, tetapi
review/QA tetap tidak eligible tanpa checks pass atau waiver pengguna yang sesuai. Lead menerima
laporan/rencana onboarding ketika menyusun rencana tiket. Excerpts AGENTS/CLAUDE/README dari Git
baseline disimpan dengan SHA/digest dan label untrusted guidance di context; tools tetap
menegakkan permission/approval, termasuk larangan instruksi push/deploy dari repo.

## Waiver

QA-plan menjalankan baseline kembali pada base dan manifest yang dipakai kandidat. Verification
service membuat fingerprint **per test gagal**, terikat ticket/scope, baseline SHA, environment
dan diagnostics. Identitas failure adalah nama test, tipe/kode/pesan error, expected/actual, serta frame stack milik
project; timing, ordinal, baris/kolom file, frame internal `node:` dan baris rencana `1..N` dibuang, sehingga
penambahan test hijau, perubahan ordinal, atau pergeseran nomor baris tidak mengubah failure lama. Pesan atau
assertion yang berubah tetap failure baru (review R8: sebelumnya output Node nyata membuat semua waiver tidak cocok). Setiap failure kandidat harus cocok dengan waiver pengguna sendiri;
failure baru/berubah dengan count sama, environment/base/scope lain, skipped/incomplete,
infrastructure dan acceptance UAC tidak tertutup waiver. Receipt lama tanpa daftar per-failure
masih memakai pencocokan signature aggregate persis, tanpa memperluas waiver historis.

GUI menampilkan baseline failed dan rujukan evidence, serta form waiver per tiket yang sudah
ada. Evidence QA menyebut `required_checks.status=waived` bila dipakai. Tidak menyebut semua
checks hijau atau memindahkan approval UAT setelah rebuild/base berubah.

## Menjalankan

Gunakan Linux/macOS; Windows memakai WSL dengan source/workspaces di filesystem Linux.
API boleh berjalan di Windows, tetapi path repo harus dapat dibaca host worker. Setelah
memigrasikan DB, buat project existing lewat GUI/API, buka panel onboarding dan pilih manifest
JSON (`examples/dev010/runner-manifest.json`). Pilih patch/SHA jika diperlukan, lalu mulai job.

```sh
cd apps/backend
export PIPELINE_WORKSPACE_ROOT=/absolute/linux/managed-workspaces
python -m app.onboarding inspect --source /absolute/linux/source-repo
python -m app.worker --runtime onboarding --db /absolute/linux/app.sqlite3 --artifacts /absolute/linux/artifacts
```

Runtime onboarding tidak membutuhkan Hermes/model/key. Worker `--runtime pipeline` juga
mendaftarkannya dan melanjutkan tiket sesudah approval scope dengan konfigurasi provider/Hermes
yang sudah dipin. API/worker harus memakai DB dan artifacts yang sama; integrator, preview dan
pipeline memakai `PIPELINE_WORKSPACE_ROOT` yang sama.

Alternatif intent operator yang eksplisit:

```sh
python -m app.onboarding request --project PROJECT_ID --manifest ../../examples/dev010/runner-manifest.json \
  --db /absolute/linux/app.sqlite3 --artifacts /absolute/linux/artifacts
# Pilihan patch tambahan: --patch /chosen/source.patch --source-sha FULL_SOURCE_HEAD_SHA
```

CLI hanya mengantre pekerjaan. Inspect tidak mengeksekusi source dan request tidak memberi
approval scope/UAT/release. Jika sumber bukan Git, pengguna perlu menyiapkan Git snapshot dulu.

## Evidence

[DEV-013-results.json](../spikes/DEV-013-results.json) mencatat source hasil kualifikasi DEV-010,
fitur receipt melalui Hermes/OpenRouter nyata, browser kandidat passed/base failed, serta
produksi integrator mencapai Accepted dalam DB qualification. Scope/UAT diberikan **test-user
fixture**, bukan UAT manual pengguna. Total 14 model calls, reported cost $0,0176504; usage
unknown kosong. Kualifikasi ini tidak menutup pilot PO/release DEV-015.

ID model diperiksa pada katalog resmi [OpenRouter GPT-4.1 Mini](https://openrouter.ai/openai/gpt-4.1-mini).
Adapter Hermes tetap memakai pin/API yang diverifikasi DEV-006/010; tidak menambah adapter baru.
Setup reproduction opt-in memakai model berbayar dan dedicated fixture source:

```sh
PYTHONPATH=apps/backend python examples/dev013/qualification.py \
  --pilot-root /absolute/linux/dev010-feature-05 --root /new/absolute/linux/dev013-run \
  --hermes-python /absolute/linux/hermes-venv/bin/python --model openai/gpt-4.1-mini --accept-fixture-uat
PYTHONPATH=apps/backend python examples/dev013/summary.py --root /absolute/linux/dev013-run \
  --output docs/spikes/DEV-013-results.json
```

`--accept-fixture-uat` hanya berlaku pada source fixture yang dibuat script dan DB terisolasi;
tanpanya kualifikasi berhenti di UAT. Sumber pilot dibaca untuk export kandidat ke fixture baru,
tidak diubah. Evidence authoritative lengkap tetap di DB/artifacts lokal; summary bukan
pengganti report runner. Export/release/sinkronisasi sumber menjadi scope DEV-014.
