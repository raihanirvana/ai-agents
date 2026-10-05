# Operasi lokal dan pilot coffee shop

Gunakan POSIX/WSL, Docker dan Python 3.11–3.13 untuk worker/Hermes. API, SQLite dan managed workspace sebaiknya berada
di filesystem Linux WSL; jangan memakai SQLite pada network/9p mount. Stack yang dikualifikasi: static React/Vite,
fixture stateless, migration `none`, npm public lockfile dan flat Node TAP. Pembayaran/transaksi produksi, auth aplikasi
target, database target persisten, mobile, deployment/hosting tidak termasuk dukungan ini.

## Setup

Dari root checkout, ikuti instalasi dependensi di README. Pasang Hermes dari commit dan versi
`apps/backend/app/runtime_spike/hermes-pin.json` sesuai [runtime.md](../decisions/runtime.md), lalu:

```sh
docker pull node:22.20.0-alpine
docker build -t aiagent-verification:1.63.0 contracts/verification
cp examples/dev010/models.openrouter.example.json agents/models.json
export HERMES_PYTHON=/absolute/linux/hermes-venv/bin/python
export DATA_DIR=/absolute/linux/aiagent-data
export DATABASE_PATH="$DATA_DIR/app.sqlite3"
export ARTIFACT_DIR="$DATA_DIR/artifacts"
export PIPELINE_WORKSPACE_ROOT="$DATA_DIR/workspaces"
# OPENROUTER_API_KEY di env atau .env.local privat; jangan pada VITE_* atau Git.
cd apps/backend
.venv/bin/python -m app.persistence upgrade --db "$DATABASE_PATH"
.venv/bin/python -m app.persistence check --db "$DATABASE_PATH"
```

Contoh model eksplisit memakai `openai/gpt-4.1-mini`; model gratis dapat dipilih secara eksplisit setelah tools/output
dan quota dikualifikasi. Tidak ada fallback diam-diam. Harga katalog diperiksa 2026-10-05: $0.40/M input, $1.60/M output,
cache read $0.10/M; angka invoice/routing dapat berbeda. Sumber: [katalog model OpenRouter](https://openrouter.ai/openai/gpt-4.1-mini)
dan [API resmi](https://openrouter.ai/docs/api/reference/overview). Biaya aktual pilot berasal dari usage provider/DB,
bukan estimasi harga dikalikan panjang prompt. Key kosong membuat run nyata gagal jelas; fake hanya untuk tes fondasi.

## Start dan stop

Pada terminal terpisah dengan environment yang sama, dari `apps/backend`:

```sh
.venv/bin/python -m app                         # API 127.0.0.1:8000
.venv/bin/python -m app.worker --runtime pipeline --db "$DATABASE_PATH" --artifacts "$ARTIFACT_DIR"
```

Dari root checkout: `npm run dev:web`; buka `http://127.0.0.1:5173`, login memakai code lokal
`$DATA_DIR/auth/login-code`. Preview target memakai `localhost`, bukan host control plane 127.0.0.1.
Web memakai exact Origin; jangan mengubah API_HOST menjadi 0.0.0.0 atau menaruh credential pada preview.

Buat brief → minta proposal PO → baca scope/UAC/dependency → approve sebagai pengguna. Untuk new project,
gunakan `python -m app.pipeline configure` sebagaimana [pipeline.md](../decisions/pipeline.md); existing project memakai
panel onboarding dan runner manifest `examples/dev010/runner-manifest.json`. Sumber existing hanya dibaca. File dirty
tidak ikut otomatis; patch eksplisit harus cocok source SHA. Baseline merah memerlukan waiver pengguna per failure/scope
atau perbaikan sumber; required release gates tetap harus green.

Di UAT buka preview, baca evidence, kirim feedback pada scope yang sama atau minta revisi PO untuk perubahan UAC.
Scope version, candidate, verification target dan evidence harus cocok saat accept. Integrator yang menggeser accepted
tip; kandidat pada base lama kembali development lalu mendapat QA/UAT baru. Freeze release mem-pin seluruh regression
accepted, approval release terpisah. Export lokal adalah aksi eksplisit; tidak otomatis push/deploy.

Untuk berhenti, stop preview lewat GUI, kemudian Ctrl+C/SIGTERM worker dan tunggu pesan stop/cleanup selesai;
hentikan API writer dan frontend. Jangan membunuh container global atau reset/stash repo sumber. Stop satu run melalui
GUI merevokasi generation dahulu; slot dilepas setelah cleanup terverifikasi. Waiting input/quota adalah state persisten.
Jika `needs_human`, baca receipt/blocker; jangan membuat job baru untuk menghindari caps. Budget extension memerlukan
aksi pengguna yang terbatas dan mempertahankan usage lama.

## Restart, backup, restore

Restart worker pada DB/artifacts/workspaces yang sama. Lease expired dipagar ulang dan resource milik job direkonsiliasi;
request/answer dan approval tidak diulang. Preview yang kehilangan proxy ditutup dan harus dibuka kembali dari bundle
teruji. Git/DB diverged diblokir; integrator merekonsiliasi operasi pending sebelum backup.

Setelah semua writer/run/preview berhenti dengan tertib:

```sh
python -m app.recovery backup --offline --db "$DATABASE_PATH" --artifacts "$ARTIFACT_DIR" \
  --workspaces "$PIPELINE_WORKSPACE_ROOT" --destination /absolute/linux/snapshots/pilot-001
python -m app.recovery restore --offline --snapshot /absolute/linux/snapshots/pilot-001 \
  --destination /absolute/linux/aiagent-restored
export DATA_DIR=/absolute/linux/aiagent-restored
export DATABASE_PATH="$DATA_DIR/app.sqlite3"
export ARTIFACT_DIR="$DATA_DIR/artifacts"
export PIPELINE_WORKSPACE_ROOT="$DATA_DIR/workspaces"
python -m app.persistence check --db "$DATABASE_PATH"
```

Baru start API/worker. Login session/runtime token lama tidak berlaku; gunakan login code data root baru. Pilih kandidat
UAT terpulihkan dan reopen preview: target digest/build bundle tetap sama, tanpa rebuild. Simpan provider key dan model
config secara terpisah dari snapshot. Source path existing harus tersedia lagi untuk export/sync.

Jika job gagal permanen, perbaiki penyebabnya terlebih dahulu dan pastikan cleanup selesai. Operator lokal dapat
membuat satu retry eksplisit; ID keputusan yang sama idempotent. Ini mempertahankan cap, budget key, usage lama dan
checkpoint; tidak dapat melewati budget habis atau scope/phase yang sudah tidak berlaku. Gunakan ID job attempt
terakhir dari Aktivitas, lalu start worker pada DB/artifacts yang sama:

```sh
python -m app.recovery retry-job --db "$DATABASE_PATH" --job JOB_ID \
  --authorization-id operator-fix-001
```

CLI ini untuk operator yang menguasai data root lokal. Agent tidak memperoleh tool retry/authorization. Retry tidak
menyalin approval kandidat lama; pipeline masih harus review/QA/UAT terhadap target baru yang dihasilkan.

Snapshot rusak/hilang jangan dianggap QA pass. Restore biasa menolak corruption. Jika artefak tertentu hilang dan Anda
memilih restore terdegradasi, tambahkan `--allow-unavailable`; file itu ditandai unavailable, tidak dapat disajikan atau
dipakai approval. DB/Git tidak boleh diabaikan. Cleanup produk menjaga pin approval/candidate/verification/release/message;
jangan `rm -rf` artifact directories. Backup tidak mencakup scratch executors dan bukan backup online/multi-host.
Detail: [keputusan recovery](../decisions/recovery.md).

## Lokasi bukti dan kualifikasi ulang

DB: `DATABASE_PATH`; evidence/build/context: `ARTIFACT_DIR/<project>/<artifact>/<name>`;
Git: `PIPELINE_WORKSPACE_ROOT/<project>/repo.git`; scratch attempt di `runs`, preview unpacked di `.previews`.
Homes Hermes privat hanya untuk eksekusi/diagnostik, bukan artefak reopen. Artefak diperiksa checksum saat dibaca.
Inventory snapshot menyimpan file digests/refs/pins, tanpa key provider. Semua runtime data tetap gitignored.

Pilot berbayar membuat repo fixture sendiri, menggunakan test-user approval, tanpa manual user UAT atau pembayaran nyata:

```sh
# Dari root repo, environment provider telah diset.
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev015/qualification.py \
  --root /absolute/linux/dev015/pilot-001 --hermes-python "$HERMES_PYTHON"
# --resume mempertahankan DB/usage bila worker berhenti; setelah restore gunakan root terpulihkan.
```

Skrip menetapkan finite calls/tools/time/tokens per scope dan pengawas berhenti setelah reported cost melebihi USD 1.
Guard cost itu bukan cap billing provider: usage unknown dicatat dan panggilan in-flight dapat selesai sebelum threshold
diamati. Caps token/call tetap berlaku lintas retry; tidak ada budget extension otomatis. Satu percobaan bisa gagal dan
tetap harus masuk ringkasan biaya. Bukti serta batas pemeriksaan ada di [pilot-report](../pilot-report.md).


Audit hasil yang sudah completed (tidak mengirim model requests):

```sh
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev015/summary.py \
  --root /absolute/linux/dev015/pilot-001-restored \
  --failed-root /absolute/linux/dev015/failed-paid-run --output /absolute/linux/dev015/receipt.json
```

Ulangi `--failed-root` untuk setiap DB run berbayar yang dibuang; jangan menjumlahkan DB awal dan hasil restore
karena histori usage yang sama. Audit memeriksa ref/DB/release, source inventory, pin checksums dan semantik dua klik.
Snapshot/context/DB privat tetap diperlukan untuk review independen terhadap IDs receipt publik.

Pilot ini mencatat keputusan test-user ketika gagal, bukan memperbesar cap otomatis. `--resume --revise-transaction`
meminta proposal scope baru dengan preservation/dua klik lengkap; `--resume --authorize-transaction-repair`
memberi tepat satu repair tambahan dengan guidance mengembalikan unit tests accepted;
`--resume --extend-transaction-budget` mengotorisasi satu kenaikan token 100.000 yang dicatat. Semua hanya pada DB fixture
terisolasi. Kenaikan berikutnya 50.000 pada eksperimen dilakukan eksplisit melalui `JobQueue.extend_budget`,
mempertahankan usage dan call/tool/time caps. Operasi budget produk memerlukan keputusan pengguna yang bounded;
retry job bukan pengganti budget extension. Deadline pengawas berlaku per invocation, usage scope tetap kumulatif.
