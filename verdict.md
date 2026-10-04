Review arsitektur: Multi-agent AI Software Development Team
Dokumen yang direview: ARCHITECTURE.md dan MVP-BLUEPRINT.md. Klaim pihak ketiga sudah saya cek ke dokumentasi resmi per 4 Okt 2026.

Label: [F] = fakta yang sudah saya cek ke dokumentasi · [D] = keputusan desain (saran saya atau dari dokumen) · [A] = asumsi yang harus diuji

Verdict
Layak dibangun, dengan dua syarat. Fondasinya benar: backend jadi acuan approval, job tersimpan di database, approval terikat ke versi dan commit, dan "Accepted ≠ Released". Masalahnya bukan di bagian yang terlihat rumit. Ada tiga hal:

Rancangan belum punya aturan yang jelas soal kapan hasil UAT masih berlaku setelah kode digabung. Ini inti kebutuhan "preview per tiket + release gabungan", tapi masih ditulis "dapat memerlukan QA/UAT ulang" (ARCHITECTURE.md:361).
Hermes punya beberapa celah nyata untuk kasus ini. Isolasi folder kerja per run, memori yang bocor antarproyek, dan file instruksi dari repo yang dimuat otomatis.
Kompleksitas dipasang di tempat yang salah. Banyak mekanisme concurrency disiapkan untuk sistem yang praktis serial (satu developer aktif). Sementara bagian yang paling menentukan, yaitu kualitas coding model murah dan kepercayaan pada bukti QA, belum diuji sama sekali.
Temuan, diurutkan dari dampak terbesar
1. Validitas UAT setelah merge belum terdefinisi (Q4, Q5)
Ref: ARCHITECTURE.md:356-362, MVP-BLUEPRINT.md:93

Masalah. Kandidat tiket diuji di atas base commit lama, lalu digabung ke integration branch saat release. Commit yang dirilis jadi berbeda dari commit yang Anda terima di UAT. Rancangan tidak menentukan kapan hasil penerimaan itu masih berlaku.

Contoh gagal.

Tiket Menu dan Transaksi sama-sama bercabang dari main@A.
Keduanya lulus UAT, dan keduanya menambah migration dengan parent yang sama. Di Alembic ini menghasilkan multiple heads.
Keduanya juga mengubah routes.ts.
Saat release, resolusi konflik dikerjakan agent. Fitur Menu rusak, tetapi status Accepted tetap menunjuk ke commit lama yang sudah tidak ada di build release.
Perbaikan [D]: jalur kode yang sudah diterima dibuat linear, seperti merge queue.

main di clone yang dikelola aplikasi adalah satu-satunya jalur kode yang sudah diterima.
Setiap attempt tiket bercabang dari tip main yang terbaru.
Saat Anda menerima UAT: jika main belum bergerak, lakukan fast-forward. Commit yang diterima identik dengan yang Anda uji.
Jika main sudah bergerak: rebase, lalu jalankan ulang QA otomatis secara otomatis.
Penerimaan boleh "dibawa" ke commit baru hanya jika rebase bersih dan test hijau. Tandai statusnya accepted (carried from <sha>).
Konflik atau test gagal → kembali ke Anda.
Karena hanya ada satu developer aktif, main hanya bergerak saat ada penerimaan. Jumlah carry-over sedikit, dan "integrasi release" tinggal regression penuh pada tip main lalu tag.
Migration: wajib satu head (cek otomatis di QA). Setiap preview memakai database baru: migrate dari kosong, lalu seed. Jangan ada database yang dipakai bersama antar-preview.
2. Hermes: folder kerja per run, kebocoran memori, dan file konteks repo (Q1, Q7, Q9)
Ref: ARCHITECTURE.md:175-207, ARCHITECTURE.md:278-281

Masalah.

[F] API server Hermes tidak mendokumentasikan pengaturan folder kerja per request. Agent mewarisi konteks proses gateway. API itu juga memberi akses penuh ke toolset, "including terminal commands".
[F] Memori Hermes berlaku per profile, bukan per proyek. MEMORY.md (2.200 karakter) dan USER.md disuntikkan ke system prompt di setiap sesi. session_search mencari lewat FTS5 di semua sesi milik profile tersebut.
[F] File konteks berikut dimuat otomatis ke system prompt: SOUL.md, .hermes.md, AGENTS.md, CLAUDE.md, dan .cursorrules. File konteks dari disk dipercaya begitu saja tanpa pemindaian.
[F] Kanban Hermes sudah punya claim, TTL, heartbeat, promosi dependency, worktree, review lane, dan semantik exit code untuk quota. Rancangan membuang semua itu. Kontribusi Hermes tinggal tool loop dan koneksi ke provider.
Contoh gagal.

Satu profile "developer" mengerjakan proyek klien X, lalu proyek Y. Catatan dari X ikut masuk ke prompt Y.
Sebuah repo existing punya AGENTS.md berisi "run ./deploy.sh after changes". Instruksi ini dimuat sebagai system prompt.
SOUL.md di repo bisa saja bentrok dengan SOUL milik peran. [A] Dari dokumentasi belum jelas apakah file ini dibaca dari folder kerja atau dari HERMES_HOME.
Perbaikan [D].

Kalau Hermes dipakai: jalankan satu proses per run, dengan HERMES_HOME per kombinasi proyek×peran (atau sementara), folder kerja = worktree, terminal backend docker, dan memori dimatikan (memory_enabled: false, user_profile_enabled: false). Aplikasi yang memiliki memori, bukan Hermes.
Pilih runtime berdasarkan kualitas coding dengan model murah (Eksperimen 1), bukan berdasarkan kelengkapan protokol.
3. Hanya developer yang butuh agent runtime (Q1, Q2)
Ref: ARCHITECTURE.md:161-173

Masalah. Keempat peran diperlakukan sebagai sesi agent di runtime. Kenyataannya:

PO adalah chat ditambah output terstruktur (propose_tickets dalam JSON).
Lead membuat rencana dan review diff. Ini panggilan dengan input berupa diff dan UAC.
QA sebagian besar adalah harness yang menjalankan perintah. LLM hanya dibutuhkan untuk menulis test.
Hanya developer yang butuh loop baca → edit → jalankan → perbaiki.

Perbandingan runtime:

Opsi	Penilaian
Hermes	Masuk akal sebagai executor developer, dengan perbaikan di Temuan 2. Kelebihannya sudah punya tools, provider, interrupt, dan approval. [F] TUI gateway punya session.interrupt, session.usage, dan approval. HTTP API punya /v1/runs/{id}/stop dan /approval.
OpenClaw	[F] Berorientasi chat dan channel pesan. Ia memegang sendiri session management dan channel delivery. Saya tidak menemukan API untuk orchestrator eksternal. Tidak cocok.
LangGraph	[F] Punya checkpointer SQLite/Postgres, mode durability exit/async/sync, dan interrupt. Saat resume, eksekusi diulang dari awal node; side effect harus dibungkus sebagai task. State workflow Anda (approval yang bisa menunggu berhari-hari) sudah ada di database domain. LangGraph akan menjadi tempat penyimpanan state kedua. Untuk coding agent, "node"-nya adalah seluruh tool loop, jadi keuntungan durability-nya kecil. Tidak perlu.
Runtime sendiri	Untuk PO dan Lead: cukup klien OpenAI-compatible langsung. [F] DeepSeek dan OpenRouter sama-sama OpenAI-compatible. Untuk developer: tool loop minimal (read/patch/run/submit) sebagai pembanding di Eksperimen 1.
Perbaikan [D]. PO dan Lead memakai panggilan LLM langsung dari worker. Adapter runtime hanya untuk developer, dan mungkin untuk penulisan test oleh QA.

4. Bukti QA belum bisa dipercaya secara struktural (Q8)
Ref: ARCHITECTURE.md:338-349

Masalah. QA memakai model yang sama, melihat konteks yang mirip, dan memilih sendiri perintah yang dijalankan. Developer juga bisa mengubah test.

Contoh gagal. Test Playwright untuk "keranjang menampilkan total" hanya mengecek elemen #total ada. Test hijau di commit kandidat, padahal juga hijau di base. Bukti terlihat valid tetapi tidak membuktikan apa pun.

Perbaikan [D].

Hasil dicatat oleh harness, bukan dilaporkan agent. Verification service menjalankan perintah di container yang bersih, lalu menyimpan exit code, log yang di-hash, trace, dan screenshot.
QA menulis acceptance test dari UAC saja, tanpa melihat diff atau reasoning developer. Setiap test diberi tag @AC-n agar bisa dipetakan ke kriteria.
Merah di base, hijau di kandidat. Setiap acceptance test wajib gagal di base commit. Ini sinyal kepercayaan termurah yang tersedia.
Folder acceptance test terkunci untuk developer. Diff ke folder itu otomatis menggagalkan review.
UAC yang tidak bisa diotomasi (visual, copywriting) ditandai "manual". Kriteria ini masuk checklist UAT Anda, bukan diklaim lulus.
5. Workflow: celah transisi dan inkonsistensi (Q3)
Ref: ARCHITECTURE.md:121-159, MVP-BLUEPRINT.md:57-81

Masalah	Contoh gagal	Perbaikan [D]
Ada endpoint POST /transitions generik, dan drag-and-drop dibilang memakai "endpoint yang sama dengan agent" (MVP-BLUEPRINT.md:76, ARCHITECTURE.md:375). Ini bertentangan dengan aturan "tidak ada arbitrary status setter" (ARCHITECTURE.md:228).	Anda menyeret tiket ke kolom QA, dan backend menolak karena hampir semua transisi bukan hak Anda.	Gunakan command per intent: approve_scope, accept_uat, request_changes, submit_candidate. Drag-and-drop hanya untuk urutan prioritas. Gate memakai tombol.
Tidak ada transisi dari Development/QA/UAT kembali ke ScopeReview. Status Cancelled/Descoped juga tidak ada.	Feedback UAT berubah menjadi revisi UAC, tetapi tiket tetap di UAT dengan versi yang sudah dicabut.	Tambah edge * → ScopeReview saat versi UAC berubah, dan status terminal Descoped.
Loop Review↔Development dan QA↔Development tidak dibatasi.	Model murah bolak-balik 8 kali sambil membakar quota.	Batasi N iterasi, lalu Blocked: needs_human.
Approval batch tidak memeriksa kelengkapan dependency.	Tiket Transaksi disetujui, tetapi dependency-nya (Menu) belum.	Tolak approval jika ada dependency yang belum disetujui.
Dependency "commit/contract" ke kandidat yang belum diterima.	B dibangun di atas kandidat A. Lalu A diubah karena feedback UAT, sehingga B ikut tidak valid.	Untuk MVP: dependency hanya terpenuhi oleh kode yang sudah diterima dan ada di main. Konsekuensinya, latensi UAT Anda menjadi bottleneck. Ini harus disadari.
Gate "QA → UAT: preview siap" (ARCHITECTURE.md:132) bertabrakan dengan aturan "satu preview aktif" (ARCHITECTURE.md:352).	Tiket B lulus QA saat preview A masih aktif, sehingga B tidak bisa masuk UAT.	Gate = preview pernah lolos smoke test dan health check. Preview dijalankan sesuai permintaan Anda.
PO dan Anda sama-sama mengedit tiket saat diskusi.	Revisi bertabrakan dan expected_revision terus menghasilkan conflict.	PO hanya membuat usulan (diff yang Anda terima atau tolak). Edit langsung oleh Anda tetap tersedia.
Tidak jelas siapa yang memutuskan feedback UAT itu "bug" atau "kebutuhan baru".	Agent mengklasifikasikan feedback sebagai bug, lalu mengubah scope diam-diam.	PO mengusulkan klasifikasi, Anda yang mengonfirmasi.
6. Worker serial memblokir chat PO (Q2, Q6)
Ref: ARCHITECTURE.md:303-309

Masalah. Semua LLM berjalan sebagai job di worker. Kapasitasnya hanya satu developer slot.

Contoh gagal. Run developer berjalan 25 menit, dan pertanyaan Anda ke PO antre di belakangnya.

Perbaikan [D]. Gunakan dua lane dengan batas concurrency di tabel jobs: lane interactive (PO dan klarifikasi) dan lane execution (dev, QA, review). Perbaikan dari feedback UAT mendapat prioritas di lane execution.

7. Durability: realistis, tetapi titik lemahnya di proses anak, bukan di database (Q6)
Ref: ARCHITECTURE.md:297-322

Claim atomik, lease, fencing, dan at-least-once sudah benar. Yang terlewat:

Proses yang tertinggal. Agent menjalankan npm run dev, Playwright, atau container lain. Stop atau crash worker tidak mematikan semuanya.
Contoh gagal: setelah restart ada port 5173 yang masih terpakai dan tiga Chromium zombie.
Perbaikan: satu container per attempt, diberi label job_id/attempt. Saat startup, worker membersihkan semua container yang tidak punya lease aktif. Stop berarti docker kill container itu, bukan menghentikan proses agent saja.
Heartbeat dari supervisor, bukan dari agent. Jika agent yang mengirim heartbeat, npm install yang berjalan 6 menit tanpa tool call membuat lease kedaluwarsa.
Retry setelah crash.
Untuk MVP: buang worktree attempt lama dan mulai attempt baru dari base. Selalu satu worktree per attempt, bukan hanya "jika proses lama tidak bisa dihentikan".
Checkpoint yang murah: agent membuat WIP commit di branch attempt. Resume = attempt baru dari WIP commit terakhir yang lolos build.
Biaya retry = biaya satu run LLM penuh. Retry otomatis cukup sekali untuk error transient. Selebihnya Blocked.
Bagian yang bisa disederhanakan. Untuk satu proses worker, "capability token dengan lease generation" (ARCHITECTURE.md:223) cukup diganti UPDATE … WHERE id=? AND attempt=? AND lease_owner=? saat submit.
8. Repo existing: worktree berbagi .git dengan repo pengguna (Q9)
Ref: ARCHITECTURE.md:331-336

Masalah.

git worktree add di repo Anda berbagi refs, config, hooks, dan stash dengan worktree utama.
[F, perilaku git] Satu branch tidak bisa di-checkout di dua worktree.
Hook pre-commit Anda akan ikut berjalan di commit agent.
File .env bisa dibaca agent lalu terkirim ke provider cloud.
Contoh gagal.

Agent menjalankan git checkout main di worktree-nya, gagal, lalu "memperbaiki" dengan git worktree prune atau --force.
Atau agent menjalankan git stash di repo bersama, sehingga perubahan lokal Anda ikut bergeser.
Perbaikan [D].

Clone ke workspace yang dikelola aplikasi, bukan worktree di repo Anda. Worktree hanya dibuat di dalam clone itu.
Hasil dikirim balik sebagai branch yang Anda fetch, hanya setelah release disetujui.
Container hanya me-mount clone tersebut. Daftar terlarang untuk dibaca: .env*, *.pem, id_*. Agent tidak diberi kredensial push.
Prioritas instruksi: kebijakan aplikasi > instruksi repo. Instruksi repo diperlakukan sebagai data, dan perintah berbahaya ditolak oleh tool, bukan oleh prompt.
Jika Anda terus commit ke main selama agent bekerja: saat release, rebase ke main Anda lalu jalankan regression ulang. Ini sama dengan mekanisme di Temuan 1.
9. "Model gratis" adalah penghematan semu; caching yang menentukan biaya (Q7, Q10)
[F] OpenRouter, model :free:

20 request per menit.
50 request per hari jika total credit yang pernah dibeli di bawah $10.
1.000 request per hari jika sudah membeli minimal $10.
Satu run developer bisa memakai 30–100 request [A]. Dengan 50 request per hari, praktis kurang dari satu tiket per hari.

[F] Harga resmi DeepSeek deepseek-flash (V4.1-Flash), off-peak:

Komponen	Harga per 1M token
Input, cache miss	$0.15
Input, cache hit	$0.003
Output	$0.60
Konteks 1M token dan mendukung tool calls.
Harga peak dua kali lipat. Jam peak: 01–04 dan 06–10 UTC, Senin–Jumat = 08–11 dan 13–17 WIB, tepat di jam kerja Anda.
Estimasi [A]. Satu run dev: 60 putaran × ~30k token input, dengan cache hit 80% → kira-kira $0.10 per run off-peak. Jadi credit $10 jauh lebih realistis daripada tier gratis.

Perbaikan [D].

Context builder harus ramah prefix cache. Bagian yang stabil ditaruh di depan: SOUL singkat, aturan proyek, UAC. Bagian yang berubah-ubah di belakang. Jangan ada timestamp atau ID di awal prompt.
Catat persentase cache hit per run.
Rencana teknis per tiket digabung ke run developer. Lead cukup membuat arsitektur proyek sekali, lalu review.
Review oleh LLM baru dijalankan setelah gate deterministik (lint, typecheck, test) hijau.
Residensi data: kode repo existing akan terkirim ke provider. Ini keputusan Anda (lihat Pertanyaan).
10. Kompleksitas yang belum perlu (Q2)
Ref: ARCHITECTURE.md:97-114, ARCHITECTURE.md:238-259

Dokumen mencantumkan 14 modul dan 18 tabel. Untuk MVP:

Tunda:
connector_links, Board connectors, dan Release service sebagai modul terpisah. Release cukup = tag + regression di tip main.
Capability flags adapter.
Simulator yang lengkap. Cukup fake provider dengan skrip respons untuk test workflow.
Pindahkan decisions ke repo target (docs/decisions/*.md). Pengetahuan proyek jadi tetap ada di luar tool dan otomatis menjadi konteks.
context_snapshots cukup berupa hash ditambah file di folder artefak.
Pertahankan: pemisahan API dan worker, tabel jobs, events dan SSE dengan Last-Event-ID, serta approval per versi. Semuanya murah dan tepat.
11. Preview lokal/VPS dan perangkat mobile (Q10)
Lokal [A]. Laptop 8 GB dengan Docker Desktop kira-kira hanya muat satu container dev, satu preview, dan satu Chromium Playwright. node_modules per worktree bisa 0,3–1 GB. Gunakan shared store (misalnya pnpm) dan bersihkan worktree yang sudah selesai.
VPS.
Minimal 4 GB RAM untuk build ditambah Playwright [A].
URL preview harus ikut login. Preview adalah aplikasi tanpa autentikasi yang berisi data uji.
Routing berbasis path sering merusak SPA yang memakai path aset absolut. Gunakan port atau subdomain per preview.
Kode yang dihasilkan agent berjalan di host yang sama dengan API key provider. Isolasi container menjadi wajib, bukan opsional.
Mobile.
Membuka GUI dari HP: backend yang bind ke localhost tidak bisa dijangkau. Perlu bind ke LAN dengan login, atau sebuah tunnel. Ini hanya ditambahkan jika memang dibutuhkan.
Kantor Three.js di HP: matikan secara default.
Drag-and-drop di layar sentuh perlu library yang mendukung touch.
Membangun aplikasi mobile: di luar scope, karena butuh toolchain lain (Xcode di macOS, emulator), bukan Playwright.
Arsitektur MVP paling sederhana

Browser ─REST/SSE─ FastAPI (API + serve static build)
                      │ SQLite WAL: projects, tickets, ticket_versions(UAC),
                      │   approvals, dependencies, messages, jobs(+attempts/usage),
                      │   candidates(+verification), artifacts, events
                   Worker (1 proses, 2 lane: interactive | execution[1 slot])
                      ├─ PO / Lead      → panggilan LLM langsung (OpenAI-compatible) + JSON schema
                      ├─ Developer      → adapter runtime (Hermes per-proses ATAU loop sendiri)
                      │                    dalam container per attempt, mount 1 worktree
                      ├─ QA             → LLM menulis acceptance test dari UAC;
                      │                    harness menjalankan & mencatat (red-on-base/green-on-candidate)
                      └─ Preview        → 1 container aktif, DB baru + seed, health check
Git: clone dikelola per proyek; main = jalur kode yang sudah diterima;
     branch per attempt dari tip main; merge queue serial; release = tag + regression.
Layar MVP:

Board dengan tombol gate. Drag-and-drop hanya untuk prioritas.
Detail tiket: diff UAC, bukti, preview, terima/feedback.
Chat PO dengan usulan berupa diff.
Feed aktivitas dan biaya.
Yang ditunda
Kantor virtual Three.js. Event-nya sudah disiapkan, jadi bisa ditambahkan belakangan tanpa mengubah backend.
Connector Jira/Trello.
Developer paralel.
Dependency ke kandidat yang belum diterima.
Beberapa preview aktif sekaligus.
Remote runner antara laptop dan VPS.
Embeddings atau vector database.
PostgreSQL.
Model lokal.
Mobile runner.
Akses GUI dari HP, kecuali Anda membutuhkannya sekarang.
5 eksperimen awal
Runtime dan kualitas coding.
Bandingkan Hermes (satu proses per run, HERMES_HOME per proyek, memori mati, backend docker) dengan loop minimal buatan sendiri, pada 3 tiket coffee shop memakai deepseek-flash.
Ukur: lulus acceptance test tersembunyi, jumlah putaran, token, persentase cache hit, biaya, dan waktu.
Verifikasi juga: stop mematikan seluruh proses anak, session.usage akurat, dan apakah SOUL.md/AGENTS.md dari repo ikut dimuat.
Merge queue dan migration.
3 tiket dengan file yang tumpang tindih dan masing-masing membawa migration.
Ukur: berapa kali penerimaan bisa dibawa ke commit baru versus harus UAT ulang, efektivitas pengecekan satu head, dan waktu membuat preview dengan database baru.
Kepercayaan QA.
Tanam bug secara sengaja di 5 kandidat.
Ukur: berapa yang tertangkap, apakah semua acceptance test merah di base, dan apakah developer mencoba mengubah test.
Uji crash dan cancel.
kill -9 worker di tengah run dev dan di tengah npm install, lalu coba sleep laptop.
Pastikan: tidak ada container yatim, attempt lama ditolak saat submit, job diklaim ulang, dan biaya retry tercatat.
Realitas budget.
Jalankan satu alur penuh: brief → 3 tiket → release.
Catat: request per hari, token, dan biaya peak versus off-peak.
Bandingkan dengan batas OpenRouter free (50 dan 1.000 request per hari).
Pertanyaan yang mengubah arsitektur
Stack target: dibatasi atau bebas? Jika proyek baru selalu memakai satu stack pilihan Anda, preview, QA, dan pengecekan migration bisa dibuat spesifik dan andal. Jika harus mendukung repo existing dengan stack apa pun, preview dan QA harus generik (deteksi atau konfigurasi per repo). Ini memperbesar onboarding secara signifikan.
Bolehkah kode repo existing dikirim ke provider cloud (DeepSeek, atau provider gratis di OpenRouter)? Jika tidak, pilihan provider dan model berubah total.
"Mobile" maksudnya memantau atau approve dari HP, atau membangun aplikasi mobile? Yang pertama hanya soal autentikasi dan akses jaringan. Yang kedua adalah lini runner yang terpisah.
Kalau mau, review ini bisa saya jadikan halaman yang bisa dibagikan.

Sumber:

Hermes Programmatic Integration
Hermes API Server
Hermes Kanban
Hermes Memory
Hermes Configuration
OpenClaw Agent runtime
OpenClaw Multi-agent
LangGraph Durable execution
LangGraph Functional API
OpenRouter Limits
DeepSeek Pricing