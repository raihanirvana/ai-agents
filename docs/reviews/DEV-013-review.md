# Review DEV-013 — Onboarding repository existing

Tanggal: 2026-10-05. Reviewer: Claude (Sonnet 5.5), independen dari implementer Codex.
Status setelah recheck: **REVIEWED untuk DEV-013**; lihat bagian recheck Codex di akhir. R8 keseluruhan belum ditutup.
Baseline HEAD: `6b7c8ad` (DEV-012). Snapshot yang direview: index staged (36 file, +1.938/−31 baris).
Verdict review awal: **NEEDS_FIX**. Isolasi sumber (hooks/fsmonitor/filter tidak berjalan, repo asli tidak berubah,
clone independen tanpa alternates, validasi tree sebelum checkout) kuat dan tidak ditemukan celah. Namun waiver per
failure, yang menjadi inti AC 4, tidak berfungsi dengan keluaran Node TAP nyata, dan ada tiga bug lain. Semuanya
diperbaiki reviewer atas instruksi pengguna dengan regresi. Perbaikan adalah self-check reviewer dan **menunggu
re-review independen**; ini bukan penutupan R8.

Scope yang dibaca penuh: `app/onboarding/{source,runtime,requests,__main__}.py`, perubahan `pipeline/{gates,runtime,
workspace}.py`, `workspace/supervisor.py`, `worker.py`, `agents/context.py`, `http/{application,queries,schemas}.py`,
kontrak TS, `Onboarding.tsx`, semua tes onboarding/HTTP/browser, `docs/decisions/onboarding.md`, handoff, ringkasan
bukti `DEV-013-results.json`. Hasil kualifikasi Hermes/OpenRouter tidak diulang (berbayar); angka di ringkasan dicek
konsistensi dan kebocoran secret saja, bukan dicocokkan dengan DB/artefak privat.

## Yang sudah baik

- **Sumber tidak dieksekusi.** Semua Git terhadap sumber berjalan dengan environment broker (hooks, fsmonitor, config
  global/system mati); setiap `filter.*.clean|smudge|process|required` lokal, termasuk dari include, di-override;
  `GIT_OPTIONAL_LOCKS=0` mencegah tulis index. Tes memakai sumber bermusuhan (hook, fsmonitor, filter + `.gitattributes`)
  dan memeriksa penanda tidak dibuat serta sidik jari seluruh file sumber tidak berubah.
- **Clone independen.** Bundle → bare repo dengan template kosong, tanpa alternates, refs/config/hooks sumber tidak
  disalin; objek tetap terbaca setelah sumber dipindah. Tree divalidasi (symlink/submodule, `.env`/`.npmrc`/kunci,
  pola credential, batas 20.000 file/64 MiB) sebelum worktree patch dibuat.
- **Patch eksplisit.** Dipin ke source SHA dan digest, diterapkan hanya di worktree milik import (`git apply` menolak
  path `.git` dan keluar worktree), dan tree hasilnya divalidasi lagi.
- **Publikasi.** Laporan, pin pesan, state/event dan terminal job dalam satu transaksi; job basi tidak dapat
  menginisialisasi accepted tip; reuse import hanya bila SHA/digest patch/ref cocok.
- **Manifest.** Egress hanya untuk `npm ci --ignore-scripts` tetap; env, image, perintah divalidasi parser DEV-005.

## Temuan dan perbaikan

### R013-01 — P1: waiver per failure tidak pernah cocok dengan keluaran `node --test` nyata

Lokasi: `pipeline/gates.py` (`per_failure`). Tes `test_baseline_waiver_per_test_allows_new_green_tests...` memakai TAP
sintetis tanpa `location:`, stack, atau baris rencana `1..N`, sehingga lulus tanpa membuktikan klaim "penambahan test
hijau tidak mengubah identitas failure lama".

Dengan keluaran Node 22.20.0 nyata (tiga test gagal, satu test hijau baru disisipkan **di atas**), ketiga fingerprint
berubah. Tiga penyebab terpisah, ditemukan satu per satu:

1. `location: '/work/test.cjs:5:1'` dan frame stack proyek memuat baris:kolom, yang bergeser saat kode di atasnya berubah.
2. Frame internal Node (`Test.start`, `processPendingSubtests`, `postRun`...) bergantung pada posisi test di file dan
   test sebelumnya, bukan pada failure.
3. Failure terakhir menyertakan baris rencana `1..N` dan ringkasan, sehingga **menambah satu test mengubah fingerprint
   failure terakhir**.

Akibat: waiver pengguna berhenti cocok begitu kandidat menambah test atau menggeser baris, sehingga tiket pada repo
dengan baseline merah tidak dapat lolos QA tanpa waiver baru berulang kali. Perbaikan: fingerprint membuang baris/kolom
file proyek, frame `node:` internal, dan baris rencana/ringkasan; nama test, tipe/kode/pesan error, expected/actual
dan frame proyek tetap. Pesan atau assertion yang berbeda tetap failure baru. Regresi memakai dua keluaran Node nyata
(`tests/onboarding/fixtures/`): identik setelah test disisipkan, berbeda bila pesan error berubah.

### R013-02 — P2: repo yang diperbaiki sesudah baseline diblokir tidak dapat di-onboard lagi

Lokasi: `onboarding/source.py` `import_source`. Pesan blocker menyuruh "fix runner/source before onboarding", tetapi
receipt import pertama mem-pin SHA sumber: commit perbaikan apa pun menghasilkan "managed import already belongs to
another request/source; cannot reset it" dan proyek tidak pernah dapat di-onboard (jalan keluar hanya proyek baru).
Perbaikan: runtime meminta `replace_uninitialized=True` (hanya saat DB belum punya accepted tip). Import sebelumnya
yang receipt-nya terbukti diarsipkan ke `archive/onboarding-<request>-*` (bukan dihapus atau di-reset) lalu import baru
dibuat. Repo tanpa receipt, ref baseline yang berubah, dan proyek yang sudah diinisialisasi tidak pernah diganti.

### R013-03 — P2: daftar perubahan lokal tak terbatas disimpan di baris proyek dan dikirim tiap snapshot

Lokasi: `inspect_source` → `onboarding_detail.dirty_status`. `status --untracked-files=all` pada sumber dengan
`node_modules` yang tidak di-ignore menghasilkan ratusan ribu entri; semuanya masuk `projects.workflow` dan DTO board
yang di-fetch pada setiap event. Perbaikan: daftar tampilan dibatasi 200 entri dengan `source_status_total`, dan
keadaan lengkap dibandingkan lewat `status_digest` (perubahan di luar sampel tetap terdeteksi; tes). GUI menampilkan
"dan N perubahan lain".

### R013-04 — P1 (harness DEV-010): pembersihan container bersamaan salah melapor "owned container remains"

Handoff mencatat satu kegagalan full suite (`test_stop_terminates_an_inflight_browser_runner...`), lulus saat diulang,
"tidak diperbaiki". Itu bukan flaky acak: jalur stop dan `finally` runner sama-sama memanggil `remove_owned`, dan
`docker rm -f` pada container yang sedang dihapus oleh pihak lain menjawab "removal already in progress" sementara
container masih terlihat sesaat. Reproduksi dengan Docker nyata: dua pembersih bersamaan pada satu container gagal pada
**18 dari 25 ronde**. Dampak produk: setiap stop pada run harness yang berjalan dapat berakhir `cleanup_failed`
(needs_human) padahal tidak ada kebocoran. Perbaikan: menunggu hingga 15 detik sambil mengulang `rm -f`; hanya jawaban
not-found eksplisit yang dianggap hilang, dan kepemilikan label tetap diperiksa. `PreviewService._remove_container` (kode
DEV-011) memakai pola yang sama dan diperbaiki serupa. Setelah perbaikan 0 dari 25. Regresi: stub (penghapusan lambat,
container yang tak pernah hilang tetap dilaporkan), dan Docker nyata dengan dua thread.

### R013-05 — P3: dua modul tes memutus koleksi seluruh suite Windows

`tests/http/test_onboarding.py` dan `tests/onboarding/test_waivers.py` mengimpor paket POSIX (`fcntl`) tanpa
`importorskip`, sehingga `pytest tests` di Windows berhenti dengan "Interrupted: 2 errors during collection" (handoff
tidak menjalankan suite Windows). Ditambahkan `importorskip` sesuai pola tes POSIX lain.

## Observasi (tidak diubah)

- **O1 — Patch ditempel lewat textarea.** Browser menormalkan `\r\n` menjadi `\n`; patch dari file CRLF atau tanpa
  newline akhir dapat gagal `git apply`. Batas field 1.000.000 karakter vs batas body 1 MiB: patch besar dapat ditolak
  dengan 413, bukan 422. Disarankan unggah berkas.
- **O2 — Tidak ada manifest bawaan di form.** Pengguna harus menempel JSON manifest; belum ada pratinjau runner.
- **O3 — Signature per failure memuat seluruh stderr.** Test baru yang menulis ke stderr membatalkan semua waiver.
  Konservatif, tetapi dapat mengejutkan.
- **O4 — Riwayat Git tidak dipindai secret.** Hanya tree HEAD yang diperiksa; seluruh riwayat dipindahkan ke clone
  supervisor (tidak masuk sandbox).
- **O5 — Pemindaian blob satu proses Git per file.** Hingga 20.000 proses; sebaiknya `cat-file --batch`.
- **O6 — Waiver di GUI tetap meminta ID artefak fingerprint manual.** Daftar fingerprint baseline per failure belum
  ditampilkan sebagai pilihan.
- **O7 — Kalimat dokumen bertentangan.** `onboarding.md` menyebut semua perintah target "tanpa jaringan" dan "install
  egress" dalam satu paragraf; manifest yang divalidasi mengizinkan egress hanya untuk install.
- **O8 — Satu import berhasil tidak dapat diubah.** Setelah accepted tip ada, sumber baru tidak dapat disinkronkan
  (DEV-014).

## Verifikasi reviewer

| Perintah | Hasil |
| --- | --- |
| WSL + Docker, `tests/onboarding tests/http/test_onboarding.py` pada tree awal | 20 passed |
| Reproduksi R013-01: fingerprint Node nyata sebelum/sesudah test disisipkan | 3/3 berbeda (tree awal), 3/3 sama (sesudah) |
| Reproduksi R013-04: dua `remove_owned` bersamaan, Docker nyata | 18/25 gagal (tree awal), 0/25 |
| WSL + Docker, suite backend lengkap sesudah perbaikan | **804 passed**, 0 gagal (506 detik) |
| WSL, `test_harness`, `test_source`, `test_waivers` (diedit setelah run di atas dimulai) | lulus, tanpa container tersisa |
| Windows (`--ignore=tests/workspace --ignore=tests/runtime_spike`) | 557 passed, 20 skipped |
| GUI `playwright.web.config.ts`, `npm run build`, `tsc` | 32 passed, build/tsc lulus |
| `git diff --check` | lulus |

Tidak dijalankan: kualifikasi Hermes/OpenRouter nyata, browser preview/API.

## Pemetaan AC (penilaian reviewer)

| AC | Penilaian |
| --- | --- |
| 1 Clone independen | Terpenuhi |
| 2 Dirty dilaporkan, patch eksplisit | Terpenuhi; R013-03 (daftar dibatasi), O1 |
| 3 Baseline install/build/test/start, failure terpisah | Terpenuhi; R013-02 (retry sesudah memperbaiki sumber) |
| 4 Waiver per failure, failure baru/UAC/infra ditolak | Terpenuhi hanya setelah R013-01 (sebelumnya waiver tidak pernah cocok pada TAP nyata) |
| 5 Manifest/instruksi dalam izin, stack unsupported | Terpenuhi |
| 6 Fitur sampai Accepted tanpa mengubah sumber | Bukti kualifikasi implementer tidak diulang; isolasi sumber diverifikasi tes |

## File yang diubah reviewer

- `app/pipeline/gates.py` (fingerprint), `app/pipeline/harness.py` dan `app/preview/service.py` (race cleanup).
- `app/onboarding/{source,runtime}.py` (arsip import, batas status), `contracts/api/types.ts`, `Onboarding.tsx`.
- Tes: `tests/onboarding/{test_waivers,test_source}.py` + `fixtures/` (dua keluaran Node nyata), `tests/pipeline/test_harness.py`,
  `tests/http/test_onboarding.py` (importorskip).
- `docs/decisions/{onboarding,pipeline}.md` (klaim waiver dikoreksi), `docs/reviews/DEV-013-review.md`, catatan backlog.

Tidak ada stage/commit/push oleh reviewer. Status tiket DEV-013 tetap **DONE** (AC terpenuhi setelah perbaikan),
dengan review **NEEDS_FIX → diperbaiki, menunggu re-review**.

## Recheck perbaikan — Codex, 2026-10-05

Verdict: **REVIEWED (DEV-013 saja), siap commit/push**. Lima fix Claude diperiksa pada file aktual beserta tes
regresinya; tidak ditemukan blocker baru. Baseline tetap `6b7c8ad`; hasil berlaku pada diff final yang disertakan
dalam commit DEV-013. Codex adalah implementer awal, bukan penulis lima fix reviewer. Recheck ini menilai diff fix
secara terpisah dari review awal independen Claude; tidak diklaim sebagai independent review ulang implementasi
sendiri atau penutupan checkpoint R8 yang juga mencakup DEV-014/015.

| Pemeriksaan final | Hasil |
| --- | --- |
| WSL Ubuntu + Docker, `/root/aiagent-dev002-venv/bin/python -m pytest tests -q` dari `apps/backend` | **804 passed**, 0 failed/0 skipped, 516,34s |
| Windows, `.venv/Scripts/python.exe -m pytest tests -q --ignore=tests/workspace --ignore=tests/runtime_spike` | **557 passed, 20 skipped**, 75,82s |
| `npm run build` | TypeScript dan Vite lulus |
| `npx playwright test --config playwright.web.config.ts` | **32 passed**, 23,5s |
| Dua pemanggil `PreviewService._remove_container` bersamaan pada container berlabel milik percobaan, Docker nyata | **8/8 ronde lulus**; tidak ada container milik percobaan tersisa |
| `git diff --cached --check`, termasuk files baru | Lulus |
| Inspeksi staged files untuk ukuran dan pola credential | Tidak ada file >1 MiB atau credential tak terduga; dua kecocokan adalah token sintetis tes dan prosa historis |

R013-01 diuji dengan dua fixture TAP Node nyata, termasuk perubahan error yang harus menghasilkan fingerprint baru.
R013-02/03 diuji lewat retry/arsip/idempotency dan perubahan di luar sampel 200 status. R013-04 mencakup regresi
harness stub dan Docker nyata dalam full suite, ditambah cleanup PreviewService bersamaan di atas. R013-05
terkonfirmasi lewat koleksi dan eksekusi suite Windows. `.gitattributes` hanya pada `tests/onboarding/fixtures/`
menjaga LF dan spasi baris kosong keluaran TAP asli; aturan whitespace file lain tetap berlaku.

AC 6: `examples/dev013/summary.py` dieksekusi ulang terhadap `/root/aiagent-dev013/qualification-01`, output ke
`data/dev013/rechecked-summary.json` (gitignored). Hasil identik dengan `docs/spikes/DEV-013-results.json`.
Delapan artefak report/target/evidence/integration diperiksa via `ArtifactStore.read_bytes` (ukuran/checksum),
dan ref accepted managed Git cocok dengan commit kandidat serta objek commit tersedia. Ini memvalidasi bukti
tersimpan, bukan mengulang Hermes/OpenRouter berbayar atau UAT manual. Field `independent_review` pada summary
qualification tetap merupakan status historis saat percobaan; status review terbaru berada di dokumen ini.

Handoff/backlog dikoreksi untuk membedakan hasil awal dari final serta menghapus klaim cleanup belum diperbaiki.
Observasi O1-O8 tetap tercatat sebagai follow-up; DEV-014 tidak dimulai. Commit/push diotorisasi pengguna,
tanpa deployment.
