# Review DEV-008 — API lokal, autentikasi, SSE

Tanggal: 2026-10-05. Reviewer: Claude (Opus 5.5), independen dari implementer Codex.
Baseline HEAD: `a9918f3` (DEV-007). Snapshot yang direview: index staged (37 file, 12.919 baris termasuk
`contracts/api/openapi.json` yang digenerate).
Verdict review awal: **NEEDS_FIX**. Tidak ada celah pada kontrol akses, tetapi ada satu bug P1 (penghenti darurat
bisa gagal oleh heartbeat) dan beberapa bug P2/P3 pada query dan kontrak. Semua temuan diperbaiki oleh reviewer
atas instruksi pengguna; perbaikan adalah self-check reviewer dan **menunggu re-review independen**, bukan verdict
REVIEWED. Ini review DEV-008, bukan penutupan R5 untuk DEV-007/008/009.

Scope yang dibaca penuh: `app/http/*` (application, security, service, events, queries, schemas, contract), `api.py`,
migrasi `0004`, tambahan model/domain, 9 file `tests/http/`, `apps/web/src/api/client.ts`, `contracts/api/*`,
`tests/api-browser/*`, `docs/decisions/api.md`, handoff implementer. Probe reproduksi dan perbaikan memakai
SQLite/artifact asli, fake provider berlabel, dan Chromium; tidak memanggil provider nyata. Probe yang mencetak
perilaku aktual ada di `data/dev008/review_probes_http.py` dan `review_probes_context.py` (gitignored).

## Yang sudah baik

- **Host/Origin/CSRF.** Guard ASGI terluar memeriksa Host persis `127.0.0.1:<port>` untuk semua method (termasuk HEAD,
  OPTIONS, error), Origin exact-allowlist, header keamanan berulang, `Sec-Fetch-Site: cross-site`, dan body maksimum
  1 MiB. Probe tambahan: token CSRF dari sesi lain 403, CSRF kosong 403, cross-site GET 403, preflight origin asing 403,
  cookie pada `/runtime/*` 401, key idempotency yang sama pada path lain 409.
- **Pemisahan credential.** Cookie user hanya berlaku pada rute user, bearer runtime hanya pada `/runtime/*`; keduanya
  saling menolak. Token disimpan sebagai hash; runtime credential terikat job/owner/generation dan diperiksa ulang
  terhadap lease di dalam transaksi efek. Cookie host-only, HttpOnly, SameSite=Strict, Path=/, tanpa Domain.
- **Receipt atomik.** `TransactionDatabase` membuat command, efek domain/queue/threads, event, dan receipt berada dalam
  satu transaksi `BEGIN IMMEDIATE`; kegagalan tidak meninggalkan receipt sukses, retry identik mengembalikan receipt asli,
  key yang sama dengan work berbeda 409.
- **SSE.** Cursor persisten, replay per proyek per 100 event, `Last-Event-ID` mengalahkan query cursor, cursor
  kedaluwarsa/ahead memicu `snapshot_required`, sesi dicek tiap poll. Tidak ada token model yang dipersist per token.
- **Proyeksi publik eksplisit.** Hash credential, `runtime_ref` (termasuk `structured_outputs`), dan path penyimpanan
  tidak pernah keluar; artifact diverifikasi sebelum diunduh dan dikirim sebagai attachment + CSP sandbox.

## Temuan dan perbaikan

### R008-01 — P1: penghenti darurat gagal karena revision dinaikkan heartbeat

Lokasi: `application.py` `stop`, `schemas.py` (`Revision`).
`POST /runs/{id}/stop` mewajibkan `expected_revision`, padahal `queue.heartbeat` menaikkan `jobs.revision` pada setiap
heartbeat (default tiap ~10 detik). UI yang menampilkan run lalu menekan stop kerap membawa revision yang sudah basi.

Reproduksi: run `running`, revision terlihat UI = 2, satu heartbeat -> 3, `POST /stop {expected_revision: 2}` ->
**409 revision_conflict**, status job tetap `running`. Untuk fitur yang gunanya menghentikan pemakaian budget, ini
bukan sekadar UX: pengguna melihat error dan run terus berjalan.

Perbaikan: stop tidak lagi membawa revision (`schemas.Empty`, body `{}`); revoke bersifat idempotent dan dilindungi
Idempotency-Key. Kontrak OpenAPI/`requests.ts`/`types.ts` digenerate ulang. Dicatat di `docs/decisions/api.md`.

### R008-02 — P2: stop pada run yang sudah tidak aktif melapor sukses

Lokasi: `application.py` `stop`.
`queue.cancel` mengembalikan `False` untuk job yang bukan aktif, tetapi hasilnya diabaikan.

Reproduksi: run `succeeded`, `POST /stop` -> **200** `cleanup: "supervisor_pending"`, status tetap `succeeded`.
Respons menyatakan ada cleanup tertunda yang tidak pernah terjadi.

Perbaikan: `False` -> `409 conflict` ("run is succeeded; only an active run can be stopped"). Retry dengan key yang
sama tetap mengembalikan receipt asli.

### R008-03 — P2: baris log runtime tercampur dengan percakapan

Lokasi: `application.py` `messages`, `queries.py` `detail`.
`queue.log_line` menyimpan log supervisor sebagai `Message` ber-`ticket_id`. `GET /projects/{id}/messages` (tanpa filter
thread) dan `messages` pada detail tiket mengembalikan semuanya.

Reproduksi: setelah satu run PO fake, 3 dari 5 pesan proyek adalah `system:supervisor`. Dengan 150 baris log dan limit
default 100, riwayat chat sebenarnya terdorong keluar dari jendela dan GUI DEV-009 akan menampilkan log sebagai chat.

Perbaikan: filter `metadata.runtime_log` pada kedua query; log tetap tersedia lengkap lewat `/runs/{id}/logs`. Input
request pada thread `job:<id>` tetap tampil (itu percakapan).

### R008-04 — P2: baris log supervisor masuk ke prompt model (DEV-004 × DEV-007)

Lokasi: `agents/context.py` `_history`.
Akar sama dengan R008-03: konteks mengambil semua pesan bertipe `message` milik tiket, termasuk log supervisor.

Reproduksi: developer menulis dua baris log (`tool read_file`, `context ... tokens`), lalu konteks technical lead
memuat `system:supervisor->all message` pada lapisan "Recent messages". Dampak: token terbuang pada noise, dan keluaran
alat/tool yang masuk log menjadi jalur injeksi ke prompt lead.

Perbaikan: pesan `runtime_log` dikecualikan dari lapisan percakapan. Ini perbaikan pada kode DEV-007; ditemukan lewat
review DEV-008.

### R008-05 — P3: `board` memindai semua job untuk setiap job

Lokasi: `queries.py` `run`, `board`.
Tiap run memuat seluruh job proyek (untuk menghitung usage `_unknown`) dan menjalankan query budget sendiri.

Reproduksi: jumlah statement SQL `GET /projects/{id}/tickets` = 13 untuk 2 job dan 33 untuk 12 job; baris yang
dimuat tumbuh kuadratik. Retry dan reply job membuat jumlah job per proyek cepat membesar.

Perbaikan: job dimuat sekali dan usage dihitung sekali per budget key. Upaya pertama memakai
`dict.setdefault(key, query())` yang tetap menjalankan query tiap panggilan (argumen dievaluasi lebih dulu); regresi
menangkapnya dan diperbaiki dengan cek `in`. Regresi memastikan jumlah statement konstan untuk run dalam satu scope.

### R008-06 — P3: penolakan guard tidak memakai header pengerasan

Lokasi: `security.py` `LocalPolicy`.
Respons error dari guard dikirim langsung, tanpa `Cache-Control: no-store`, `X-Content-Type-Options`, dan
`Referrer-Policy` yang dipasang pada respons lain. Perbaikan: semua respons guard melewati pembungkus yang sama.

### R008-07 — P3: client TS melempar `SyntaxError` untuk error non-JSON

Lokasi: `apps/web/src/api/client.ts` `request`.
Respons error yang bukan envelope JSON (proxy 502, halaman crash, body terpotong) membuat `response.json()` melempar
`SyntaxError`, bukan `ApiError`, sehingga UI tidak bisa menampilkan status/kode. Perbaikan: `ApiError` bertipe
`http_error` dengan status asli. Diuji di Chromium; dengan client lama test yang sama gagal (`SyntaxError`).

## Observasi (bukan temuan, tidak diubah)

- Login tidak dibatasi laju. Code 256-bit acak sehingga tebakan tidak realistis; throttling baru berguna bila code
  nanti dapat diatur pengguna.
- Tidak ada pembersihan session/receipt/credential kedaluwarsa (sudah dicatat implementer); tabel tumbuh tanpa batas.
- `GET /projects/{id}/messages` hanya menyediakan N terbaru atau satu thread; belum ada paging mundur. Perlu bila chat
  panjang dibuka di DEV-009.
- `GET /artifacts/{id}` dan `/content` menghitung ulang hash dan menulis `verified_at` pada setiap GET; aman tetapi
  berat untuk artifact besar dan memakai kunci tulis SQLite.
- `Secure=false` pada cookie memang sesuai mode loopback; mode VPS (`__Host-`, Secure) adalah DEV-017.
- TTL runtime credential default 300 detik; run lebih lama harus meminta credential baru dari supervisor.
- Flaky DEV-004 yang dicatat implementer tidak muncul pada run review ini; laju tidak diukur ulang.

## Pemetaan AC

| AC | Hasil review |
| --- | --- |
| 1 Command dengan identitas tepercaya, revision/idempotency | Terpenuhi; R008-01/02 membuat stop sesuai (idempotent, tanpa revision palsu) |
| 2 Snapshot board/detail, log, evidence | Terpenuhi setelah R008-03/05 (log tidak lagi tercampur, board tidak kuadratik) |
| 3 SSE replay, cursor kedaluwarsa, message final persisten | Terpenuhi; diuji ulang, browser reconnect lulus |
| 4 Reconnect tidak menggandakan efek; error terstruktur | Terpenuhi; error non-JSON di client R008-07 |
| 5 Origin kontrol terpisah dari preview | Terpenuhi (guard, probe tambahan, header capture browser) |
| 6 Host/session policy §10 | Terpenuhi; R008-06 melengkapi header pengerasan |
| 7 Input answer fenced, waiver sebagai command pengguna | Terpenuhi; tidak menemukan celah |

## Verifikasi

Sebelum perbaikan (diulang oleh reviewer): Windows dan WSL `tests/http` **48 passed**; `npm run build` lulus;
Chromium `playwright.api.config.ts` **2 passed**.

Setelah perbaikan, dari `apps/backend` kecuali dicatat:

```sh
.venv/Scripts/python.exe -m pytest tests/http tests/agents tests/domain tests/persistence tests/workers -q   # Windows
/root/aiagent-dev002-venv/bin/python -m pytest -q                                                           # WSL, seluruh backend + Docker
npm run build && npx playwright test --config playwright.api.config.ts                                       # root
```

- Windows: **513 passed, 8 skipped** (symlink dan test process group POSIX), 58.8 detik.
- WSL backend lengkap: **664 passed**, 219.8 detik, termasuk Docker nyata.
- Build frontend lulus; Chromium **3 passed** (tambahan 1 test client).
- 9 test regresi baru (8 backend: `tests/http/test_review_regressions.py` 7 + 1 di `tests/agents/test_context.py`; 1 browser).
  Terhadap tree sebelum perbaikan: **8 backend failed**, test browser gagal dengan `SyntaxError`; semuanya lulus setelah fix.
- Kontrak digenerate ulang dengan `python -m app.http.contract`; test drift lulus.

## Daftar perubahan perbaikan

`app/http/{application,queries,schemas,security}.py`, `app/agents/context.py`, `contracts/api/{openapi.json,requests.ts,types.ts}`,
`apps/web/src/api/client.ts`, `tests/http/{test_review_regressions,test_runs_events}.py`,
`tests/agents/test_context.py`, `tests/api-browser/client.spec.ts`, `docs/decisions/api.md`.
Tidak stage, commit, push, atau memanggil provider nyata.

## Recheck perbaikan dan persiapan commit — Codex, 2026-10-05

Perbaikan R008-01 sampai R008-07 dibaca ulang terhadap diff index/working tree dan
regresi terkait. Tidak ditemukan blocker untuk commit DEV-008. Stop memakai body
`{}` dan key idempotency yang tetap saat retry; `expected_revision` pada stop
ditolak 422. Log runtime tetap tersedia lewat logs endpoint dan tidak masuk chat,
detail atau lapisan recent messages pada prompt.

Verifikasi yang dijalankan ulang oleh Codex:

- Windows: `pytest tests/http tests/agents/test_context.py -q --tb=short` — **74 passed**,
  termasuk delapan regresi backend reviewer dan drift kontrak. Satu warning deprecation
  HTTPX/Starlette seperti pada handoff awal.
- Root: `npm run build` — lulus; `npx playwright test --config playwright.api.config.ts`
  — **3 passed**, termasuk error non-JSON menjadi ApiError bertipe.
- `git diff --check` dan `git diff --cached --check` — lulus.

Suite lengkap Windows/WSL tidak dijalankan ulang pada recheck ini; hasil 513/8 dan
664 di atas adalah run reviewer Claude. Observasi nonblocker tetap berlaku.
Ini verifikasi perbaikan reviewer oleh implementer awal, bukan klaim independent
review menyeluruh atau penutupan checkpoint R5. Commit/push DEV-008 diotorisasi
pengguna setelah perbaikan dinilai layak. Tidak ada provider call nyata.
