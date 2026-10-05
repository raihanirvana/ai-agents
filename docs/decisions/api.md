# API lokal, autentikasi, dan replay event — DEV-008

Implementasi: `apps/backend/app/http/`, factory `create_app`, entrypoint
`app.api:app`. DEV-008 menyediakan command/query serta kontrak untuk GUI DEV-009.
Tidak menjalankan model di HTTP handler. Chat disimpan dan job PO dijadwalkan
bersama receipt command dalam satu transaksi. Runtime normal `structured`;
fixture memakai `structured:fake` dan label fake tetap ada di run/result.

## Bootstrap dan batas host

`python -m app` bind ke `127.0.0.1:API_PORT`. Startup memigrasikan SQLite ke
0004 dan membuat `DATA_DIR/auth/login-code` secara eksklusif, mode POSIX 0600.
File yang sudah ada harus berisi code ASCII minimal 32 karakter, atau startup
gagal. Di Windows, direktori data memakai akses akun OS pengguna; code tidak
ditampilkan dalam log. Database menyimpan hash SHA-256 session/runtime token,
bukan token yang bisa digunakan. Data ini gitignored dan tidak di-mount ke target.

`POST /auth/login` membutuhkan code lokal dan exact Origin kontrol. Login
menghasilkan cookie `ai_team_session`, host-only tanpa Domain, Path `/`, HttpOnly,
SameSite Strict, masa hidup 8 jam. Secure false khusus HTTP loopback lokal;
routing HTTPS/secure cookie untuk VPS adalah DEV-017. CSRF adalah HMAC per session,
dikembalikan ke client yang login; `GET /auth/session` memulihkannya setelah reload.
`POST /auth/logout` merevoke session persisten dan menghapus cookie. Session
tetap valid saat proses API restart, sampai expiry/logout. Mengganti file bootstrap
bukan mekanisme revoke seluruh session; tidak ada klaim demikian.

Guard ASGI memeriksa Host persis `127.0.0.1:API_PORT`, termasuk health, HEAD,
OPTIONS, docs, dan error. Origin harus sama persis dengan allowlist
`CORS_ORIGINS` (default origin WEB_PORT/WEB_PREVIEW_PORT, keduanya 127.0.0.1).
Mutation browser membutuhkan Origin dan `X-CSRF-Token`, kecuali login yang memakai
code. `null`, alias host, origin localhost preview, cross-site fetch dan header
keamanan berulang ditolak. Body mutation dibatasi 1 MiB. CORS memakai exact origin
dan credentials; GET tanpa Origin diperbolehkan untuk client lokal yang mempunyai
credential, bukan bypass login. OpenAPI HTTP hanya dapat dibaca setelah login.

Cookie tidak dipisahkan oleh port. Semua halaman di host 127.0.0.1 adalah kontrol
tepercaya; generated preview harus di `localhost`. Browser fixture benar-benar
membuka localhost dan menangkap incoming headers: Cookie, Authorization dan CSRF
absen. Request browser dari preview ditolak pada OPTIONS 403. Sandbox DEV-005
tetap `--network none`, tanpa provider secret, DB, Git metadata, atau Docker socket.
Suite WSL dengan Docker menguji batas ini kembali. Tidak memperkenalkan preview
server produk baru; start/reopen preview adalah DEV-011.

## Transaksi command

`Idempotency-Key` wajib untuk command bisnis: ASCII printable 1–160 karakter,
unik per principal dan tindakan logis. Request hash mengikat method, path dan
body tervalidasi. Actor browser berasal dari session (`user:local`), bukan argumen
model/request. Actor runtime berasal dari lease yang diverifikasi database.

`ApiCommand` menyimpan response setelah efek domain/event/job, pada transaksi
`BEGIN IMMEDIATE` yang sama. Layanan Workflow/Queue/Threads memakai session yang
sama melalui transaction adapter. Retry identik, termasuk sesudah restart,
mengembalikan receipt asli sebelum memeriksa revision yang sudah maju. Key sama
dengan work berbeda menghasilkan 409. Mutation gagal rollback dan tidak membuat
success receipt. Revision stale menghasilkan 409 `revision_conflict` dengan
expected/actual. Batch approval memeriksa semua tiket dalam satu transaksi.

Creation tidak punya revision entity sebelumnya. Scope approval membawa revision
per tiket. Scope/candidate/release/run mutation menggunakan expected revision;
keputusan teknis mengacu pesan proposal append-only dan keputusan tunggal.
Pertanyaan nonblocking mengacu identitas pesan immutable, scope/generation dan
status open. Tidak ada endpoint arbitrary status setter. Payload schema strict,
extra fields ditolak. Approval kandidat tetap mengikat build target dan evidence,
bukan hanya SHA; provider/runtime tidak memperoleh command approval pengguna.

Runtime hanya pada `/runtime/*`, menggunakan Bearer token terpisah yang diterbitkan
oleh method Python tepercaya `Auth.issue_runtime(lease, ttl_s=300)`. Tidak ada
endpoint browser untuk menerbitkannya. Binding mencakup job/owner/generation;
expiry dan lease aktif diperiksa saat auth dan dalam transaksi efek. Credential
lama gagal setelah cancel/expiry/generation berubah. Identity di argumen tools
ditolak oleh ToolFacade. Runtime credential tidak bekerja pada endpoint user,
dan cookie browser tidak bekerja pada endpoint runtime.

Admission tool direservasi sebelum savepoint efek. Error/denial rollback efek
tool tetapi tetap mencatat usage dan receipt error; retry key yang sama tidak
menghabiskan budget dua kali. Error tool biasa tidak menghentikan attempt. Budget
exhaustion melakukan stop/fencing secara persisten. Penambahan budget hanya oleh
pengguna dan memakai counter shared lintas retry; API menampilkan `usage` shared
serta `attempt_usage`. Unknown usage tetap ditampilkan sebagai `_unknown`.
Secrets dari environment/shape provider dan bootstrap code diredaksi sebelum
penyimpanan/public response; validation error tidak menyertakan input mentah.

## Endpoint

| Kelompok | Command/query |
| --- | --- |
| Auth | POST login/logout, GET session pada `/auth/*`; GET `/health` public |
| Project | GET/POST `/projects`, GET `/projects/{id}`, POST `/projects/{id}/brief` |
| Scope | GET/POST `/projects/{id}/tickets`, GET `/tickets/{id}`, POST `/tickets/{id}/scope-versions`, POST `/projects/{id}/scope-approvals` |
| Proposal | POST `/tickets/{id}/proposals/{proposal}/decisions`; proposal/document tampil dalam messages detail |
| Decision | POST `/projects/{id}/decisions/{proposal}`; hanya pesan accepted masuk knowledge |
| Ticket | POST `/tickets/{id}/priority`, `/cancel`, `/request-changes`, `/repair-authorizations` |
| Chat | GET/POST `/projects/{id}/messages` (percakapan saja: baris log runtime tidak ikut, lihat `/runs/{id}/logs`; sama untuk `messages` pada detail tiket); `breakdown`, `revise` (ticket wajib), `note` (tanpa job) |
| Input | POST `/runs/{id}/input`; POST `/projects/{id}/inputs/{request}` untuk user escalation nonblocking |
| Run | GET `/runs/{id}`, GET `/runs/{id}/logs?generation=N&after_seq=N`; POST `/stop`, `/budget-authorizations` |
| Candidate/evidence | GET `/tickets/{id}/candidates/{candidate}`, GET `/artifacts/{id}` dan `/content` |
| Preview | POST `/tickets/{id}/candidates/{candidate}/previews`, POST `/previews/{id}/stop`, GET `/previews/{id}` (kandidat memuat `live_preview`, board memuat `preview` aktif; lihat [preview](preview.md)) |
| Release | POST `/projects/{id}/releases` (freeze + verifikasi sebagai job), GET `/projects/{id}/releases`, GET `/releases/{id}`, POST `/releases/{id}/export`, `/sync`, `/discard`; board memuat `releases` (lihat [release](release.md)). `deployed` tidak pernah diturunkan |
| Approval | POST `/tickets/{id}/uat-decisions`, `/projects/{id}/baseline-waivers`, `/releases/{id}/decisions` (mem-pin target/evidence dan `manual_uac_ids` checklist release; release sinkronisasi juga memerlukan `reviewed_diff_ids` untuk kedua diff target) |
| Runtime | POST `/runtime/tools`, `/runtime/tickets/{id}/candidates`, `/runtime/candidates/{id}/reviews` |
| Events | GET `/projects/{id}/events?cursor=N`, optional `follow=false` |

Runtime prefix memperjelas pemisahan credential dari command browser. UAT repair
memakai request-changes; accept memakai uat-decisions. Repo existing
didaftarkan dengan onboarding pending. `POST /projects/{id}/onboarding` (DEV-013)
mempersist job dengan manifest dan patch/SHA opsional; worker melakukan import/baseline
tanpa target execution pada HTTP atau mutation repo sumber. Kandidat memerlukan
commit receipt broker yang sudah dipercaya; API tidak menyediakan minting target,
evidence atau receipt dari model. Build/QA execution DEV-010, integrasi DEV-012,
release creation/export DEV-014. Endpoint future yang belum ada menghasilkan
structured 404; tool policy future menghasilkan `not_wired` 501.

Run stop (`POST /runs/{id}/stop`, body kosong `{}`) merevoke generation sebelum merespons
`cleanup: supervisor_pending`. Stop **tidak** memakai `expected_revision`: revision job dinaikkan heartbeat
supervisor setiap beberapa detik, sehingga syarat itu akan membuat tombol stop sering gagal 409. Stop adalah
revoke yang idempotent (retry dengan key sama mengembalikan receipt asli). Run yang sudah tidak aktif
(succeeded/failed/cancelled/stopped) dijawab 409 `conflict`, bukan sukses palsu.
Supervisor aktif melakukan cleanup process/container melalui siklus DEV-004;
owner mati direkonsiliasi dari ledger recovery. Respons tidak mengklaim container
sudah mati saat command selesai. HTTP input memeriksa revision job, request aktif,
scope/generation request dan job, serta scope ticket dari domain. Jawaban lama,
request cancelled dan scope berubah ditolak; retry receipt hanya mengulang hasil
original dan tidak menjadwalkan ulang job. Request yang dieskalasi menampilkan
`input.status: escalated` dan `escalated_request_id` sehingga UI tidak mengartikannya
sebagai pertanyaan yatim yang masih perlu dijawab.

Artifacts diverifikasi sebelum download; metadata tidak menampilkan storage path.
File hilang/corrupt ditandai unavailable dalam DB/event dan tidak dikirim.
Content dikirim sebagai attachment/octet-stream dengan CSP sandbox, bukan HTML
inline di host kontrol. Export Git belum tersedia pada endpoint content.

## SSE dan kontrak frontend

Event ID adalah `events.cursor` persisten, dengan urutan commit dari SQLite.
`state` berisi cursor, project/type/entity/run, payload dan created_at. Replay
di-filter per project, dibaca per 100 event tanpa transaksi melintasi yield.
`Last-Event-ID` mendapat prioritas atas query cursor: native EventSource memakai
URL awal lagi setelah disconnect tetapi mengirim header cursor yang lebih baru.
Header malformed ditolak 422. Replay membaca event setelah cursor, sehingga
cursor terakhir tidak dikirim ulang; UI tetap menggabungkan state/message menurut
identity ketika melakukan refresh/replay, bukan membuat job dari event.

Replay window default 10.000 event per proyek; histori DB tidak dihapus. Cursor
terlalu lama atau ahead memicu `snapshot_required` (reason, snapshot_url, cursor)
dan stream ditutup. Client mengambil board snapshot konsisten beserta cursor,
memulihkan query terkait termasuk chat/detail, lalu subscribe dari cursor snapshot.
`follow=false` memberi replay finite untuk client/test. Stream live poll 250 ms,
heartbeat sekitar 15 detik, dan memeriksa session tiap poll; expiry/revoke mengirim
`session_expired` dan menutup stream. Final pesan agent disimpan oleh DEV-007
sebelum event terminal job; token delta tidak disediakan sebagai hasil persisten
di tiket ini. Reload mendapatkan pesan final dari query messages.

`python -m app.http.contract` mengekspor OpenAPI dan `contracts/api/requests.ts`
langsung dari schema route/Pydantic. Test drift membandingkan keduanya dengan
server. `contracts/api/types.ts` memakai tipe body yang generated dan mendefinisikan
DTO response query. DTO query masih eksplisit/manual; bukan klaim semua respons
OpenAPI sudah memiliki response_model. Integrasi fixture memeriksa proyeksi
board/detail/evidence/message/run. Client TS menyediakan credential cookie, CSRF,
command key, typed errors, SSE dedup cursor, snapshot refresh async dan teardown.
UI login/board/decision tetap DEV-009. Client tidak retry mutation otomatis.

Error envelope: `{ "error": { "code": "revision_conflict", "message": "...",
"details": { "expected": 3, "actual": 4 } } }`. Status umum: 401 credential,
403 policy, 404 identity/route, 409 conflict/stale/budget/unavailable, 422 invalid
request/intent, 501 not-wired, 500 internal error tersanitasi.

## Verifikasi dan handoff

Lihat [DEV-008-handoff](../reviews/DEV-008-handoff.md) untuk perintah, hasil aktual,
pemetaan AC dan known issues. Checkpoint R5 belum ditutup; implementer checks
dan fixture fake tidak membuktikan percakapan PO/provider nyata (DEV-015).
