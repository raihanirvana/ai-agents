# Review DEV-024/025/026 (M–P) — 12 Oktober 2026

Reviewer: Codex. Baseline: `5de593360ff82375a687b15643901cc97e5c567b`.
Target: working tree M–P, termasuk file baru. Pengguna mengotorisasi review,
perbaikan, commit, dan push. Reviewer menguji fix sendiri; ini bukan review
independen kedua atas fix. DEV-027/028 dan konfigurasi proyek demo di luar scope.

Verdict akhir: DEV-024/025/026 DONE / REVIEWED setelah perbaikan dan recheck.
Pelaksana mencatat regresi 964 passed dan implementasi DONE saat review berjalan;
reviewer mempertahankan catatan pelaksana dan menambahkan hasil review sendiri.

## Temuan dan perbaikan

| ID | Severity | Pemicu / dampak | Perbaikan dan bukti |
| --- | --- | --- | --- |
| M-01 | P1 | Suite gabungan dipotong menjadi 60 test; journey berikutnya hilang tanpa incomplete. | Semua journey dipertahankan. Unit test memakai 72 journey. Browser Docker benar-benar mengeksekusi 61 journey dan menemukan kegagalan journey terakhir (60 passed, 1 failed, 0 skipped). |
| M-02 | P1 | Seluruh journey gagal otomatis dikecualikan hanya karena menyentuh satu kontrol berubah, atau juga gagal di base. Bug lain dalam journey ikut lolos. | Mandatory failure tetap menggagalkan QA; changed-control/base failures hanya diagnosis. Pengecualian hanya dari keputusan QA pengguna yang masih valid untuk target/evidence asal. Test memeriksa kedua jalur bypass dan validitas waiver pengguna, termasuk evidence keputusan yang hilang. |
| M-03 | P1 | Target/suite accepted yang tidak tersedia bisa dilewati, atau menimbulkan error tanpa klasifikasi retry; suite/verification sumber tidak diperiksa terhadap pin. | Validasi manifest, build record, suite digest, mandatory IDs, verification dan evidence. Sumber unavailable/mismatch menghasilkan incomplete infrastructure. Test target/suite/evidence unavailable serta suite berbeda. |
| M-04 | P1 | Evidence hanya menyimpan ringkasan jumlah/ID; report lengkap, command dan suite gabungan hilang. Status passed tanpa baris test lengkap bisa diterima. | Simpan suite/digest, legacy IDs, source target/suite/verification/evidence/waiver IDs, dan receipt runner lengkap dalam acceptance artifact kandidat. Cocokkan invocation, target/suite digest, runner, exact IDs/counts/UAC, smoke, command dan exit code. Missing, duplicate, skipped, contradictory status, wrong digest, smoke palsu, missing command/infra flag/invocation semuanya incomplete. Jika semua journey user-waived, pin keputusan tetap dicatat sebagai waived tanpa klaim eksekusi. Base diagnosis mem-pin build bytes/config/toolchain/fixture/migration identity, bukan SHA saja. |
| N-01 | P2 | Lima meja memiliki center paling luar ±4,2 dan sisi meja ±4,95; dinding berada di ±4,42. | Batasi rentang center menjadi 7: sisi paling luar ±4,25. Browser menguji lima role serta interaksi avatar WebGL dan conversation. |
| N-02 | P2 | Metadata fake output UiSpec hilang dari DTO dan panel, walaupun message asal berlabel fake. | DTO meneruskan boolean asal; panel spec dan no_ui_change menampilkan FakeBadge. Query test dan browser memeriksa label. |
| N-03 | P3 | Dua state sah dengan screen/kind/copy sama tetapi target berbeda menghasilkan key React ganda. Target state/feedback juga tidak ditampilkan. | Key baris unik, tampilkan target testid. Browser memeriksa kedua target, feedback, copy literal (termasuk markup sebagai teks), token/font/spacing/radius, serta tidak ada duplicate-key error. |
| N-04 | P2 | Copy panjang tanpa spasi masih valid menurut UiSpec, tetapi membuat panel melebar 5.816 px pada viewport 1280. | Batasi minimum width section dan wrap teks; browser mereproduksi overflow sebelum fix, lalu panel tidak memiliki horizontal overflow sesudah fix. |
| O-01 | P2 | Driver mencari `name`/`previous_ui_spec` di seluruh prompt. Data percakapan dapat membuat TL mendapat UiSpec atau revisi memakai spec historis yang salah. | Decode object pada bagian Task; baca hanya field top-level task. Test quoted ui_spec dalam task role lain dan spec revision 99 dalam percakapan tetap memakai revision 1 dari task. Stage asli juga diuji dengan driver. |
| T-01 | P3 | Browser tests lama memakai locator UAC ambigu, copy badge lama, run pertama (sekarang bisa project_setup), serta fixture UAT tanpa verification pin. | Scope locator ke daftar UAC, gunakan copy sekarang, pilih PO dan pin data-run, lengkapi preview verification_id fixture. Tidak mengubah aturan approval produk. |
| W-01 | P2 | Cleanup macOS memeriksa status proses lalu environment; bila proses menjadi zombie di antaranya, AccessDenied membatalkan inspeksi dan dapat menahan recovery. | Setelah AccessDenied, periksa ulang bahwa proses zombie atau hilang. Proses hidup/masih tidak terbaca tetap memblokir cleanup. Dua kasus deterministik gagal sebelum fix; empat transisi diuji, lalu semua 98 worker tests (termasuk kedua SIGKILL) lulus. |

Fixture domain awal sempat mencoba mengubah Verification immutable dan memakai
unavailable_reason di luar enum. Fixture dikoreksi dengan receipt baru dan reason
`missing`; kegagalan setup tersebut tidak dihitung sebagai bug runtime.

## Pemetaan acceptance criteria

| Scope | Bukti |
| --- | --- |
| M / DEV-024: suite accepted menjadi gerbang wajib | `regression_entries`, `regression_suite`, `_regression_gate`, dan integrasi `_verify_attempt`; source pins diperiksa, setup fixture dimaterialisasi, seluruh test wajib dijalankan. Failure membawa owner tiket/journey/error ke Developer; incomplete tetap infrastructure. `test_regression_gate.py` dan browser Docker >60 journeys. |
| N / DEV-025: role API/web, kantor, panel UiSpec | Role union/format/projection, lima station, query detail dan UiSpec DTO/panel; `ui-spec.spec.ts`, office tests dan query test. OpenAPI tetap sesuai aplikasi melalui HTTP generated-contract checks; detail tiket memang respons dictionary tanpa schema DTO. |
| O: fake driver dan test | FakeUiUxDriver deterministik berlabel, salinan template tidak dimutasi, amandemen aditif, fallback role lain; unit tests dan real stage `test_labelled_fake_ui_ux_driver_runs_the_real_stage`. |
| P / DEV-026: telemetry UI/UX | Semua ROLES muncul di `by_role`, termasuk zero-job role; jobs/phases/usage/unknown-usage tetap dibedakan. Test memeriksa UI/UX job, model duration, token dan cost; QA tanpa job tetap nol job. |

Pin pada UAT sudah ada. Pemilihan **accepted** untuk gerbang dibenarkan: hanya
perilaku yang telah terintegrasi masuk accepted tip. Tidak perlu snapshot baru
atau mengeksekusi suite dari kandidat yang UAT-nya approved tetapi belum terintegrasi.

Perubahan wording/scope tidak memberi agent waiver otomatis. Jika perlu mengubah
perilaku accepted, revisi tiket asal dan verifikasi suite barunya, atau gunakan
keputusan pengguna yang memenuhi policy pin evidence. Jangan mengubah test
regresi untuk menyembunyikan kegagalan.

## Verifikasi aktual

Dari `apps/backend`, dengan Node 22.20.0 dan Docker tersedia:

```sh
.venv/bin/python -m pytest tests/pipeline tests/agents tests/domain tests/workers tests/persistence tests/release tests/http -q --tb=short
.venv/bin/python -m pytest tests/domain/test_regression_gate.py tests/pipeline/test_fake_ui_ux_and_metrics.py tests/pipeline/test_regression_browser.py -q --tb=short
```

Dari root:

```sh
API_TEST_PYTHON="$PWD/apps/backend/.venv/bin/python" npx playwright test --config playwright.web.config.ts
npm run build
```

PATH test backend memasukkan Node 22 dan `/Users/23061535/homebrew/bin` agar Git/Docker
tersedia. Browser memakai Vite asli, Chromium, dan API disposable; state tertentu
memakai contract-shaped API fixtures. Tidak memakai database proyek demo.

| Selection | Hasil aktual |
| --- | --- |
| Tujuh grup backend penuh | 1.055 passed, 1 failed worker recovery `[0.5]`, 12 warnings expression-index SQLite, 1.273,70 detik. |
| Recheck gate/fake/actual Docker browser setelah memperketat receipt/pin base | 30 passed, 62,50 detik; browser menjalankan semua 61 journey. |
| Recheck akhir gate/waiver/fake setelah mencatat all-waived source pins | 29 passed, 5,11 detik. |
| Reproduksi race macOS (sebelum fix) | 2 failed (zombie/gone), 8 passed. |
| Seluruh workers setelah fix race | 98 passed, 17,67 detik, termasuk kedua SIGKILL dan denial yang tetap memblokir cleanup. |
| Web lengkap | 41 passed, 33,4 detik. |
| Reproduksi copy panjang (sebelum fix) | 1 failed, horizontal overflow 5.816 px. |
| Recheck panel setelah wrapping | 3 passed, 6,1 detik; total 42 kasus browser unik terverifikasi. |
| Build frontend akhir | TypeScript/Vite lulus, 8,54 detik; warning chunk Three.js >500 kB tetap ada. |

Failure full run terjadi pada `assert gone(pgid)`. Database fixture menunjukkan
reaper berhenti karena proses belum bisa diverifikasi, sehingga retry belum dibuat;
aturan fail-closed bekerja. Dua SIGKILL lulus saat dijalankan terpisah sebelum fix.
Follow-up menemukan race status/environment macOS dan mereproduksinya deterministik;
traceback awal sendiri tidak menunjukkan PID mana yang inspeksinya ditolak.
Runtime mempertahankan blocking untuk proses hidup yang masih tidak terbaca.

Collection awal memuat 1.056 kasus. Lima kasus gate/pin tambahan dan empat transisi
inspection ditambahkan sesudah collection. Dengan recheck 29 kasus gate/fake, 30
termasuk Docker, serta 98 workers, **seluruh 1.065 kasus backend unik pada tree final
terverifikasi**, termasuk kasus full run yang semula gagal. Full run 21 menit tidak
diulang seluruhnya; semua kode yang berubah setelah collection diperiksa pada
selection terkait. `git diff --check` lulus.

Log lokal:
`/private/tmp/dev024-full.log`, `dev024-final-recheck.log`, `dev025-web-final.log`,
`dev025-build-final.log`, `dev024-workers-final.log`, `dev024-waiver-final.log`,
`dev025-panel-final.log`. Log, cache Python, build, dan artefak browser tidak di-commit.

Git/DB/build/browser/container berjalan nyata. Provider/driver tetap fake berlabel;
fixture synthetic receipt tidak membuktikan QA/UAT produk, kualitas UI/UX model,
atau kompatibilitas provider nyata. Font panel memakai font yang tersedia beserta
fallback; tidak mengunduh font eksternal. Runner memiliki timeout/budget finite;
jumlah journey besar yang belum selesai tetap incomplete, tanpa pass parsial.

## Diff dan handoff

Diff lengkap termasuk file baru tersedia melalui `git show` setelah commit.
File baru: `pipeline/regression.py`, `pipeline/fake_ui_ux.py`,
`tests/domain/test_regression_gate.py`, `tests/pipeline/test_regression_browser.py`,
`tests/pipeline/test_fake_ui_ux_and_metrics.py`, `tests/web-browser/ui-spec.spec.ts`,
dan dokumen review ini. Supporting fix/test: `workers/runtime.py`,
`tests/workers/test_macos_identity.py`; browser fixtures/locators lama diperbarui. Tiket berikutnya DEV-027 (demo terukur) tetap TODO.
