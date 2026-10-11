# Review DEV-022/023 (I–L) — 12 Oktober 2026

Reviewer: Codex. Baseline: `71869618e9f9d6f4c00fb9b578b97ed4235c3fd7`.
Target: working tree I–L, termasuk file test baru. Pengguna mengotorisasi review,
fix, commit dan push. Reviewer memperbaiki dan menguji temuannya sendiri;
ini bukan independent review kedua atas fix. DEV-024 dan seterusnya serta
aktivasi flag proyek demo berada di luar assignment.

Verdict akhir: DEV-022 dan DEV-023 DONE / REVIEWED setelah fix dan recheck;
tidak ada blocker tersisa pada scope I–L yang diuji.

## Temuan dan perbaikan

| ID | Severity | Pemicu / dampak | Perbaikan dan bukti |
| --- | --- | --- | --- |
| I-01 | P2 | TL merestatakan parent dari UiSpec untuk kontrol teknis baru. Merge warisan memulihkan wording accepted lama sebelum validasi spec, sehingga output yang tepat justru ditolak. | Validasi output TL sebelum restorasi supervisor, lalu pin seluruh kontrol UiSpec sebagai prioritas di atas warisan. Test stage memakai parent dengan wording accepted berbeda dan kontrol teknis scoped; dua model call cukup, kontrak menyimpan parent persis seperti spec. Instruksi/context TL menjelaskan parent verbatim dan prioritas spec. |
| I-02 | P2 | Amandemen copy/state tanpa `ui_contract` TL membuang kontrol teknis dari kontrak sebelumnya dan gagal pemeriksaan aditif. | Pin seluruh kontrak sebelumnya setelah derivasi, termasuk bila TL tidak mengirim kontrak. Test QA → UiSpec r2 → TL tanpa kontrak mempertahankan instrumentation container dan menaikkan revisi tepat sekali. |
| K-01 | P2 | `assert_value(s)` menerima case/spasi yang berubah, padahal runner membandingkan nilai form persis; input asli kosong malah ditolak. | Pisahkan matching teks tampilan dari nilai form; nilai asli termasuk string kosong sah. Unit test menolak nilai yang diubah; browser Docker menunjukkan perubahan nilai aplikasi tetap gagal. |
| K-02 | P2 | `{title}` berulang boleh diisi input berbeda dalam satu template; pesan dialog juga menerima template literal yang belum diisi. | Placeholder berulang memakai backreference input yang sama; dialog wajib melalui rendering copy, termasuk template. Test menolak campuran dua judul dan pesan `Hapus {title}?`, tetapi menerima rendering dari input asli. |
| K-03 | P2 | Feedback `native_confirm` boleh ditulis QA sebagai alert/prompt dengan pesan sama. Bentuk locator CSS testid yang sah dalam kontrak tidak dikenali validator dialog. | Cocokkan jenis native feedback dan copy untuk testid locator akhir; dukung bentuk CSS testid dan bounded nth. Browser fixture dengan alert menggantikan confirm tetap gagal, unit test memeriksa tipe dan vocabulary. |
| J-01 | P2 | CSS esbuild mengubah `#FFFFFF` menjadi `#fff`, atau `#FF0000` menjadi `red`; build benar ditolak. Sebaliknya hex opaque dihitung hadir di awal nilai hex transparan yang berbeda. | Normalisasi RGB hex pendek/nama CSS pendek hasil minify; alpha harus opaque dan token hex harus utuh. Test shorthand, red/gray dan alpha berbeda; build nyata menerima `#fff`, `#000`, `red`. |
| J-02 | P2 | Copy satu karakter tidak diperiksa. Copy JS dengan apostrof atau escape `\xE9`, hex Unicode huruf besar dan surrogate pair bisa ditolak walaupun hasil bundler benar. | Semua fragmen literal nonkosong wajib hadir; decode escape JS secara statis tanpa mengeksekusi target. Test copy hilang, single quote, café, kutip Unicode dan emoji. Output esbuild lokal mengonfirmasi spelling `\xE9`/`\u201C` yang tidak cocok dengan pencarian JSON sebelumnya. |
| L-01 | P2 | Pydantic mengonversi marker PO `0`/`"false"` menjadi false, melewati UI/UX meski domain/API mensyaratkan boolean asli. | `StrictBool` pada TicketProposal/PoRevision. Delapan kasus nonboolean ditolak; fake PO create/revision dan HTTP create/edit/replay membuktikan bool tersimpan serta approval tetap manual. |
| T-01 | P3 | Test SIGKILL menunggu proses tercatat lalu mematikan worker sebelum pesan spawn ditulis, tetapi selalu mewajibkan pesan itu dalam evidence recovery. Clock beku juga memakai selisih arbitrer dari waktu sebelum setup. | Tunggu pesan durable yang akan diuji sebelum SIGKILL; gunakan expiry lease aktual. Parametrik jeda log 0/0,5 detik menutup race tanpa retry, skip, atau mengurangi assertion. Production worker tidak diubah. |

Run reproduksi awal menghasilkan 23 failed / 22 passed. Dua assertion test dialog
awal masih berhenti pada validasi fixture karena tidak memiliki assertion outcome;
fixture dikoreksi. Run antara juga menemukan query log test recovery belum memakai
prefix timestamp, serta fixture integrasi spec belum memenuhi minimum tiga warna;
keduanya diperbaiki pada test. Recheck akhir menguji fixture yang valid, bukan
menganggap semua kegagalan sementara itu bug runtime.

## Pemetaan acceptance criteria

| Scope / AC | Bukti |
| --- | --- |
| I: TL menerima spec; kontrol derived verbatim, tambahan teknis saja | `runtime._technical_plan`, `add_technical_controls`, prioritas merge pinned; `test_ui_spec_stage` menguji derivasi tanpa TL contract, repair redefinition, wording warisan, dan amandemen yang mempertahankan tambahan lama. |
| J: Developer menerima spec dan referensi pinned | Context implement memuat spec/token/copy/state/feedback; policy/parameter `read_design_reference` diperiksa di `test_ui_ux_role`, termasuk larangan bagi QA/TL dan batas/path referensi. Tidak ada fetch runtime. |
| J: run_checks dan submit memeriksa bundle | `ui_inventory.check_build`, `DeveloperChecks._ui_inventory`, `ProductWorkspace.submit`; test statis mencakup copy/placeholder/color serta legacy tanpa spec. Test integrasi build nyata memeriksa failure fields, tidak ada kandidat saat copy/token hilang, lalu satu kandidat setelah perbaikan dalam job yang sama. |
| K: expected berasal dari copy atau original input | `check_expected_values` pada propose_tests dan revisi suite yang telah memenuhi asal spec; fixtures dimaterialisasi per test. Test menolak parafrase, input test lain, template campuran/belum dirender, dan dialog keliru. |
| K: runner teks case-insensitive, input tetap persis | Trusted runner memakai ignore_case untuk assert_text/contains; browser Docker menunjukkan kapitalisasi/spasi display lulus, wording lain dan nilai input berubah gagal. Dialog native juga mempertahankan jenisnya. Runner code digest mengikuti mekanisme upgrade target yang sudah ada. |
| L: PO menandai UI change; scope approval tetap manual | StrictBool output, domain `_scope`/TicketVersion.scope, API Scope dan normalisasi payload idempotensi, scheduler skip false. Test create/revision fake PO, domain inheritance/historical shape, HTTP validation/replay, serta stage skip. OpenAPI/requests diuji terhadap aplikasi. |

Revisi suite historis tetap mengikuti mekanisme asal: assertion/input tidak boleh
ditulis ulang sembarangan, setup baru hanya berasal dari langkah prerequisite
yang lulus atau input asli dengan source witness. Penjagaan UiSpec berlaku bila
suite asal sudah memenuhi spec; ini tidak memberi model waiver untuk expected baru.

## Verifikasi aktual

Dari `apps/backend` dengan `.venv` yang sudah tersedia:

```sh
env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest tests/pipeline tests/agents tests/domain tests/workers tests/persistence tests/http -q --tb=short
env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest tests/pipeline/test_ui_inventory_spec.py tests/pipeline/test_ui_contract_submission.py tests/pipeline/test_review_followups_2026_10_11.py tests/pipeline/test_qa_spec_expectations.py tests/pipeline/test_ui_spec_browser.py -q --tb=short
```

| Selection | Hasil aktual |
| --- | --- |
| HTTP awal, termasuk generated-contract checks | 71 passed, 2 warnings, 12,44 detik; dependensi httpx2 ternyata sudah tersedia. |
| Stage/spec expected/inventory/domain marker/worker processes/browser | 82 passed, 70,00 detik. |
| PO runtime + HTTP commands dengan tambahan marker/replay | 33 passed, 6,01 detik. |
| Recheck akhir inventory, submission, followup checks, expected QA, browser | 52 passed, 60,47 detik. |
| Enam grup regresi lengkap | 999 passed, 12 warnings refleksi expression index SQLite, 693,00 detik, 0 gagal. |
| Build frontend (`npm run build`, dari root) | TypeScript dan Vite lulus; warning chunk kantor Three.js >500 kB, build selesai 7,18 detik. |

Tambahan escape Unicode, integrasi build spec, serta larangan template dialog
literal dibuat sesudah collection run lengkap. Recheck 52 test tersebut memakai
tree final dan mencakup seluruh perubahan kode setelah collection.
Collection tree final berisi 1.005 test: seluruh 999 kasus run lengkap serta
enam kasus tambahan pada recheck 52 test lulus. Total **1.005 kasus unik**
terverifikasi; tidak ada failure tersisa. `git diff --check` juga lulus.
Log lokal: `/private/tmp/dev022-regression.log`, `dev022-final-recheck.log`,
`dev022-targeted-final.log`, `dev022-po-http.log`, `dev022-http.log`.
Log dan artefak runtime tidak di-commit.

Git/DB/build/browser/SIGKILL dijalankan nyata; provider/driver tetap fake berlabel.
Tidak ada panggilan provider nyata dan tidak ada klaim kualitas model, QA/UAT
proyek nyata, atau kompatibilitas provider dari hasil fixture ini. Pemeriksaan
bundle membuktikan keberadaan literal, bukan visibility/behaviour/reachability.
Frontend diverifikasi lewat build; browser UI platform, deployment dan aktivasi
UI/UX proyek demo tidak termasuk verifikasi ini.

## Diff dan handoff

Diff lengkap termasuk file baru tersedia lewat `git show` sesudah commit.
Patch review disiapkan di `/private/tmp/DEV-022-023-review.patch`.
File baru: `test_ui_change_marker.py`, `test_qa_spec_expectations.py`,
`test_ui_inventory_spec.py`, `test_ui_spec_browser.py`, serta dokumen review ini.
DEV-024 dan seterusnya tetap mengikuti backlog; review ini tidak mengerjakannya.
