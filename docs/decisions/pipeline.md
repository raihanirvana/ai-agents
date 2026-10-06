# Pipeline produk dan QA — DEV-010

Status implementasi: DEV-010 DONE. Review independen R6: NOT_REVIEWED.
Sumber kebutuhan: ARCHITECTURE §5–10, AGENTS.md, AC DEV-010 di backlog.

## Alur dan sumber otoritas

Worker `--runtime pipeline` mendaftarkan runtime structured untuk PO/lead dan pipeline
untuk technical plan → QA plan → developer → technical review → verification.
Scheduler hanya mengambil proyek yang dikonfigurasi eksplisit, scope yang disetujui,
dependency eligible, dan tiket tanpa blocker. Approval scope, UAT, waiver, repair
extension, serta release tetap berasal dari pengguna melalui domain produk.

JobQueue/SQLite menentukan stage, role, scope, generation/lease, reservation, input,
retry, quota, serta budget. Manifest eksperimen DEV-006 tidak menjadi state authority.
Adapter `ProductAdmission` memakai interface transport relay DEV-006 tetapi semua
admission dan usage masuk ke job DB. Key provider hanya berada di supervisor relay;
child Hermes mendapat bearer per-run. Target mendapat snapshot tanpa `.git`, tanpa
secret, DB kontrol, maupun socket Docker. Common Git metadata dan refs ada di broker.
Setiap broker operation memeriksa lease produk sebelum memakai credential lokal.

Default budget scope: 48 model requests, 120 tool calls, 1.800 detik aktif,
200.000 total tokens, 4.096 output tokens per request. Ini cap request/token/time,
bukan batas biaya USD otomatis. Usage dan cost yang tak dilaporkan adalah unknown.
Retry, jawaban input, serta repair memakai akumulasi scope yang sama. Budget pipeline adalah pool
`pipeline` per scope (`ticket:<id>:v<n>:pipeline`), terpisah dari chat PO/lead pada scope itu (review R010-01);
reply lead untuk pertanyaan developer mewarisi pool pengirim. Perpanjangan
hanya lewat command pengguna; tidak ada fallback model otomatis. Provider/model
tetap dapat dipilih per role melalui `agents/models.json`.

Untuk demo yang diotorisasi pengguna, budget command menerima
`unlimited_total_tokens: true` dengan `additions: {}` untuk job latest yang
stopped karena total_tokens, cleanup selesai, scope/stage masih eligible.
Nilai total_tokens menjadi null di seluruh pool scope dan retry baru;
usage, history, repair counter dan pin evidence tidak direset. Keputusan ini
diaudit dengan actor user/idempotency key. Batas model calls/tool calls/active
time dan output per request tetap finite. Policy project eksplisit
`workflow.pipeline.budget_limits` berlaku untuk pool baru; pool existing memakai
keputusan budget yang sudah tersimpan. Default proyek lain tetap 200.000 token.

Otorisasi pengguna `unlimited_budgets: true` pada budget command menghapus semua
cap job (`model_calls`, `tool_calls`, `active_s`, `output_tokens`, `total_tokens`
menjadi null). Flag policy eksplisit dibutuhkan untuk null pada limit wajib;
default proyek lain tetap finite. Usage seluruh attempt tetap dijumlahkan,
retry memerlukan cleanup selesai dan scope/stage saat ini masih eligible.
Hermes memakai representasi unlimited iterations bawaan (sys.maxsize), relay
tidak menimpa output limit dengan null, dan batas teknis respons/timeout provider
tetap mengikuti model registry. Demo ini juga memiliki otorisasi user untuk
`ticket.workflow.unlimited_repairs=true`; API menampilkan repair_limit null.
Counter repair dan usage tidak direset. Approval scope/UAT/release, lease fencing,
sandbox limits dan timeout command tetap berlaku.

Jika accepted base maju ketika run menunggu, checkpoint dengan scope yang sama
dan base lama tetap menjadi artefak histori. Resume tidak menyalin source lama
ke tree terbaru; gunakan jalur repair/rebase kandidat sebelumnya, atau mulai dari
base terbaru jika konflik. Scope checkpoint yang berbeda tetap ditolak. Upaya
baru memerlukan kandidat, target, review, QA dan UAT baru.
Saldo provider tetap menentukan apakah request dapat dilayani.

Technical review memakai builder khusus per call dengan batas estimasi 32.768
token untuk scope, full diff (termasuk lockfile), dan gate evidence. Builder chat/
planning tetap 8.000 secara default; tidak ada mutasi konfigurasi builder bersama.
Manifest snapshot mencatat limit dan estimasi aktual. `ContextRefused/TooLarge`
gagal permanen sebelum model call; ukuran di atas cap tidak dipotong diam-diam
atau diulang sebagai transient crash. Usage/token/time caps job tetap berlaku.

Penolakan lead atau browser assertion gagal menghasilkan `repair_feedback` yang
persisten. Developer berikutnya menyalin source kandidat yang ditolak ke snapshot
baru dengan attempt/ref/credential baru; accepted ref tidak bergerak. Domain membatasi
tiga `request_changes`: initial development dan dua repair otomatis, lalu needs_human.
Infrastructure/incomplete evidence menjadi failure terlihat, bukan loop tanpa batas.

## Workspace, recovery, dan publikasi

DEV-005 tetap memiliki sandbox, dependency-cache offline, broker commit, clean build,
serta resource ownership. Resource intent ditulis ke job sebelum launch, lalu stopper
dan reconciler memeriksa owner/generation. Cleanup dan pengarsipan selesai sebelum
retry dapat berjalan. Engine tidak tersedia atau owner berbeda tidak dianggap clean.
Inspect error selain explicit not-found juga menahan cleanup. Stop segera memanggil
resource stopper sehingga child/container harness tidak menunggu timeout runner.

Pertanyaan developer menyimpan checkpoint source sebagai artifact, lalu membuat
pertanyaan terarah ke lead melalui Threads. `needs_user` tetap dieskalasi ke pengguna.
Resume membangun context dari DB dan memulihkan byte checkpoint pada attempt baru;
scope/base lama ditolak. Reservation model yang belum difinalisasi saat crash dicatat
unknown ketika recovery, tanpa menghapus hitungan request sebelumnya.

Submit kandidat, approve technical review, dan QA-pass/open-UAT menyelesaikan job di
transaksi DB yang sama dengan domain publication. Marker completion job/generation
memungkinkan supervisor membersihkan child yang crash sesudah commit tanpa membuat
retry di phase yang sudah berubah. Stop/revision setelah eksekusi harness dicek lagi
dalam transaksi publikasi sehingga hasil stale tidak membuat Verification/UAT.

## Required checks dan baseline waiver

Install/build dijalankan pada clean source kandidat dengan manifest dan image ID
yang dipin. `test` adalah required repo gate; adapter membaca **Node TAP**
(`node --test`, atau `npm test` yang menjalankannya), termasuk
`describe`/`it` bertingkat: result suite tidak dihitung sebagai eksekusi test, dan
ID anak memakai path nama suite agar nama sama di suite berbeda tetap terpisah.
Test kosong, skipped/todo,
cancelled, duplicate IDs, summary tidak konsisten, atau format framework lain menjadi
incomplete. Test IDs yang ditemukan di accepted baseline juga wajib muncul pada
kandidat. Perubahan test tetap masuk diff yang direview lead. Output target pada gate
repo tidak pernah menjadi authoritative browser report.

Perbaikan demo 2026-10-06: adapter lama menghitung hanya result top-level dan
menolak tiga test hijau di dalam satu suite (executed=1 vs tests=3). `run_command`
phase test kini mengembalikan `repository_gate` agar developer melihat incomplete
sebelum submit. Salah argumen write ke read_file ditolak dengan petunjuk patch_file.
Script echo sukses/tes kosong tetap incomplete. Histori evidence/repair tidak diubah.

Baseline memakai command/image/config yang sama dalam sandbox baru. Failure asli
baseline memperoleh fingerprint artifact dari verification service. Hanya waiver
pengguna yang cocok dengan scope, base SHA, environment, test ID, dan signature
failure dapat menutup gate yang gagal. DEV-013 memfingerprint diagnostics per test gagal
tanpa timing, ordinal, nomor baris/kolom, frame internal Node, dan baris rencana TAP; tambahan test hijau atau
pergeseran baris tidak mengubah failure lama, sedangkan pesan error yang berbeda tetap failure baru.
Semua failures memerlukan waiver masing-masing. Receipt lama tanpa per-test list tetap
memakai exact aggregate signature. Jumlah failure sama tidak cukup. Incomplete/infrastructure failure tidak
dapat di-waive; browser UAC tetap wajib. Evidence menampilkan `required_checks:
{status: waived, waiver_ids: [...]}` bila waiver dipakai.

## Suite dan evidence authoritative

QA melalui Hermes hanya menyusun DSL browser yang tervalidasi: click/fill/press/reload dan
assert_text/count/visible/value. Ada 1–24 mandatory tests, setiap test punya assertion,
ID unik, tujuan feature/bug/regression/smoke, dan pemetaan UAC. Semua automated UAC
harus tercakup; manual UAC tetap checklist pengguna dalam domain/API/GUI yang ada.
Suite disimpan sebagai artifact supervisor di luar mount developer. Tool schema
meng-inline referensi Pydantic agar nested plan/steps terkirim utuh ke Hermes.
`fill` mengubah nilai field, bukan event keyboard. Enter harus berupa langkah
`press` dengan selector field dan value `Enter`, sesudah fill. Tombol press
dibatasi Enter/Tab/Escape/Space/Backspace/Delete/ArrowUp/Down/Left/Right/Home/End;
string arbitrary, chord, newline, dan missing value ditolak kontrak. Tetap wajib
assertion hasil; press sendiri tidak membuktikan UAC. Perubahan runner keyboard
mengubah code digest, sehingga suite/target/QA lama tidak dapat dilabel ulang
pass: rencana yang salah harus dibuat ulang dan target baru diverifikasi.
Untuk persistence, `{"action":"reload"}` tidak menerima selector/value dan
memuat ulang page dalam context test yang sama, preserving browser storage.
Health response harus 200. State harus di-assert lagi sesudah reload; add/assert
tanpa reload tidak membuktikan restoration. Tiap mandatory test tetap memakai
context baru, jadi tidak mewarisi state dari test lain. Actual fixture regression
membuktikan persistence passed sedangkan memory-only gagal sesudah reload.

Build bundle immutable, build record, suite digest, source/base SHA, configuration,
toolchain, fixture/migration definitions, image ID, dan runner-code/image digest
membentuk verification target. Rebuild/config/image berubah membutuhkan target dan
QA baru. Runtime menolak konfigurasi/base/runner yang berubah sebelum verifikasi.
Build/target/suite/log/evidence dipin melalui kandidat, Verification, dan attachments
pesan; resource cleanup tidak menghapus artifact teruji.

Harness memulai static target readonly tanpa published port dan tanpa jaringan.
Runner Playwright terpisah hanya berbagi network namespace target yang terisolasi;
suite/plan readonly hanya dipasang ke runner. Browser membatasi request ke origin
target. Tidak ada writable report mount yang dibagi. Report dibaca hanya dari stdout
runner, dengan nonce invocation, target digest, suite digest, exact mandatory IDs,
UAC mapping, dan discovered/executed/passed/failed/skipped yang diverifikasi ulang.
Multiple frames, report palsu/missing, zero/skipped tests, health/transport failure
atau exit tidak cocok menghasilkan incomplete. Status pass dari model diabaikan.

Runner mencatat command/exit, report/checksum, duration, screenshot dan trace berbatas
ukuran. Command install/build/repo test punya stdout/stderr artifacts dan environment
identity. Timeout runner juga menghasilkan evidence incomplete; stale run tidak
boleh mempublikasikannya. Static server memeriksa symlink di seluruh ancestor path.

Pada base yang dapat dibangun, suite yang sama dijalankan terpisah terhadap base.
Feature/bug tests harus gagal pada base dan lulus kandidat; regression boleh green
di kedua sisi. Base awal kosong dicatat eksplisit non-applicable. Base gagal build
atau browser comparison incomplete menolak QA pass. `pipeline:fake` tetap berlabel;
browser nyata dengan model fake tidak dapat membuka UAT. Smoke health di sini milik
verifikasi tertutup; lifecycle preview untuk pengguna adalah DEV-011.

## Setup dan menjalankan

Gunakan Linux/macOS dengan Docker (WSL pada Windows). Simpan workspace dan checkout
Hermes di filesystem Linux. Install Hermes editable sesuai pin di
`apps/backend/app/runtime_spike/hermes-pin.json` dengan Python >=3.11,<3.14; jalur interpreter
venv dipertahankan, bukan di-resolve ke base Python. Worker memeriksa package, source
commit/cleanliness, dan versi sebelum run. Lihat setup DEV-006 di runtime.md.

Dari root repository:

```sh
docker pull node:22.20.0-alpine
docker build -t aiagent-verification:1.63.0 contracts/verification
# Buat konfigurasi model lokal; key di .env.local atau env OPENROUTER_API_KEY.
cp examples/dev010/models.openrouter.example.json agents/models.json
export HERMES_PYTHON=/absolute/linux/hermes-venv/bin/python
export PIPELINE_WORKSPACE_ROOT=/absolute/linux/product-workspaces
cd apps/backend
./.venv/bin/python -m app.persistence upgrade
# Buat proyek mode new lewat GUI/API, lalu gunakan project ID-nya:
./.venv/bin/python -m app.pipeline configure --project PROJECT_ID \
  --manifest ../../examples/dev010/runner-manifest.json \
  --workspace-root "$PIPELINE_WORKSPACE_ROOT"
./.venv/bin/python -m app.worker --runtime pipeline
```

Konfigurasi di atas membuat managed Git dengan empty technical base untuk proyek
baru. Tiket pertama tetap perlu membangun app, lockfile dan tests sesuai manifest,
serta approval scope pengguna. CLI tidak mengimpor repo sumber atau mengganti base
yang berbeda dari DB. Manifest adalah konfigurasi operator, bukan tool model.

### Bootstrap dependency pada proyek baru

Pada proyek `new` yang accepted base-nya masih kosong, developer mendapat tool
`run_command` phase `bootstrap`. Developer membuat `package.json` terlebih dahulu
dengan versi persis dari `reference_bootstrap` pada context. Bootstrap menghasilkan
`package-lock.json` dari katalog `contracts/bootstrap/react-vite`, kemudian developer
menjalankan phase `install`, `test`, dan `build`. Tidak ada kode fitur/fondasi yang
dimasukkan otomatis ke accepted ref. Lock yang dihasilkan adalah perubahan attempt
yang ikut diff, commit, review, QA, serta UAT. Build target mem-pin dependency digest.

Katalog awal: React/React DOM 18.3.1, Vite 6.4.3 dan plugin React 4.3.4 dengan seluruh
dependency closure, URL registry HTTPS dan integrity SHA512. Root lock disesuaikan
dengan nama/version dan dependency declaration package.json; closure referensi tetap
dipertahankan agar peer/optional/platform pins tidak di-resolve ulang. Dependency baru,
range versi, workspace, override, peer/optional root atau package sumber lain ditolak
secara eksplisit; penambahannya membutuhkan kualifikasi katalog/runner. Ini dukungan
reference runner MVP, bukan resolver npm umum.

Bootstrap tidak menjalankan npm/kode target pada host, tidak membuka jaringan container,
tidak melonggarkan install command, dan tidak mengubah lock repo existing. File dibaca/
ditulis melalui broker snapshot dengan lease dan generation produk saat ini. Install
tetap fixed `npm ci` dengan scripts dinonaktifkan dan tarball diverifikasi supervisor.
Catalog digest dicatat di log run; lock sebenarnya masuk source SHA/dependency digest.

QA dan developer diberi context eksplisit ketika source kosong: tidak ada folder lain
untuk dicari. QA menyusun feature suite dari UAC/rencana teknis dan menyatakan selector
sebagai kontrak untuk developer. QA tidak mengarang regression behavior yang belum ada.
Run yang sudah stopped karena budget tetap stopped; operator fix/restart worker tidak
mereset usage atau memperpanjang cap. Continuation memerlukan budget extension pengguna
yang terbatas, memakai usage kumulatif dan command produk yang sama.

Kualifikasi opt-in berbayar dengan DB terisolasi dan **bundled test fixture**:

```sh
cd ../..
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev010/qualification.py \
  --root /absolute/linux/qualification-feature --case feature \
  --hermes-python "$HERMES_PYTHON" --model openai/gpt-4.1-mini
# Root berbeda untuk seeded bug:
PYTHONPATH=apps/backend apps/backend/.venv/bin/python examples/dev010/qualification.py \
  --root /absolute/linux/qualification-bug --case bug \
  --hermes-python "$HERMES_PYTHON" --model openai/gpt-4.1-mini
```

`--resume` mempertahankan DB/scope/budget/checkpoint dan model kecuali dipilih ulang
secara eksplisit. `--extend-model-calls N` (1–16) pada resume adalah opt-in test-user
budget command untuk run yang stopped, bukan extension otomatis. Bootstrap fixture
dan approval scope oleh test-user hanya fixture setup; bukan onboarding DEV-013,
integrator produk DEV-012, maupun approval UAT/release.

## Kualifikasi nyata dan batas

Manifest bukti: [DEV-010-results.json](../spikes/DEV-010-results.json).
OpenRouter `openai/gpt-4.1-mini` berhasil pada fitur toggle diskon (24 requests,
$0,0322168); seeded bug berhasil setelah restart dan explicit budget extension,
dengan 32 requests mini ditambah 5 `openai/gpt-4.1` (37 total, $0,068536).
Dua kandidat final mencapai UAT dengan browser tests yang lulus kandidat dan gagal
base, tanpa fake dan tanpa approval UAT pengguna. Total semua enam percobaan termasuk
empat percobaan awal yang gagal: **$0,12668808**, usage unknown kosong pada ringkasan.

Percobaan awal mengungkap venv symlink yang kehilangan Hermes, nested tool schema
yang kosong, serta final prose tanpa completion tool. Semuanya diperbaiki; satu
correction turn tetap memakai budget produk yang sama. Qwen pada percobaan awal
tidak menyelesaikan QA; ini belum membuktikan kompatibilitasnya pada implementasi
akhir. Model qualification ini tidak menggantikan percakapan PO/pilot DEV-015.

Metadata model/usage diperiksa 2026-10-05 pada katalog resmi
[GPT-4.1 mini](https://openrouter.ai/openai/gpt-4.1-mini),
[GPT-4.1](https://openrouter.ai/openai/gpt-4.1),
[Qwen](https://openrouter.ai/qwen/qwen3-coder-30b-a3b-instruct/providers), dan
[usage accounting](https://openrouter.ai/support/). API Hermes diperiksa pada source
pin DEV-006: `AIAgent.run_conversation(..., conversation_history=...)`.

Saat kualifikasi nyata, image runner adalah image acceptance DEV-006 yang kompatibel
dan diberi tag verification; exact image ID ada di target/evidence. Dockerfile publik
DEV-010 juga dibangun dan diuji terpisah. Image build baru memiliki ID baru; evidence
lama tidak otomatis menjadi approval untuk image baru.

Batas minimum: static React/Vite, Node TAP, fixture stateless, migrations `none`.
DSL belum mendukung arbitrary browser scripts, upload, auth flows, atau backend DB
target. Custom manifest `start`/port/health dicatat sebagai identity, tetapi acceptance
minimum menggunakan trusted static server pada internal 4173 dan health `/`.
Preview GUI, integration accepted ref dan existing-repo onboarding tersedia melalui
DEV-011/012/013. Release dan pilot tetap DEV-014/015. Artifact besar, DB, serta private Hermes
transcripts tidak di-commit. Review independen R6 belum dilakukan.
