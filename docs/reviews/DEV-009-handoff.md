# DEV-009 implementation handoff

Status implementasi: DONE setelah perbaikan review. Review awal Codex: NEEDS_FIX;
temuan sudah diperbaiki dan diverifikasi. Re-review perbaikan independen belum dilakukan,
R5 belum ditutup. Lihat [laporan review](DEV-009-review.md).
Pelaksana: Claude (Sonnet 5.5), 2026-10-05. Baseline: `62a25ac` (DEV-008).
Tidak ada commit/push DEV-009. Ini handoff implementer, bukan independent review.

## Hasil dan file

GUI React memakai `ApiClient` DEV-008: login code lokal, proyek/brief, board per phase,
detail tiket, chat PO, usulan revisi dengan diff, approval scope satuan/batch, UAT,
aktivitas run, dan tampilan conflict/error. Keputusan: `docs/decisions/gui.md`.

| File | Tanggung jawab |
| --- | --- |
| `apps/web/src/App.tsx`, `main.tsx` | Gerbang sesi, route hash, `CrashGuard` |
| `apps/web/src/workspace.tsx` | Snapshot, SSE, command idempotent per proyek |
| `apps/web/src/components/{Login,Projects,Workspace,Board,Ticket,Chat,Activity,ui}.tsx` | Layar dan tindakan |
| `apps/web/src/{diff,format}.ts`, `api/instance.ts`, `style.css` | Diff scope, label, client tunggal, gaya |
| `contracts/api/types.ts` | `Run.result` menjadi `| null` (backend memang mengirim null pada run baru) |
| `playwright.web.config.ts`, `tests/web-browser/{server.py,flow.spec.ts,states.spec.ts,review-fixtures.ts,review-regressions.spec.ts}` | Fixture dan 29 tes browser |
| `README.md`, `docs/decisions/gui.md`, backlog | Cara menjalankan, keputusan, log |

Berkas baru perlu dibaca langsung (`git status --short`, lalu direktori di atas); `git diff`
tidak mencakup untracked files. Backend tidak diubah.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| 1: brief -> proposal profile/menu/transaksi, diskusi, edit, terima/tolak, approve scope | `flow.spec.ts` test 1 (backend, scheduler, runtime terstruktur nyata; model = FakeProvider berlabel): breakdown 3 tiket, usulan revisi ditolak lalu diterima, edit scope manual -> v3, batch approve, reload |
| 2: board dari backend, drag/drop prioritas, approval eksplisit | test 1 (drag, urutan bertahan setelah reload; approval dua langkah); phase tidak berubah lewat drag. UAT: `states.spec.ts` test 2. Release: tidak ada UI (lihat known issues) |
| 3: detail scope version, dependency, pesan, bukti, status kerja | `states.spec.ts` test 2 (kandidat, verifikasi, dependency, approval) dan `flow.spec.ts` test 1 (versi scope, pesan) |
| 4: waiting input/quota, manual, waiver, target identity, artefak unavailable, fake | `states.spec.ts` test 1-3; label fake juga pada backend nyata (`flow.spec.ts` test 1 dan 3) |
| 5: streaming/reconnect/reload dan conflict/error | reload di test 1; conflict 409 nyata di `flow.spec.ts` test 2; stream putus di `states.spec.ts` test 3; snapshot refresh client: tes DEV-008 |
| 6: usable di desktop/laptop tanpa Three.js | `states.spec.ts` 3 ukuran layar (1280x720, 1366x768, 1920x1080): tanpa scroll halaman, composer dan board terlihat |
| Kontrak stop | `{}` tanpa `expected_revision`: `flow.spec.ts` test 3 (backend nyata) dan `states.spec.ts` test 1. Uji mutasi: stop dengan `expected_revision` membuat kedua tes gagal |

## Cara menjalankan

```sh
npm run build
npx playwright test --config playwright.web.config.ts   # 29 tes GUI
npx playwright test                                      # smoke DEV-001
npx playwright test --config playwright.api.config.ts    # browser DEV-008
```

Pengembangan manual: API, `python -m app.worker --runtime structured`, `npm run dev:web`,
lalu buka `http://127.0.0.1:5173` dan tempel isi `data/auth/login-code`.

## Hasil verifikasi aktual (Windows, Chromium)

- `npm run build`: lulus.
- GUI: 9 lulus; diulang 3 kali (27 lulus, tanpa flaky).
- Smoke DEV-001: 5 lulus. Browser DEV-008: 3 lulus.
- Suite backend (pytest) tidak dijalankan ulang: tidak ada kode backend yang berubah.
- Satu bug ditemukan oleh tes dan diperbaiki: `Run.result` null membuat halaman putih.

Verifikasi reviewer setelah perbaikan: build lulus; GUI 29 lulus (20 regresi baru), smoke
5 lulus, browser DEV-008 3 lulus. Dua regresi tambahan memakai API/DB/file nyata: UAT
mem-pin QA + smoke receipt, dan draf tidak menimpa revisi scope dari tab lain setelah SSE.
Receipt UAT adalah contract fixture sintetis, bukan hasil QA nyata. 18 regresi baru lainnya
memakai respons tiruan. Temuan dan reproduksi kode sebelum fix ada di laporan review.

## Known issues

- Tanpa UI release (DEV-014 belum ada; endpoint release hanya kontrak). Release approval
  dan deployment tidak diklaim.
- Waiting quota/input, evidence hilang, dan waiver diuji dengan respons API tiruan sesuai
  kontrak. UAT juga diuji lewat API/DB/file nyata dengan receipt sintetis dari fixture domain;
  belum membuktikan QA harness nyata. Percakapan PO nyata dan model nyata belum diverifikasi (DEV-015).
- Waiver baseline meminta ID artefak fingerprint manual; belum ada daftar fingerprint di API.
- Tidak ada token streaming; hanya pesan final (kontrak API DEV-008).
- Chat tanpa paging mundur; prioritas belum dipakai scheduler (konvensi di `docs/decisions/gui.md`).
- Gaya/aksesibilitas belum diaudit alat (kontras, screen reader); hanya peran/label dasar.
