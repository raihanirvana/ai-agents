# DEV-015 — temuan pilot dan self-check implementer

Tanggal: 2026-10-05. Penulis/implementer: Codex. Baseline `b12d11f`.
Status saat self-check ditulis: **NOT_REVIEWED**. Review kode Claude kini selesai; fix reviewer direcheck Codex
([review Claude](DEV-015-review-claude.md)). **R8 OPEN**. Dokumen ini mencatat temuan nyata dan checks terhadap
perbaikan sendiri; tidak menggantikan re-review independen. Status eksekusi/receipt final ada di
[pilot-report](../pilot-report.md) dan [handoff AC](DEV-015-handoff.md).

## Temuan dan perbaikan

### R015-01 — P1: restore dapat reopen, tetapi tidak dapat memulai attempt baru

Backup sengaja mengecualikan scratch worktrees. Restore menyalin bare repo dan bundle target yang sudah diuji;
preview tetap HTTP 200 dengan digest lama. Namun parent `runs` tidak dibuat sehingga `start_attempt` menghasilkan
FileNotFoundError. Bug direproduksi pada pilot nyata setelah relocate.

Fix: restore membuat parent kosong sebelum root dipublikasikan; tidak memulihkan worktree atau capability lama.
Regresi `test_wal_refs_context_and_waiting_input_relocate_without_old_credentials` sekarang juga membuat/stops
attempt baru setelah restore. Root eksperimen yang sudah terpulihkan diperbaiki dengan mkdir saja. DB/Git/caps/usage
tetap; failure logs disimpan. Checks waiting request, stale lease, credential revocation dan context/pins tetap lulus.

### R015-02 — P1: model structured tidak diberikan schema task yang divalidasi

PO nyata mengirim `depends_on_keys` pada revisi existing ticket. Validator menolak setelah satu repair, tetapi prompt
runtime hanya berisi task/prose dan error path, tidak shape kontrak. Tidak ada scope atau approval yang terpasang dari
output invalid. Scope breakdown dan revision memiliki field dependency berbeda.

Fix: `_ask` memasukkan `TypeAdapter(union).json_schema()` ke context snapshot; request awal dan bounded repair
menerima schema yang persis sama dengan validator. Instruksi PO memperjelas flat revision dan existing ticket IDs.
Test regresi memutar jawaban invalid lalu valid, memeriksa schema kedua prompt, jumlah call, tepat satu proposal dan
scope approved yang belum berubah. PO nyata berhasil sesudah satu explicit operator retry dengan parent/usage/cap lama.
Schema membantu model; validation, idempotency, fencing, dan batas satu repair tetap authoritative.

### R015-03 — P2: QA mengira exact item text tidak mencakup harga

Menu v1 memiliki suite yang mengharapkan `Latte`/`Espresso`, padahal item asli berisi harga. Repair developer sempat
menghapus harga dan ditolak lead. Run berhenti setelah tiga repair. QA baseline/source dan semantik exact whole-element
text kini dijelaskan. PO mengusulkan v2 dengan string nama/harga asli dan paragraph, lalu test-user approve versi baru.
Tidak ada edit langsung terhadap suite lama atau waiver atas kegagalan baru. Histori/failure/usage v1 tetap tersedia.

### R015-04 — P1 pada bukti pilot: kasus dua klik hanya melakukan satu klik

Runner membuat fresh browser context/page per testcase. Suite transaksi v1 membuat case “after-two-clicks” dengan
satu click, sehingga kandidat mendapat 3/4 pass dan tidak diterima. Technical review/repair model kemudian menganggap
ini bug implementasi; total token scope akhirnya habis. Nama test/coverage ID saja tidak membuktikan semantik UAC.

Fix: instruksi QA menyatakan isolasi per test dan seluruh urutan interaction/assert dalam satu case. Revisi transaksi
v2 memperjelas sequence lengkap melalui PO/test-user dan membutuhkan suite model baru. Reviewer independen harus
memeriksa steps suite final terhadap UAC, bukan counts saja. Validator schema/report tetap tidak dapat menafsirkan
seluruh natural-language UAC secara umum; kualitas rencana QA membutuhkan review.

### R015-05 — P2: scope transaksi tidak menjaga kontrak paragraph menu secara eksplisit

Transaksi v1 memperkenalkan total dinamis; QA memilih tag `p` tanpa requirement tag dari scope. Lead sempat menerima
penghapusan paragraph asli, bertentangan dengan UAC menu Accepted. Scope v2 mempertahankan paragraph asli dan memakai
span terpisah, termasuk contact/menu preservation. Approval/suite/candidate/QA/UAT baru diperlukan. Release gabungan
tetap menjalankan regression semua Accepted; tidak ada pengecualian terhadap suite menu lama.

### R015-06 — P2: checkpoint skrip qualification hilang saat proses terputus

Domain menyimpan scope/approval v2, tetapi file facts baru ditulis dalam finally yang tidak selalu berjalan pada
terminasi proses. Resume sempat tidak mengetahui keputusan itu. Skrip sekarang menulis JSON checkpoint atomik,
menyalin baseline source fingerprint ke root terpulihkan, dan merekonsiliasi proposal decision serta versi scope
yang sudah terpasang sebelum melanjutkan. Scope tidak dipasang atau disetujui lagi. Ini perbaikan skrip eksperimen;
persistence/receipt domain produk tetap dipakai.

Wiring harness/redactor dan constructor DockerHarness pada skrip qualification awal juga dikoreksi. Percobaan gagal
berbayar tetap masuk total; fixture bootstrap dan approval test-user tidak diklaim output model atau UAT manusia.

### R015-07 — P2: developer menghapus unit tests dan memasukkan dependency browser yang tidak tersedia

Pada scope transaksi v2, developer mengganti `test/cart.test.js` dengan Playwright test. Required gates menolak ketiga
kandidat karena module/test execution gagal dan baseline test IDs hilang. Teks feedback gate berupa report ringkas
kurang membantu model memperbaiki akar masalah; operator memeriksa file/logs dan memberi file test accepted sebagai
arahan, lalu mengotorisasi tepat satu repair tambahan. Model memulihkan repo tests; tidak ada edit langsung target/QA
oleh operator, test removal waiver, atau kenaikan repair limit otomatis. Instruksi developer kini membedakan unit tests
repo dan acceptance browser QA. Feedback gate yang lebih actionable merupakan follow-up produk, belum diubah di sini.

Total token v2 berhenti pada cap. Dua bounded user decisions memperbesar cap 250.000 → 350.000 → 400.000 untuk
menyelesaikan recovery/verification, dengan seluruh usage/parent/call/tool caps tetap. Extension kedua memverifikasi
target kandidat yang sudah dibangun dan lulus repo tests. Histori ini wajib masuk laporan biaya/receipt; hasil pilot
tidak boleh diklaim tanpa bantuan operator. Tidak ada extension selanjutnya yang otomatis.

## Checks dan observasi

WSL full **853 passed** (810,94s), sebelum schema fix terakhir; final agents/pipeline **219 passed** (157,02s).
Windows full **577 passed, 22 skipped** sebelum schema fix; final agents/operator retry **160 passed** sesudah fix.
Recovery/workers/release targeted **87 passed**; GUI **36 passed**; build lulus. Instruksi developer diperjelas kemudian
untuk menjaga unit tests dan dipakai pada pilot nyata. [Receipt final](../spikes/DEV-015-results.json) mencatat release
approved dengan 9/9 browser, 2/2 Node, 13 UAC, 472 pin terverifikasi dan source fingerprint utuh. Drill release nyata
memulihkan 585 file/472 pin tanpa pin unavailable dan mempertahankan target/approval. Total reported USD 0,2141148
termasuk percobaan gagal; invoice belum dicocokkan. Imports/assertions Node awal tetap, hanya import order/komentar berbeda.

Observasi yang bukan klaim perbaikan lengkap: snapshot inventory mendeteksi corruption, tidak ditandatangani terhadap
operator yang mengganti DB/hash bersama; backup offline mengandalkan stop writers dan menolak ledger ownership aktif;
restore multi-host/remapping belum dikualifikasi. Provider quota outage belum dicoba berbayar. GUI/schema/report yang
lulus tidak menjamin QA plan semantik benar; contoh kegagalan di atas menjadi bahan review R8. Tidak ada manual UAT,
deployment/hosting atau fitur transaksi produksi. Self-check tidak menutup review independen DEV-014/015.
