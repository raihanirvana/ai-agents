# DEV-011 implementation handoff

Status implementasi: DONE. Review awal Codex: NEEDS_FIX, diperbaiki; fix menunggu re-review independen.
Laporan dan verifikasi terbaru: [DEV-011-review](DEV-011-review.md). R6 belum ditutup.
Pelaksana: Claude (Sonnet 5.5), 2026-10-05. Baseline: `6cd5f8e` (DEV-010).
Tidak ada commit/push DEV-011. Ini handoff implementer, bukan independent review.

## Hasil dan file

Tiket UAT kini punya preview on-demand: panel di detail kandidat meminta supervisor menjalankan bundle build yang
sudah diuji dalam container tanpa jaringan, diakses lewat proxy loopback di `http://localhost:5180/`. Keputusan dan
batas: `docs/decisions/preview.md`.

| File | Tanggung jawab |
| --- | --- |
| `app/preview/requests.py` | Aturan permintaan/stop/switch/eligibilitas (DB saja, dipakai API) |
| `app/preview/service.py`, `proxy.py` | Lifecycle container, health smoke, proxy loopback→unix socket, recovery (POSIX + Docker) |
| `contracts/verification/preview-server.cjs` | Server statis di container, listen unix socket |
| `persistence/models.py`, `migrations/versions/0005_previews.py`, `pins.py` | Tabel `previews`, pin artefak preview aktif |
| `http/{application,queries,security}.py`, `config.py`, `api.py`, `contracts/api/*` | Endpoint start/stop/get, `live_preview`/`preview` pada DTO, `PREVIEW_PORT` |
| `pipeline/wiring.py`, `worker.py` | `build_preview`; hook maintenance dan shutdown di `--runtime pipeline` |
| `apps/web/src/components/Ticket.tsx`, `style.css` | Panel preview (status, identitas target/build, link localhost, buka ulang/hentikan) |
| `tests/preview/**`, `tests/http/test_previews.py` | 15 + 5 tes backend (Docker nyata) |
| `playwright.preview.config.ts`, `tests/web-browser/{preview.spec.ts,preview_server.py}` | 4 tes browser dengan API + PreviewService + Docker nyata |
| `tests/web-browser/states.spec.ts`, `review-fixtures.ts`, `playwright.web.config.ts` | 1 tes GUI tiruan, fixture memuat field baru |
| `.env.example`, `README.md`, `docs/decisions/{preview,api}.md` | Konfigurasi, cara menjalankan, keputusan |

Berkas baru perlu dibaca langsung (`git status --short`); `git diff` tidak mencakup untracked files.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| Target memenuhi gates/UAC dan smoke tersedia; checklist/waiver terlihat; reopen artefak sama | `test_only_the_current_verified_candidate...` (eligibilitas via `evidence.verification`), `test_switching_...reopen_serves_the_same_artifact` (`details` identik, bytes sama); checklist manual dan waiver dari DEV-009 tetap di UatSection/Approvals |
| Rebuild = target baru walau SHA sama; artefak hilang unavailable; reset fixture bukan target baru | `test_feedback_that_supersedes...rebuild_of_the_same_sha_is_a_new_target`; `test_a_missing_or_corrupt_build_bundle...`; `test_an_artifact_that_disappears_after_the_request...`; browser test 4 (409 `artifact_unavailable`, tanpa baris/container); fixture stateless dicatat di `details.fixture` |
| Switch membersihkan resource; approval tidak bergantung proses preview; ownership terpisah dari job | `test_switching_stops_the_previous...`, `test_cleanup_refuses_a_container_that_is_not_this_previews...`, `test_uat_acceptance_does_not_depend_on_the_preview_process`, `test_preview_serves...` (jumlah job tidak berubah), `test_a_restarted_worker...` |
| Origin berbeda; kredensial/DB terisolasi | Browser test 1: link `localhost`, cookie sesi 127.0.0.1 ada tetapi tidak ada `Cookie/Authorization/X-CSRF-Token` pada request ke preview; test backend: tanpa env secret/DB |
| Host berbeda §10, header capture, mutation ditolak, jaringan target terisolasi | Browser test 1: halaman target mencoba GET+POST ke API dengan credential: `blocked`; server mencatat semua request ber-Origin preview 403 tanpa credential. `test_preview_serves...`: `NetworkMode none`, tanpa port binding, hanya `lo`, `wget` ke gateway/host/loopback gagal |
| Migrasi dari kosong dan upgrade | Tidak berlaku untuk stack minimum (migrasi `none`); target dengan migrasi lain ditolak (`test_stacks_that_need_migrations...`, HTTP 4xx). Migrasi skema 0005 sendiri: `tests/persistence` |
| Feedback memicu repair dan kandidat baru; accept mengacu kandidat/scope/target/evidence | Browser test 3 (request-changes → Development, preview ditutup `superseded`); accept memakai ID yang sama (`test_uat_acceptance_...`) |
| Commit/build/evidence dipin; cleanup tidak menghapus artefak yang dirujuk | `test_an_active_preview_pins...`; `test_preview_serves...` (artefak tetap `available` setelah stop) |
| Tiket independen tetap berjalan saat UAT | `test_an_independent_ticket_keeps_being_worked...`: scheduler mendispatch tiket lain dan slot execution tetap bisa di-claim saat preview `ready` |

## Cara menjalankan

```sh
npm run build
npx playwright test --config playwright.web.config.ts       # 30 tes GUI (tanpa Docker)
npx playwright test --config playwright.preview.config.ts   # 4 tes preview (Docker; Windows: backend di WSL)
npx playwright test; npx playwright test --config playwright.api.config.ts
# Docker/WSL (venv Linux, image node:22.20.0-alpine lokal):
cd apps/backend && python -m pytest -q
```

Pengembangan manual: API, `python -m app.worker --runtime pipeline` (Docker, `HERMES_PYTHON`), `npm run dev:web`,
buka tiket di UAT lalu **Buka preview**.

## Hasil verifikasi aktual

- WSL + Docker, suite backend lengkap: **742 passed** (359 detik), dijalankan sebelum satu tes tambahan terakhir;
  sesudahnya `tests/preview` **15 passed**, serta `tests/http/test_previews.py` dan `tests/persistence` lulus.
- Windows (`--ignore=tests/workspace --ignore=tests/runtime_spike`): **555 passed, 14 skipped** (tes preview butuh
  POSIX/Docker).
- GUI (`playwright.web.config.ts`): 30 passed. Preview browser: 4 passed. Smoke DEV-001: 5 passed. Browser DEV-008: 3 passed.
- `npm run build` dan `tsc --noEmit` lulus.
- Uji mutasi: `--network bridge` pada container preview menggagalkan tes isolasi. Eksperimen kontrol sementara (tidak
  di-commit): halaman yang sama dibuka pada `127.0.0.1:19863` memang menerima `Cookie`, sehingga tes header pada
  `localhost` bermakna.
- Masalah yang ditemukan sendiri saat uji dan diperbaiki: path unix socket melebihi batas 108 byte pada root workspace
  panjang (kini socket di direktori temp pendek); `live_preview` tak terdefinisi pada fixture lama membuat panel crash
  (kini ditoleransi).

## Known issues

- Hanya static React/Vite stateless dengan migrasi `none`; target lain ditolak, bukan dipratinjau tanpa migrasi.
- `preview-server.cjs` adalah salinan aturan `static-server.cjs` (menjaga identitas runner DEV-010); harus dijaga sepadan.
- Satu worker per host. Recovery menghentikan semua preview aktif di host itu.
- HTTPS/host terpisah untuk VPS, serta iframe, tetap DEV-017.
- Di Windows, tes browser bergantung pada forwarding localhost WSL2 (tes menunggunya); bukan perilaku produk.
- Tidak ada tombol "rebuild" tersendiri: rebuild adalah feedback → kandidat baru → QA/UAT baru.
- Tidak ada model/provider nyata; QA pada fixture adalah contract fixture bertanda, bukan hasil QA nyata.
