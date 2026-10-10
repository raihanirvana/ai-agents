# Salinan dependency sandbox dan amendment UI — 11 Oktober 2026

Status IN_PROGRESS / NOT_REVIEWED. Implementasi dan regresi terarah sudah
diverifikasi pada permintaan review mendalam pengguna. Worker demo tetap mati.
Tes memakai DB/Git/workspace sementara; tidak ada panggilan provider model nyata,
migration atau perubahan project demo. Review ini dilakukan implementer,
bukan independent review.

## A. Dependency tidak disalin pada test/build/start

- Sandbox memvalidasi tree dependency lalu memasangnya readonly di
  `/installed/node_modules`. Seed source mengecualikan node_modules dan membuat
  symlink internal `/work/node_modules` ke mount itu.
- Export tar tidak memakai follow-link. Importer mengabaikan hanya referensi
  mount yang persis, menolak referensi yang diubah, dan mempertahankan tree host
  melalui rename saat publikasi source. Dependency tidak ditulis ulang per file.
- Install tetap mengekspor dependency baru, tetapi tidak seed dependency lama
  yang memang akan dihapus npm ci. Source/dist tetap melalui import tar bounded,
  validasi path/symlink/special files, pause dan publikasi dengan rollback.
- Dependency mempunyai batas terpisah 100 ribu entries / 512 MiB. Source/output
  tetap memakai max_snapshot_files/max_snapshot_bytes. Cache install dan
  run_checks menggunakan batas dependency yang sama. Root dependency harus real
  directory; symlink yang keluar dari tree ditolak.
- Vite `.vite`, `.vite-temp`, dan `.cache` memakai tmpfs terpisah 32 MiB masing-masing;
  target tidak dapat mengubah paket readonly. Scratch tidak diekspor/dicache.
  `/tmp` maksimum seperempat memory cap pada test/build/start; install memakai
  batas tmpfs yang dikonfigurasi (default 512 MiB). `/work` maksimum setengah
  memory cap, dibatasi juga work_mb. Memory/CPU/network policy tetap finite.

Readonly mount menggunakan tree instalasi lokal yang tervalidasi, bukan akses
langsung ke direktori cache privat supervisor. Target tidak mendapat DB, Git,
secret atau Docker socket. Legacy tree tetap diperiksa sebelum mount.

Ini menghilangkan full dependency seed/export pada fase setelah install, bukan
semua I/O: validasi metadata/digest dan cache restore masih membutuhkan I/O.
Install pertama tetap memakai tmpfs dan dapat gagal karena memory/disk/file cap.
Framework yang menulis direktori package lain membutuhkan scratch policy yang
diuji, bukan otomatis membuka dependency writable. React/Vite dan cache restore
sudah dijalankan nyata pada Docker 24.0.5 / node:22.20.0-alpine. Belum ada
benchmark workload demo yang sama atau pengukuran peak memory; kelulusan fixture
ini tidak menjamin proyek besar bebas OOM.

## Bug yang ditemukan dan diperbaiki saat review

1. Docker 24 mengubah permission working directory tmpfs saat create; copy root
   source juga mewariskan mode host. Container sekarang mulai di `/`, seed hanya
   isi source, lalu command memakai cwd `/work`. Scratch memiliki UID/GID 1000.
2. `docker cp` pada tmpfs `/work` mengembalikan archive kosong meskipun command
   lulus. Probe nyata menghasilkan archive 1536 byte berisi hanya `.`. Exporter
   supervisor kini menghentikan proses target dengan SIGSTOP, memastikan state
   quiescent, men-stream tar dari mount hidup, lalu pause sebelum validasi dan
   publikasi. Tar binary berasal dari image readonly; opsi/env-nya tetap.
   Tidak menambah capability, akses host, atau writable mount. Kegagalan/timeout/
   cancel tidak mempublikasikan tree parsial.
3. Mount bernama `/installed-node_modules` mematahkan resolusi peer dependency
   Node (`picomatch` dari Vite). Path sekarang `/installed/node_modules`, menjaga
   struktur nama direktori yang diperlukan CommonJS/ESM.
4. Static UI submission memanggil `ProductWorkspace.fake` yang tidak tersedia.
   Metadata kini memakai fake flag identitas attempt yang sudah diverifikasi.
   Tes nyata membuktikan missing testid dapat diperbaiki pada job yang sama
   sebelum satu kandidat dipublikasikan.
5. Install offline dengan network none belum selalu mendapat install_phase.
   Semua pemanggil install trusted kini meneruskannya, termasuk baseline dan
   independent build. Limit dependency juga mencakup scratch yang ditambahkan.

Error initialization kini menyimpan log container; cache installation memasukkan
bounded_io.py ke identitas implementasi installer. Fixture coverage repair lama
diperbarui ke QaSuiteRevision tanpa mengubah expected value atau menghapus step
asli, dan tetap membuktikan fresh baseline/candidate serta retry proposal.

## B. Testid untuk aksi, semantic locator untuk assertion

Kontrak baru dipin dengan `action_locators: testid`. Validator memastikan kontrol
akhir pada aksi memakai testid, termasuk tombol pada row yang difilter original
input. Role/name/label/text tetap tersedia untuk assertion yang membutuhkannya.
Static inventory membuktikan literal testid, bukan accessible name atau fungsi.
Kontrak historis tanpa field kebijakan mempertahankan vocabulary lama dan bentuk
serialisasinya. Tidak ada approval lama yang diklaim memenuhi kebijakan baru.

## C. QA meminta amendment ke TL

`request_contract_amendment(reason)` tersedia hanya dalam QA planning. Request
dipersist idempotent dengan scope/generation fencing. Job QA selesai sebagai
handoff tanpa suite/pass; scheduler membuat technical_plan TL di lane ringan
dengan key request. TL mempertahankan semua kontrol lama, menambah yang kurang,
dan menaikkan revision tepat satu. Retry TL memakai plan request yang sudah
dipersist, tanpa menaikkan revision lagi. QA planning memakai key revision baru.

Budget pool/usage/history tetap; scope approval/UAT/release dan repair cycles
aplikasi tidak diubah. Amendment tidak mengubah target yang sudah disubmit.
Kontrak revision sekarang menerima 1..1000. Dynamic text juga menerima original
select_option values (termasuk multi-select), selain fill. Seed statis dapat
dideklarasikan lewat literal text kontrak; arbitrary generated output bukan
sumber expected value. Native confirmation menggunakan click_dialog yang ada.

## Verifikasi dan handoff

Perintah dari apps/backend memakai Git modern yang tersedia:
`env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest <paths> -q --tb=short`.
Run pertama dengan PATH default gagal karena `/usr/local/bin/git` 2.15 tidak
mendukung --initial-branch; PATH hanya diubah untuk proses tes, tanpa mengubah
konfigurasi pengguna.

| Paths / kelompok | Hasil akhir |
| --- | --- |
| agents/test_tools; pipeline/{test_scheduler,test_contracts,test_selector_repair,test_ui_contract,test_contract_amendment}; workspace/{test_source_tools,test_bounded_dependency_export} | 129 passed |
| workspace/{test_fsutil,test_manifest,test_gitbroker,test_supervisor_logic,test_review_regressions}; pipeline/test_harness | 92 passed |
| pipeline/{test_coverage_repair,test_contract_amendment} | 25 passed |
| pipeline/test_ui_contract_submission | 1 passed |
| workspace/{test_readonly_dependencies_docker,test_bounded_dependency_export}, sebelum penambahan bulk case | 17 passed |
| workspace/test_readonly_dependencies_docker::test_dependencies_above_source_file_cap_are_not_seeded_or_exported | 1 passed |
| sandbox_docker: isolasi Git/secret/socket/network/root, timeout/cleanup, commit nyata, command evidence | 4 passed |

Sebagian kelompok mengulang tes yang sama; **250 test IDs unik** terverifikasi
(`--collect-only` pada gabungan selection: 250). Ini selection terarah, bukan
seluruh suite repository. Docker/build/browser nyata digunakan, tetapi model
integration berlabel fake dan tidak dapat memberi QA pass/UAT produk.

Dependency fixture nyata lebih dari 20 ribu files tetap berhasil dengan cap
source 2 entries, inode package tetap, dan source baru tersimpan. Fixture Vite
menjalankan install/test/build, restore snapshot tanpa download (download
dipasang fail guard), lalu build lagi. Archive fixtures mencakup duplicate,
path escape, .git, reference readonly yang diubah, dangling symlink, cap,
cancel dan rollback publication.

Planning membuktikan QA handoff/retry, additive revision, TL retry tanpa model
call baru, new QA job, shared budget/usage dan penolakan control replacement.
Browser coverage revision membuktikan persisted proposal reuse serta fresh
execution; fake tetap menolak UAT.

Compile/source dan git diff --check diperiksa kembali. Belum commit/push atau
restart worker. Sisa sebelum status keseluruhan DONE: benchmark demo identik,
peak memory pada install besar, kompatibilitas framework selain fixture,
recovery lintas proses/kill di seluruh titik publication dan independent review.
Legacy suite tetap perlu locator observed untuk revision; kontrak baru tidak
di-backfill otomatis ke target lama. SIGKILL di antara rename bukan skenario
rollback exception yang sudah diuji.

Referensi primer yang diperiksa:
[Docker cp](https://docs.docker.com/reference/cli/docker/container/cp/),
[tmpfs](https://docs.docker.com/engine/storage/tmpfs/),
[bind mount readonly](https://docs.docker.com/engine/storage/bind-mounts/).

## D. Tindak lanjut review efisiensi (temuan d–h) — 11 Oktober 2026

Status IN_PROGRESS / NOT_REVIEWED. Dikerjakan implementer setelah commit 4c1bd7c;
belum commit/push, worker tidak di-restart. Bukan independent review.

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| d. Cek UI baru setelah commit + build penuh | `run_checks` menambah phase `ui_contract`: static inventory pada `build_output` workspace Developer sendiri, di bawah lock operasi yang sama dengan build. Output hilang/symlink → failed. Advisory: submit tetap memeriksa build kandidat independen; `qa_pass` selalu false. Ringkasan transcript menyimpan `missing_testids`; instruksi Developer diperbarui. | `test_run_checks_reports_missing_testids_on_own_build_before_submit`, `..._fails_closed_without_output_and_skips_without_contract` |
| e. Telemetry memperbesar write DB | Metric phase divalidasi lalu dibuffer di `RunContext` (maks 256, flush sinkron jika penuh), digabung ke transaksi heartbeat; heartbeat revoked mengembalikan buffer; flush akhir sebelum archive. Tidak ada lagi pesan `phase.metric` per call. Relay menggabungkan `model.response`/`provider.error` ke satu baris `relay`, dan `tool.metric`/`context.size`/`relay.rejected`/`provider.retry` ke baris audit event yang sama. | `tests/workers/test_telemetry_buffer.py` (5) |
| e. Query pesan tanpa filter SQL | `ProductWorkspace.latest_intent` (intent + scope_version + meta, `not_runtime_log()`, `LIMIT 1`) untuk `suite()`, `ui_contract()`, baseline submit. `_concern_preflight` memfilter intent handoff/review + candidate_id di SQL, tidak lagi jendela 200 pesan termasuk log. | `test_latest_intent_filters_in_sql_and_ignores_runtime_logs` (300 log runtime) |
| f. Receipt install reused | Sudah tertutup oleh A (scratch tmpfs tidak diekspor). Dikeraskan: digest receipt mengecualikan `.cache/.vite/.vite-temp` yang selalu ditutup tmpfs. | `test_install_receipt_digest_ignores_tmpfs_masked_scratch` |
| g. Cache baseline hanya base lulus | Gate `failed` lengkap (counts ada, `infrastructure_failure` false) ikut dicache; incomplete/infra failure tidak. Keputusan waiver tetap oleh pemanggil setiap kali; revalidasi dependency tetap mewajibkan passed. | `test_baseline_cache_admits_only_deterministic_gate_outcomes` (6 kasus) |
| h. `locate()` mengubah semantik selector lama | Target tanpa `ui_contract` (suite sebelum 6714cc3) menjalankan runner dengan `locator_semantics: legacy_engine` di plan runner: selector selain `testid=`/`label=` memakai engine Playwright seperti sebelumnya. Digest suite tidak berubah; key cache baseline-browser memuat mode. | `test_legacy_suite_keeps_playwright_engine_role_semantics`, `test_harness_marks_only_targets_without_ui_contract_as_legacy` |

Perubahan `acceptance.py` mengubah `runner_code_digest`, sehingga tiket aktif
melewati `runner_contract_upgrade` sekali lagi (target baru, QA/UAT baru).

Verifikasi awal Claude (sebelum tindak lanjut di bawah; dari apps/backend, PATH Git modern seperti di atas):
- Test baru: 17 passed.
- `tests/workers tests/pipeline tests/agents`: 431 passed, 7 failed. Ketujuh
  kegagalan identik pada worktree HEAD 4c1bd7c bersih (pesan retry berbahasa
  Indonesia vs regex Inggris, `base_build(image_id=)` pada stub test, lease None
  di dua test budget scheduler, `ContextTooLarge` role lead, preflight abstain);
  bukan regresi perubahan ini dan belum diperbaiki.
- `tests/http` tidak dapat dimuat: venv tidak memiliki `httpx2` untuk
  starlette.testclient (masalah environment, tidak diubah).

Keterbatasan: telemetry di runtime_ref kini tertinggal hingga satu interval
heartbeat (default 5 detik); metric dari crash proses sebelum flush hilang
(observasional, bukan evidence). Cache baseline gagal mengunci fingerprint
kegagalan flaky sampai input berubah. Keterbatasan release gabungan pada review awal diperbaiki di bagian E. Cek UI di run_checks
adalah presence statis, bukan bukti perilaku browser.

## E. Tindak lanjut handoff dan review Codex

- Release gabungan mem-pin `legacy_test_ids` pada target. Runner memilih semantik
  per test: suite lama tetap melalui engine Playwright; suite dengan UI contract
  tetap exact. Digest suite tidak diubah dan tidak ada downgrade global.
- Buffer telemetry dipadatkan menjadi agregat per fase jika flush DB terus gagal.
  Jumlah sampel, durasi, maksimum, kegagalan dan cache hit tetap dipertahankan;
  pengujian menggunakan 1.024 metric selama DB tidak tersedia, lalu flush berhasil.
- Pembacaan file melalui dir-fd memakai `O_NONBLOCK` dan memeriksa regular file
  setelah open. Snapshot scan yang sudah basi tidak bisa menggantung pada FIFO
  yang menggantikan file. Ada regresi terpisah untuk digest dan copy.
- Tes retry mengikuti pesan Indonesia, tes budget memakai lane ringan yang benar,
  batas context dihitung dari konteks wajib, dan preflight concern mengikuti
  diagnosis + revisi suite generik. Bukti fake tetap tidak dapat membuka UAT.
- Tes HTTP memperhitungkan job/event runner setup otomatis. Dependency dev
  `httpx2==2.13.1` (beserta httpcore2/truststore) dipasang dan dipin sesuai TestClient
  Starlette yang sudah dipin; `httpx` lama tetap tersedia untuk integrasi lain.
- Tes sandbox menunggu command benar-benar mulai sebelum cancel, dan memeriksa
  penolakan symlink/FIFO/.git saat import, sebelum perubahan mencapai host.

Ini review implementer, bukan independent review. Tidak ada worker demo dinyalakan.

### Hasil verifikasi akhir handoff

Semua perintah memakai venv backend dan Git modern pada PATH seperti bagian awal.

| Perintah dari `apps/backend` | Hasil |
| --- | --- |
| `pytest tests/workers tests/pipeline tests/agents tests/http -q --tb=short` (ulang setelah patch) | **527 passed**, 590,03 s; dua warning refleksi expression index SQLite |
| `pytest tests/workspace tests/release tests/recovery tests/onboarding -v --tb=short --durations=15` (dimulai sebelum pembaruan fixture sandbox) | **191 passed, 2 failed**, 1.157,30 s; dua fixture sandbox lama sudah diperbaiki dan diulang pada baris berikut |
| `pytest tests/workspace/test_sandbox_docker.py -q --tb=short` (kode/fixture terbaru) | **13 passed**, 43,99 s |
| `pytest tests/release tests/workspace/test_fsutil.py tests/workspace/test_supervisor_logic.py tests/workspace/test_bounded_dependency_export.py -q --tb=short` | **81 passed, 2 failed**, 444,72 s; detail intermiten di bawah |
| `pytest tests/release/test_review_regressions.py -k 'sync_approval_requires or discard_during_sync' -q --tb=short` | **2 passed**, 46,12 s; pengulangan dua kegagalan release |
| `pytest tests/release/test_release_flow.py -k mixed_release -q --tb=short` | **2 passed**, 30,37 s; juga lulus pada grup release di atas |
| `pytest tests/pipeline/test_review_followups_2026_10_11.py tests/workers/test_telemetry_buffer.py -q --tb=short` | **20 passed**, 1,15 s; termasuk regresi FIFO dan buffer saat DB gagal |

Syntax 26 file Python berubah/baru diperiksa melalui `ast.parse`;
`git diff --check` bersih. Angka di tabel saling tumpang tindih, bukan total unik.
Sembilan kegagalan ekspektasi tes lama pada putaran inti awal kini ditutup oleh
putaran ulang 527 passed. Tes HTTP memakai httpx2, tanpa warning deprecation httpx.

**Keterbatasan yang belum terisolasi:** pada putaran release bersamaan dengan grup
Docker lain, dua freeze menghasilkan gate incomplete: stderr `Could not find
\'test.cjs\'`, sementara file ada pada tree commit, dan laporan browser passed.
Release tetap failed sehingga tidak dapat disetujui/sync. Kedua tes lulus saat
rerun terpisah, dan keduanya juga lulus pada putaran infra pertama. Tidak ada
waiver atau retry otomatis yang ditambahkan untuk menyembunyikan masalah ini.
Penyebab hilangnya file di command sandbox belum terbukti; perlu workload berulang
serta inventory source sebelum/ sesudah seed/export untuk isolasi, bukan klaim
bahwa seluruh grup selalu hijau. Worker/backend/frontend demo tetap mati;
container pengujian dibersihkan.


## F. Planning paralel, pilihan QA dan input command immutable

Implementasi tersedia, review **NOT_REVIEWED**. Benchmark lanjutan dan validasi
workload demo ditunda atas instruksi pengguna pada 11 Oktober 2026; catatan ini
tidak mengklaim pengukuran performa final atau penyebab pasti insiden test.cjs.

| Scope | Implementasi / file utama | Bukti dan batas |
| --- | --- | --- |
| QA planning paralel | `pipeline/scheduler.py`, `runtime.py`, `workspace.py`, `workers/queue.py`: QA plan source-only berada di lane interactive dan bisa berjalan bersama Developer setelah TL plan. Developer mulai dari UI contract dan menunggu suite valid sebelum submit. Revision, lease, cleanup dan shared budget tetap diperiksa. Baseline dieksekusi sebelum publikasi commit, melalui cache. | Regresi scheduler/contract amendment; tidak ada otomatisasi scope approval, QA pass atau UAT. |
| Benchmark siap demo | `pipeline/benchmark.py`: temp DB/Git/artifact/cache, cold/warm per identitas, browser baseline dan Developer checks; replay transcript berlabel estimasi, tanpa provider call. `workspace/memory_observation.py` menyediakan sampling optional bila kernel tidak menyediakan memory.peak. | Putaran awal pada fixture reference disimpan sebagai **preliminary**, berjalan bersamaan dengan tes lain; tidak membuktikan speedup demo atau peak memory. Pengukuran berikutnya ditunda. |
| Keputusan user untuk QA tidak meyakinkan | `domain/qa_resolution.py`, `service.py`, `evidence.py`, migration `0007_qa_waivers.py`, HTTP/queries/UI Ticket, pin dan release runtime. Keputusan user immutable mem-pin semua identitas dan hanya dapat mengambil alih UAC dari test gagal yang dieksekusi lengkap dengan diagnosis test/unknown. | QA asli tetap failed. Missing/skipped, fake, smoke/gate/infra gagal, proven app failure dan evidence stale ditolak. UAT checklist wajib mencakup UAC yang diambil alih. Release gabungan hanya mengecualikan test ID yang diputuskan, mempertahankan smoke dan manual approval baru. |
| Preset QA | Scope/project `qa_profile`: lightweight (otomatis atau campuran sesuai UAC), manual (smoke otomatis + UAC user). Projects/Workspace/ScopeForm, HTTP dan kontrak API mengikuti pilihan. | Pilihan proyek memengaruhi proposal berikutnya; scope historis immutable. PO/model tidak bisa mengubah approval lama. |
| Stabilitas source mount | `workspace/command_seed.py`, `sandbox.py`, `supervisor.py`: setiap command mendapat source snapshot bounded unik, node_modules terpisah, inventory file/type/SHA diperiksa initializer sebelum target berjalan. Mount tidak mengacu pada path yang diganti import. | Tes seed membuktikan snapshot bertahan ketika source diganti; enam command Docker nyata node --test test.cjs mempertahankan file dan inventory. Mitigasi dan observabilitas, belum pembuktian akar penyebab insiden historis. |

### Verifikasi dan keterbatasan

Dari `apps/backend`, memakai venv dan Git modern pada PATH:

- Selection akhir: `pytest tests/domain/test_qa_manual_resolution.py tests/http/test_qa_choices.py tests/pipeline/test_scheduler.py tests/pipeline/test_contract_amendment.py tests/persistence/test_constraints.py -q --tb=short`: **65 passed**, 11,67 s.
- Selection sebelumnya termasuk migration dan seed: **27 passed**, 11,73 s;
  warning yang sudah dikenal tentang refleksi expression index SQLite.
- `npm run build`: lulus; warning ukuran chunk Office yang sudah ada.
- Grup luas domain/http/persistence/workers/agents/pipeline dihentikan pengguna:
  **700 passed, 2 failed**, 473,91 s. Dua kegagalan berasal dari ekspektasi teks
  guard `passed verification`, diperbaiki pada migrasi dan selection akhir lulus.
- Grup workspace/release/recovery/onboarding dihentikan pengguna:
  **93 passed**, 471,12 s, sebelum selesai. Bukan hasil full suite.
- Proses pytest panjang sudah berhenti dan `docker ps` kosong setelah cleanup.
  Worker, backend dan frontend demo tidak dinyalakan.

Bukti domain/HTTP memakai receipt fixture terkontrol, bukan QA model nyata.
Sampling memory hanya observational, lower bound jika memakai memory.current;
OOM, recovery SIGKILL, runtime provider dan workload proyek besar belum dibuktikan
oleh selection ini. Tidak ada independent review, commit, push atau restart.

## G. Tindak lanjut review a908ce7 (temuan a–e)

Status IN_PROGRESS / NOT_REVIEWED. Dikerjakan implementer; belum commit/push,
worker tidak di-restart. Bukan independent review.

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| a. Developer gagal di jeda TL→QA amendment | `_qa_plan_state` menganggap `waiting`: job planning aktif, QA meminta amendment, atau TL plan sukses yang revisinya belum punya job qa_plan. Developer baru menyerah setelah 3 observasi `dead` berturut-turut. | `tests/pipeline/test_qa_plan_wait.py` (race persis dari review) |
| b. Waktu submit tidak dibatasi + submit paralel | Tunggu QA plan dibatasi 240 dtk; habis → `qa_plan_pending` tanpa commit. `submit_candidate` memakai lock: retry setelah timeout tool menunggu submit pertama (maks 900 dtk) lalu mengembalikan hasil yang sama, atau `submission_in_progress`. | `test_wait_is_bounded_and_returns_suite_when_ready` |
| c. `qa_profile: manual` hilang saat revisi | Revisi tanpa field mewarisi profil versi sebelumnya; proposal PO selalu mewarisi (PO tidak dapat mengganti profil). Pilihan eksplisit user tetap berlaku. | `tests/domain/test_qa_decision_coverage.py` (edit user, proposal PO) |
| d. Keputusan user hanya menutup sebagian QA macet | Supervisor menyimpan diagnosis `unknown` turunan (`supervisor_derived`) saat atribusi aplikasi ditolak validator (diagnosis model tetap tersimpan, `derived_from`), output diagnosis tidak valid/tidak lengkap, atau revisi coverage gap gagal. `qualify` menerima verifikasi `incomplete` hanya untuk coverage gap terbukti: semua test kandidat lulus, base run lengkap, smoke/gate lulus, tanpa infrastruktur gagal. Migration 0008 membuka trigger waiver untuk status `incomplete`; downgrade menolak jika ada waiver semacam itu. Tanpa diagnosis dan tanpa job QA aktif, UI tidak lagi "menunggu" selamanya. | coverage gap → UAT; 5 kasus penolakan; pesan tanpa job aktif; migrasi up/down di tests/persistence |
| e. Polling memuat semua job | Query terbatas (`LIMIT 1`, `count`) per poll. | sama dengan (a) |
| f. test.cjs release | Tidak diubah; tetap dipantau di demo. | — |

Verifikasi (apps/backend, PATH Git modern): test baru 15 passed;
`tests/domain tests/persistence` + test baru 152 passed; `tests/domain` +
pipeline amendment/coverage repair/selector repair/test concerns/product loop/
UI submission/scheduler 219 passed. Suite penuh tidak dijalankan ulang.

Keterbatasan: provider error yang retryable tetap tidak membuat diagnosis turunan
(job retry dulu); bila job akhirnya gagal, UI menampilkan instruksi menjalankan
ulang QA, bukan keputusan. Submit pada cache dingin (base_build + build + gate)
masih bisa melebihi 1020 dtk; lock mencegah submit ganda tetapi tidak
memperpendek build. Developer tetap memegang slot execution selama menunggu.

## H. Rekomendasi kecepatan dan role (lanjutan G)

Status IN_PROGRESS / NOT_REVIEWED; belum commit/push/restart. Bukan independent review.

| Rekomendasi | Perubahan | Bukti |
| --- | --- | --- |
| 3.1 Cache install tanpa salin | Entry di-hash saat publish dan sekali per proses supervisor, lalu diikat ke fingerprint metadata (ukuran/mode/mtime/inode). Restore memakai hardlink (tanpa salin, tanpa hash ulang); fallback salin + verifikasi digest jika hardlink tidak didukung (mis. EXDEV). Bukan bind-mount langsung ke entry cache: entry dapat dievict (LRU) saat workspace masih memakainya, dan direktori cache tetap privat supervisor. Perubahan in-place lewat hardlink mana pun mengubah fingerprint → hash ulang → entry dibuang. File hasil restore 0644 (dependency di-mount readonly). | `tests/workspace/test_installation_cache_restore.py` (4); Docker nyata `test_readonly_dependencies_docker.py` 4 passed (restore tanpa download + build Vite) |
| 3.2 Receipt install metadata | Receipt `run_checks` memakai `meta:` fingerprint metadata, bukan hash isi. | `test_install_receipt_digest_ignores_tmpfs_masked_scratch` |
| 1a/1b Penantian QA plan | `run_checks` yang lulus menunggu QA plan (maks 240 dtk, tetap di bawah 900 dtk per tool) dan melaporkan `qa_plan` ready/pending/unavailable; submit hanya menunggu 30 dtk, lalu `qa_plan_pending` tanpa commit. Lock submit dari G tetap. | `tests/pipeline/test_qa_plan_wait.py` |
| TL: error amendment sampai ke TL | `_ask` menerima `check`; aturan plan TL (ui_contract wajib, revisi +1, aditif, tanpa keputusan scope) dijalankan di dalam putaran repair, dengan detail kontrol yang berubah. Tetap dicek ulang setelahnya untuk output yang sudah di-checkpoint. | `test_rejected_amendment_is_repaired_by_tl_in_the_same_job` |

Verifikasi: tests/agents, tests/domain, pipeline (amendment, qa_plan_wait,
review followups, product loop, coverage repair, test concerns, UI submission)
dan workspace (installation cache, fsutil, bounded dependency export): 415 passed.
Docker readonly dependencies: 4 passed.

Belum dikerjakan: pengukuran token nyata (butuh demo dengan provider nyata;
bandingkan dengan audit Mini Perpustakaan 19,5 juta token); belum ada benchmark
waktu restore cache sebelum/sesudah. Instruksi role tidak ditambah pada putaran
ini (pesan `next` di output tool yang menjelaskan qa_plan/submit).

## I. Review 2e5e8ac: jalur keputusan QA dan biaya tunggu

Status IN_PROGRESS / NOT_REVIEWED; belum commit/push/restart. Bukan independent review.

| Temuan | Perubahan | Bukti |
| --- | --- | --- |
| 1. Coverage gap macet jika revisi melempar exception | `_revise_coverage_gap` membungkus revisi: InvalidOutput/ValueError/ModelError non-retryable membuat diagnosis turunan dulu, lalu exception dilempar ulang. Outcome gagal yang retryable tidak membuat diagnosis (retry dulu). | `tests/pipeline/test_qa_decision_paths.py` (6) |
| 2. User tidak melihat dugaan bug aplikasi | Diagnosis turunan menyimpan `application_repair_issues`. `qualify`/`available` mengembalikan ringkasan diagnosis dan klaim aplikasi yang ditolak (temuan, alasan validator); diagnosis model (`derived_from`) divalidasi dan ikut `evidence_ids` serta dipin di keputusan. Panel QaResolution menampilkan ringkasan dan peringatan. Keputusan lama tanpa diagnosis model tetap valid (legacy evidence set). | `test_withheld_application_claim_is_shown_and_pinned_with_the_decision`; `tsc --noEmit` web lulus |
| 3. Tunggu QA plan membakar active_s | `RunContext.uncharged_wait`: heartbeat tetap memperpanjang lease, tetapi waktu tunggu dikurangkan dari charge active_s. Slot execution tetap dipegang. | `test_supervisor_owned_wait_is_not_charged_as_active_time` |
| 4. Batas 24 finding | `QaPlan` membatasi 24 test sehingga set keputusan ≤ 24; pemotongan diam-diam diganti error eksplisit. | — |
| 5. Urutan created_at | `latest_diagnosis` mengutamakan artifact `supervisor_derived`, lalu created_at/id. | test (2) dengan diagnosis model yang dibuat lebih akhir |
| 6. Masih terbuka | test.cjs release, submit cache dingin > 1020 dtk, peak memory. | — |

Benchmark restore hardlink (`python -m app.pipeline.benchmark --rounds 2`, tanpa
provider; hasil `efficiency-benchmark-hardlink-2026-10-11.json`, pembanding
`efficiency-benchmark-2026-10-11.json`, mesin dan fixture sama):

| Sampel | install (cache hit) | test | build | total |
| --- | --- | --- | --- | --- |
| cold, sebelum | 22,7 dtk | 1,8 | 11,1 | 162,8 dtk |
| cold, hardlink | 3,7 dtk | 1,8 | 10,7 | 155,6 dtk |
| warm, sebelum | 19,1 dtk | 1,7 | 11,6 | 36,4 dtk |
| warm, hardlink | 4,4 dtk | 1,6 | 11,0 | 19,1 dtk |

Peak memory proses benchmark: cold 945 MB, warm 183 MB (bukan peak per container).
Sisa 3,7–4,4 dtk adalah validasi lockfile/scan/fingerprint dan pembuatan hardlink.

Verifikasi: tests/agents, tests/domain, tests/workers, tests/pipeline: 614 passed,
1 failed (`test_processes::test_dead_worker_recovery...`) saat benchmark Docker
berjalan bersamaan; diulang sendiri: passed. Test baru putaran ini 35 passed.
Token nyata tetap belum diukur (butuh demo provider nyata + `app.pipeline.metrics`).
