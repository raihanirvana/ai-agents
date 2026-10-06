# Blueprint MVP: AI Software Development Team

Tanggal: 4 Oktober 2026. Revisi: 3, setelah review dalam verdict.md dan verdit2.md.
Status: spesifikasi MVP untuk implementasi; sistem belum dibangun.

Rancangan teknis: [ARCHITECTURE.md](./ARCHITECTURE.md).
Tiket pembangunan platform: [IMPLEMENTATION-BACKLOG.md](./IMPLEMENTATION-BACKLOG.md).
Panduan AI pelaksana: [AGENTS.md](./AGENTS.md).
Setup, prompt pengerjaan, dan titik code review: [DEVELOPMENT-WORKFLOW.md](./DEVELOPMENT-WORKFLOW.md).

## Tujuan

Pengguna memberi breakdown kebutuhan. PO merincinya menjadi tiket dan UAC
(acceptance criteria), lalu pengguna berdiskusi dan menyetujui scope sebelum
pengerjaan. Technical lead, developer, dan QA menjalankan pekerjaan. Setiap
tiket yang lulus QA tersedia untuk UAT pengguna. Full release dilakukan setelah
penerimaan fitur dan verifikasi build gabungan.

Mendukung proyek dari nol dan repository existing. Versi pertama berjalan lokal,
dengan penyimpanan persisten dan model API gratis/murah. Deployment VPS menjadi
tahap berikutnya jika pekerjaan harus tetap berjalan saat laptop mati.

## Keputusan awal

- Frontend: React, Vite, TypeScript. Board, chat PO, preview, dan aktivitas agent.
- Kantor virtual: Three.js melalui React Three Fiber, membaca event backend;
  dibuat setelah workflow dan event pekerjaan nyata berjalan.
- Backend: Python/FastAPI, database SQLite, dan worker background terpisah dari
  request HTTP. Worker memiliki lane interactive dan execution agar diskusi PO
  tetap responsif saat developer bekerja. Pembaruan aktivitas melalui SSE.
- PO dan lead mulai dengan panggilan model terstruktur dan tools terbatas.
  Developer memakai runtime tool loop; QA memakai model untuk menyusun/menganalisis
  test dan harness deterministik untuk eksekusi. Empat soul dan pesan tetap ada.
- Hermes adalah kandidat executor, belum keputusan final. Uji satu tiket nyata
  dengan model murah sebelum membangun seluruh UI. Runtime lain menjadi fallback
  jika Hermes tidak memenuhi kontrak, tanpa membangun dua runtime penuh sejak awal.
- Scheduler aplikasi menjadi satu-satunya pengendali job. Hermes menjalankan
  sesi agent; dispatcher Kanban Hermes tidak dijalankan untuk job yang sama.
- Model: konfigurasi provider dan model per peran; DeepSeek Flash/OpenRouter
  menjadi kandidat. Verifikasi ID, dukungan tools, quota, dan harga saat setup.
- Satu developer aktif pada awalnya. Empat peran tidak harus memakai empat model.
- Repo existing diklon ke workspace milik aplikasi. Worktree dibuat di clone
  tersebut oleh supervisor; sandbox mendapat source snapshot tanpa metadata Git.
  Diff/commit/checkpoint melalui broker, terpisah dari repo/perubahan asli pengguna.
- Kode diterima masuk branch internal accepted secara berurutan. Perubahan
  kode atau build/config setelah QA/UAT memerlukan pengujian dan persetujuan baru.
  Proyek baru mulai dari initial empty commit teknis; foundation code tetap
  mengikuti scope approval sampai UAT dan integrasi.
- Board internal menjadi acuan status produk. Runtime menyimpan sesi dan status
  eksekusinya; status eksekusi tidak otomatis berarti tiket diterima pengguna.
- Jira/Trello ditambahkan melalui connector setelah workflow internal terbukti.

Tidak membutuhkan akun Jira/Trello, VPS, atau pembayaran model untuk menyusun
fondasi. Eksekusi agent sungguhan memerlukan provider yang sudah dikonfigurasi.
Fake provider untuk pengujian diberi label dan tidak boleh menghasilkan klaim
QA nyata. Kode target dijalankan di container terpisah dengan data testing.

Target pilot web: stack referensi React/Vite. Repo existing menggunakan runner
manifest yang menyatakan install/build/test/start, toolchain, port, dan migration
bila ada. Stack lain didukung setelah manifest/runner-nya diverifikasi; MVP tidak
mengklaim bisa menjalankan semua repo otomatis. Mobile runner adalah tahap lanjut.

## Peran

| Peran | Tanggung jawab dan keluaran |
| --- | --- |
| PO | Klarifikasi brief, tiket, UAC, asumsi, dependency, revisi scope |
| Technical lead | Onboarding repo, arsitektur, rencana teknis, code review |
| Developer | Implementasi, commit, cara menjalankan, handoff ke review |
| QA | Test cases dari UAC, analisis hasil harness, bug report, pemetaan bukti |
| Pengguna | Menyetujui scope, UAT per tiket, menerima fitur, keputusan release |

Orchestrator menjalankan aturan antrean, dependency, retry, dan handoff.
Keputusan status dan hak persetujuan ditegakkan backend, bukan hanya prompt.

Setiap peran memiliki SOUL.md, instruksi tugas, daftar tools, dan kontrak output.
SOUL.md mengatur identitas/perilaku; ia tidak menggantikan workflow atau izin.

Pesan antar-agent merupakan data nyata di backend, bukan dialog dekoratif.
Developer bisa meminta keputusan lead; backend menyimpan thread dan menjadwalkan
jawaban. Hanya pesan terarah yang perlu jawaban memicu pekerjaan model.

## Workflow tiket

Draft → Scope Review → Ready → Development → Technical Review → QA → UAT →
Integrating → Accepted.

- PO membuat usulan revisi yang bisa diterima/ditolak pengguna; edit langsung
  oleh pengguna tetap tersedia. Usulan tidak menimpa scope disetujui.
- Pengguna menyelesaikan review dengan menyetujui kumpulan tiket tertentu.
- Approval mencatat versi scope/UAC yang disepakati. Worker hanya mengambil
  tiket disetujui yang dependency-nya terpenuhi.
- Approval memeriksa referensi dependency valid dan tidak siklik. Dependency
  yang belum disetujui ditampilkan; tiket boleh disetujui tetapi belum eligible.
- Untuk MVP, dependency kode terpenuhi oleh kandidat accepted yang sudah masuk
  branch internal accepted. Dependency mem-pin versi/candidate/integration SHA;
  perubahan kontrak pada base terbaru memerlukan revalidasi downstream.
  Tiket independen tetap boleh berjalan selama UAT.
- Technical review yang meminta perubahan kembali ke Development.
- QA menemukan kegagalan aplikasi kembali ke Development dengan langkah
  reproduksi dan bukti. Kesalahan kontrak selector atau runner tetap di QA;
  koreksi suite menghasilkan suite/target baru dan eksekusi baseline/kandidat
  baru, tanpa menganggap hasil lama lulus atau memakai siklus perbaikan kode.
- QA lulus dan kandidat lolos preview smoke test membuka UAT. Preview boleh
  stopped dan dinyalakan ketika pengguna mencoba; tidak harus selalu aktif.
- UAT meminta perbaikan kembali ke Development, kemudian review dan QA ulang.
- Kebutuhan tambahan masuk usulan revisi scope/tiket baru, bukan perubahan diam-diam.
- Perubahan scope yang sudah disetujui memerlukan approval versi baru.
- Revisi scope mengembalikan tiket ke Scope Review dan mencabut run versi lama.
- Perubahan commit membuat kandidat baru; bukti/approval lama tetap tersimpan
  sebagai histori dan tidak otomatis berlaku untuk kandidat baru.
- Build/config/fixture definition berubah menghasilkan verification target baru;
  approval tidak berpindah otomatis walaupun source SHA tetap sama.
- Loop perbaikan otomatis dibatasi: default tiga siklus per versi scope, lalu
  Blocked: needs_human. Retry transient default sekali dan dicatat terpisah.
- Pengguna dapat membatalkan tiket. Pembatalan tiket accepted menjadi pekerjaan
  revert baru jika kodenya sudah terintegrasi, bukan menghapus histori approval.
- Blocked adalah kondisi tambahan dengan alasan dan langkah penyelesaian,
  bukan penanda bahwa pekerjaan telah selesai.
- Accepted tidak sama dengan Released.

Drag-and-drop mengubah prioritas atau menjalankan intent yang memang diizinkan
untuk pengguna. Approval/UAT/release membutuhkan konfirmasi kandidat dan tombol
eksplisit. UI dan agent memakai domain commands yang sama, bukan status setter.
PO mengusulkan apakah feedback adalah bug atau scope baru; perubahan UAC selalu
memerlukan konfirmasi pengguna. Perbaikan dalam UAC tidak perlu approval scope baru.

## Preview dan release

Setiap kandidat UAT menunjuk immutable verification target: candidate ID,
source/base SHA, versi UAC, build artifact digest, runner manifest revision,
toolchain/image identity, konfigurasi, dan fixture/migration identity. Target
menyertakan cara mencoba dan lokasi build/preview; approval mem-pin target serta
verification evidence yang ditampilkan pengguna.
Harness mencatat commands, exit code, log, environment, dan artifact references.
Acceptance tests dikelola terpisah dari workspace yang bisa ditulis developer.
Acceptance E2E web berjalan di runner terpisah dari aplikasi target dengan
invocation, suite digest, mandatory test IDs, counts, dan UAC coverage yang
dikendalikan verification service. Test kosong, mandatory skipped/missing, report
palsu/invalid, atau coverage otomatis tidak lengkap tidak menghasilkan pass.
Test fitur baru/reproduksi bug dibandingkan base vs kandidat bila relevan;
regression tests diharapkan tetap lulus. UAC manual menjadi checklist pengguna.
Default required checks harus lulus. Failure baseline existing hanya dapat
dikecualikan lewat waiver pengguna untuk test ID/signature/environment/base SHA
dan scope tertentu; UAC tiket serta infrastructure failure tidak dapat di-waive.
UI menunjukkan waiver dan failure tersebut tanpa menyebut semua checks hijau.

Preview yang sedang diuji menunjuk ke build tertentu. Implementasi awal boleh
membatasi satu preview lokal aktif dengan kemampuan membuka ulang artefak teruji
dan konfigurasi yang sama. Rebuild mencatat build/target baru dengan QA/UAT ulang;
reset ke fixture yang sudah ditentukan target tidak memerlukan build baru.
UI menunjukkan target/build digest serta preview yang belum dijalankan, berhenti,
atau unavailable. Data transaksi percobaan menggunakan data testing.
Preview memakai database terpisah dan seed. Migration diuji dari database kosong
serta upgrade dari schema/base existing bila relevan; validasi head mengikuti
toolchain proyek, bukan mengasumsikan semua repo memakai Alembic.

Tiket yang membutuhkan fitur lain diuji bersama dependency yang diperlukan.
Worktree memisahkan kode; container mengisolasi eksekusi. Container target tidak
mendapat control database, provider keys, repo asli pengguna, atau Docker socket.
Metadata Git/common refs tidak di-mount ke sandbox; accepted ref milik integrator.
Control dan preview memakai host berbeda, bukan sekadar port berbeda. Lokal:
control UI/API pada 127.0.0.1, preview pada localhost. Cookie host-only, Origin/CSRF,
dan network policy mengikuti kontrak konkret di arsitektur §10; preview tidak
menerima credential kontrol. Akses awal melalui tab terpisah.

Saat UAT diterima, integrator memeriksa base kandidat masih sama dengan tip accepted.
Jika sama, lakukan fast-forward dengan pencatatan operasi persisten. Jika berubah,
rebase/perbaiki, jalankan QA, dan minta UAT kandidat baru. Rebase bersih dan test
hijau tidak memindahkan approval otomatis. Penerimaan baru efektif setelah ref
accepted berhasil diperbarui dan direkonsiliasi.

Release berjalan pada tip accepted yang membekukan scope milestone; tiket yang
diterima setelah freeze masuk release berikutnya. Jalankan regression/integration
tests pada verification target release, tampilkan build/evidence, lalu tunggu
approval pengguna untuk target itu. Jika repo sumber berubah, sinkronisasi
gabungan membuat release candidate/target baru dengan review, QA/regression,
dan UAT gabungan/checklist UAC terdampak. Acceptance tiket lama tetap histori;
perubahan UAC memerlukan revisi/tiket baru lebih dahulu.
Ekspor branch/patch dan deployment adalah aksi eksplisit;
repo asli tidak diubah otomatis. Target hosting ditentukan pada tahap deployment.

## Penyimpanan dan konteks

Entitas awal: projects, tickets, ticket_versions, approvals, dependencies,
messages, jobs, candidates, verifications, artifacts, releases, dan events.
Brief berada pada projects; attempt/runtime/usage berada pada jobs; preview
metadata pada candidates. Build/verification target manifests adalah immutable
artifacts; approvals/verifications menunjuk target dan evidence IDs. Input
request/answer disimpan di messages/jobs; baseline waiver di approvals.
Context snapshots dan log panjang disimpan sebagai
artefak. Keputusan proyek diterima disimpan pada docs/decisions di clone managed.

Simpan state sebelum memulai pekerjaan. Job memiliki ID unik, claim/lease,
heartbeat, jumlah percobaan, dan hasil. Setelah restart, rekonsiliasi pekerjaan
sebelum menjalankan ulang agar tidak membuat tiket, commit, atau handoff ganda.
Heartbeat berasal dari supervisor. Setiap attempt punya workspace/container sendiri.
Saat crash, rekonsiliasi proses lama sebelum retry; pertahankan log dan commit
checkpoint terverifikasi. Jangan menjanjikan resume tepat di tengah tool call.
Approval database dan update Git tidak atomik bersama: simpan integration operation
dan pulihkan operasi tertunda secara idempotent sebelum menjadwalkan dependency.

Waiting input memakai request ID, scope, recipient attempt/generation, dan jawaban
idempotent. Run ownership berbeda dari slot execution. Request/checkpoint disimpan
sebelum melepas slot; penantian lama menghentikan resource dengan tertib. Jawaban
valid melanjutkan generation baru; balasan setelah cancel/revisi tetap histori.
waiting_input, waiting_quota, stopped, failed, dan cancelled dibedakan.

Commit/build/evidence dipin selama dirujuk kandidat aktif, approval, atau release.
Cleanup memeriksa referensi produk; artefak hilang diberi status unavailable.
Backup/restore lokal offline mencakup SQLite konsisten, managed Git repositories,
artefak, dan manifest inventory. Restore memvalidasi refs/digests, merekonsiliasi
jobs, dan dapat membuka artefak teruji kembali. Wajib sebelum pilot selesai.

Konteks model terdiri dari instruksi peran, tiket dan UAC disetujui, keputusan
proyek relevan, potongan kode, percakapan terbaru, dan ringkasan history lama.
History asli tetap disimpan. UAC/approval bukan hanya bagian ringkasan chat.
Memori dan pesan diberi project ID agar proyek berbeda tidak tercampur.
Jika memakai Hermes, runtime home dipisahkan per proyek/peran/attempt. Memori
global built-in dimatikan untuk pilot; aplikasi menyediakan konteks proyek.
Lokasi SOUL, tool policies, dan resume diverifikasi pada versi runtime dipin.

Rahasia provider tersimpan di konfigurasi backend dan tidak dikirim ke frontend.
Catat penggunaan token, biaya jika tersedia, tool calls, dan batas retry.
Supervisor menegakkan batas finite durasi aktif/model calls/tool calls dan
output/token bila terukur. Usage per scope/tiket diakumulasi lintas retry/attempt;
repair limit tidak menggantikan batas satu run. Cap biaya mengikuti data provider,
tanpa menjanjikan tagihan presisi untuk request in-flight/usage yang tidak tersedia.
Quota habis membuat pekerjaan menunggu; tidak otomatis memakai model berbayar.
Sisakan kapasitas request interaktif pada limiter provider; chat tetap bisa
waiting_quota meskipun memiliki lane sendiri.
Tempatkan prompt stabil di depan agar ramah cache; ukur cache hit bila provider
melaporkannya. Budget ditentukan dari percobaan, bukan estimasi biaya per tiket.

## Layar MVP

1. Proyek: buat baru atau pilih path repo lokal.
2. Brief dan diskusi PO: breakdown kebutuhan, klarifikasi, usulan tiket.
3. Board: kolom workflow, approval kumpulan tiket, dependency, prioritas.
4. Detail tiket: UAC, komentar, aktivitas, diff, hasil QA, preview, keputusan UAT.
5. Aktivitas tim: pekerjaan aktual, pesan handoff, penggunaan model, blockers.
6. Release: scope tiket, build gabungan, hasil regression, keputusan pengguna.
Kantor virtual ditambahkan setelah alur end-to-end terbukti, dengan avatar per
peran dan aktivitas dari event yang sama dengan board.

## Tahapan implementasi

### 1. Fondasi domain dan percobaan runtime

Bangun DEV-001, lalu workspace/sandbox/broker minimum DEV-005 dan spike standalone
DEV-006 tanpa menunggu scheduler penuh. Persetujuan scope eksperimen, run manifest,
ownership, generation, bounds, dan bukti tetap dicatat; bukan bypass workflow produk.
DEV-002/003/004 membangun schema/domain/worker secara independen. Jalankan
spike satu fitur kecil, misalnya item menu dan total keranjang, Hermes + model
murah dengan container terisolasi dan acceptance runner terpisah.
Ukur tool correctness, bukti test, jumlah putaran, biaya/cache, stop, dan recovery.
Pilih runtime berdasarkan hasil sebelum membangun seluruh GUI.
Jika provider/key belum tersedia, pembangunan fondasi tetap berjalan. Spike nyata
dicatat pending; hasil fake tidak dianggap bukti kompatibilitas atau kualitas model.

### 2. Board, chat PO, dan lane interaktif

Bangun GUI proyek/brief, board, detail UAC, usulan PO, approval batch, serta
pesan antar-agent. PO tetap dapat menjawab ketika run developer sedang berjalan.
Jika provider quota habis, UI menampilkan waiting_quota. DEV-007 bisa selesai
dengan fake/contract checks; percakapan PO nyata wajib dibuktikan pada pilot.
Simpan state dan events; buktikan restart/reconnect tidak kehilangan approval.

Perubahan status tidak boleh bergantung pada parsing prosa bebas semata.
Validasi output terstruktur dan hasil tool. Catat kegagalan integrasi agar
keputusan runtime berdasarkan percobaan, bukan asumsi dokumentasi.

### 3. Satu tiket sampai QA dan UAT

Hubungkan lead/developer/QA, harness acceptance tests, preview on-demand, feedback,
dan penerimaan tepat kandidat. Uji kandidat buruk, scope revision, stale attempt,
dan perbaikan berulang. Tidak menggunakan hasil fake sebagai bukti pengujian nyata.

### 4. Repo existing, antrean integrasi, dan release

Tambahkan clone managed, runner manifest, baseline, dependency ke accepted code,
fast-forward integration dengan recovery, perubahan base saat UAT, regression,
dan approval release. Instruksi repo diikuti dalam batas tool policies aplikasi.

### 5. Kantor virtual dan deployment

Tambahkan visual kantor interaktif setelah event workflow nyata tersedia.
Packaging VPS, connector Jira/Trello, dan mobile runner menjadi tahap lanjutan.
Mobile runner memerlukan toolchain/device yang sesuai, berbeda dari runner web.

## Kriteria percobaan berhasil

- Prompt coffee shop menghasilkan tiket profile, menu, dan transaksi dengan UAC.
- Developer tidak mulai sebelum pengguna selesai review dan memberi approval.
- Satu tiket lulus QA dengan bukti eksekusi dan bisa dicoba secara lokal.
- Pengguna dapat meminta perbaikan, lalu menerima kandidat yang sudah diuji ulang.
- Tiket independen tetap berjalan saat UAT berlangsung.
- Chat PO tetap responsif saat developer aktif; dependency kode menunggu acceptance.
- Restart tidak kehilangan approval, percakapan, status, atau referensi artefak.
- Stop/restart tidak meninggalkan executor yatim; hasil attempt lama ditolak.
- Rebase kandidat memerlukan QA/UAT baru; approval lama tidak dibawa otomatis.
- Rebuild dari SHA sama menghasilkan target/QA/UAT baru; reopen memakai artefak teruji.
- Target tidak dapat menulis accepted ref, report authoritative, atau memperoleh cookie kontrol.
- Test kosong/skipped/report palsu ditolak; baseline waiver tidak menutupi failure baru.
- Restart saat waiting_input dan batas model/tool loop tidak menggandakan pekerjaan.
- Cleanup melindungi data yang dipin; backup lokal memulihkan proyek dan bukti utuh.
- Repo hasil proyek pertama dapat dipakai sebagai repo existing untuk fitur baru.
- Repo asli dan perubahan lokal pengguna tidak dimutasi oleh agent.
- Release candidate menggunakan tip accepted yang diuji dan scope yang dibekukan.

## Ditunda setelah pilot

Developer paralel, dependency ke kode belum diterima, beberapa preview bersamaan,
connector Jira/Trello, remote runner, vector database, model lokal, mobile runner,
dan multi-host deployment. Kantor Three.js tetap bagian roadmap setelah workflow.

## Referensi yang sudah ditinjau

- Hermes integration: https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration
- Hermes memory: https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/
- Hermes context files: https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/
- React Three Fiber: https://r3f.docs.pmnd.rs/getting-started/introduction
- Playwright MCP: https://github.com/microsoft/playwright-mcp
- OpenRouter limits: https://openrouter.ai/docs/api_reference/limits
- Git worktree refs: https://git-scm.com/docs/git-worktree#_refs
- Cookie port isolation: https://www.rfc-editor.org/rfc/rfc6265#section-8.5
- Cookie configuration: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie

Dokumentasi dan harga dapat berubah; pin versi runtime dan verifikasi provider
sebelum melakukan integrasi nyata.
