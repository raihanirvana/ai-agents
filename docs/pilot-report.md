# Pilot DEV-015: coffee fixture pada repository existing

Tanggal: 2026-10-05. Pelaksana: Codex. Baseline platform: `b12d11f`, sesudah review/fix DEV-014 dan push.
Status implementasi **DONE**: release gabungan approved dan receipt diperiksa. Review kode independen Claude selesai, fix reviewer direcheck Codex;
checkpoint **R8 OPEN** sesuai [batas review](reviews/DEV-015-review-claude.md). Hasil eksekusi eksperimen bukan verdict review, UAT manual manusia, atau deployment.

## Skenario dan batas

Repo sumber adalah fixture React/Vite coffee shop yang dibuat eksplisit, dengan Node tests dan lockfile. Bootstrap
fixture adalah kode tepercaya, bukan keluaran model. Setelah itu onboarding memakai managed clone production;
repo sumber memiliki file untracked yang tidak ikut import. Fingerprint seluruh file, termasuk `.git`, diambil
sebelum pekerjaan dan dibandingkan setelahnya. Source tidak di-reset, di-stash, ditulis, atau dieksekusi.

PO/OpenRouter menerima brief tiga tiket: contact/profile independen, heading/menu independen, dan discount toggle
transaksi fixture yang hanya bergantung pada menu Accepted. Semua memakai cents, tanpa backend/payment/dependency baru.
Technical lead menyusun plan/review, QA menyusun suite lewat tools, developer Hermes mengubah kode di sandbox.
Install/build/repo tests berjalan dalam Docker; acceptance browser berjalan pada runner terpisah, memakai report
authoritative. Model tidak dapat mempublikasikan QA pass dari teks sendiri.

Model semua role: `openai/gpt-4.1-mini` melalui OpenRouter. Executor: Hermes package `0.21.5`, commit
`f97608f178d1ffeca59860195ab7da295f7c8e5f`, Python 3.13.16. Model/config/toolchain ini dikualifikasi pada skenario
sempit tersebut; hasil tidak berlaku otomatis untuk provider/model/stack lain.

Approval scope, revisi scope, feedback UAT, acceptance, dan release dilakukan eksplisit oleh
`user:dev015-qualification` pada DB eksperimen terisolasi. Ini test-user automation. Tidak ada manual UAC dalam brief,
tidak ada manusia yang melakukan UAT, dan tidak ada klaim aplikasi siap untuk pembayaran produksi. Preview diperiksa
HTTP 200 dari localhost; perilaku fitur diperiksa browser harness. GUI platform diperiksa suite tersendiri.

## Bukti alur nyata

Skrip [qualification.py](../examples/dev015/qualification.py) memakai domain/job DB, scheduler, worker, provider,
supervisor workspace, preview, integrator, onboarding dan release produk. Data privat tidak masuk Git. Receipt publik
telah ditulis oleh [summary.py](../examples/dev015/summary.py): [DEV-015-results.json](spikes/DEV-015-results.json).
Audit memeriksa DB/Git serta checksum seluruh 472 pin; receipt tidak menggantikan artefak privat untuk review independen.

- PO membuat tiga scope/dependency dari brief. Chat/revisi proposal tetap bekerja saat developer aktif; scope baru
  tidak disetujui otomatis oleh model.
- Profile/menu mencapai UAT independen sebelum profile diterima; transaksi menunggu acceptance menu. Trigger handoff
  terarah awal disisipkan oleh skrip menggunakan identity developer yang valid; jawabannya berasal dari lead/provider.
  Pesan plan, suite, implementasi dan review dari pipeline adalah keluaran role nyata, bukan fixture output.
- Preview profile dibuka lalu ditutup. Writer berhenti tertib, snapshot offline dibuat, dan restore ke root baru
  mempertahankan target/build digest preview, approval, evidence/context serta usage. Reopen tidak melakukan rebuild
  atau mengklaim QA baru.
- Feedback profile pada scope yang sama mensupersede kandidat lama. Acceptance menu menggeser base; profile mendapat
  kandidat, target, review, QA, preview dan test-user UAT baru sebelum integrasi.
- Acceptance profile juga membuat kandidat transaksi pada base sebelumnya stale; transaksi kembali development
  sebelum QA/UAT baru. Release harus memverifikasi semua fitur accepted pada satu target gabungan.

## Hasil akhir yang diaudit

Ketiga tiket Accepted: profile v1, menu v2, transaksi v2. Release `0df5b1c836274723a1e07f99d3b178c6`
approved oleh test-user pada accepted tip `5f999b802f273e16bc9a3a71891dff3975e067eb`.
Target release `589b890e41a12af4f880129b870a903fd924e026d63884a80a42470447510e6c` menjalankan
**9/9 browser tests dan 2/2 Node tests**, tanpa skip atau missing automated UAC; seluruh **13 UAC** tercakup.
Audit steps memeriksa dua klik dan nilai antara 7.20 lalu 8.00 dalam satu testcase, bukan nama test saja.
Imports dan semua executable/assertion lines unit tests awal tetap sama; hanya urutan import dan satu komentar berbeda.
Accepted ref, DB dan release cocok; fingerprint lengkap repo sumber, termasuk `.git`, tidak berubah.

Backup pertama sebelum acceptance mempertahankan preview/approval/context dan usage; preview hasil restore HTTP 200
pada target yang sama. Sesudah release approved dan cleanup, drill kedua memulihkan **585 file snapshot** ke root baru:
**472 pin terverifikasi**, target/build/evidence/approval/ref tetap, tanpa QA atau approval baru.
40 file unpinned yang telah dibersihkan tetap unavailable; tidak ada pin unavailable. Inventory checksum drill kedua:
`8b1499680783d69f276fc6371b519129ca3c3f0250df63eba262ab3461d9ea79`.
Drill kedua memakai release hasil model nyata; test recovery release terpisah tetap menggunakan QA fixture berlabel.
Tidak ada export, push atau deployment terhadap repo fixture.

## Kegagalan yang dipertahankan

Percobaan awal mengungkap wiring salah pada skrip qualification: argumen harness/redactor tertukar dan konstruksi
DockerHarness tidak menggunakan sandbox supervisor. Percobaan berbayar yang gagal tetap dihitung; tidak diganti
fake dan tidak disembunyikan dari biaya. Koreksi ini tidak mengubah kontrak runtime/provider produk.

Menu v1 berhenti setelah tiga repair terbatas. QA mengharapkan teks nama item tanpa harga, sedangkan repo asli
menampilkan `Espresso: 2.50` dan `Latte: 4.00`; satu perubahan developer menghapus harga dan ditolak lead.
Solusi: PO mengusulkan scope v2 yang eksplisit mempertahankan nama/harga dan paragraph asli, lalu test-user menerima
revisi dan approve scope baru. QA suite baru berasal dari model. Tidak ada edit suite/receipt diam-diam atau waiver
untuk kegagalan baru. Instruksi QA diperjelas agar memeriksa source aktual, memahami exact whole-element text, dan
membedakan feature/bug dari regression. Bukti dan usage v1 tetap disimpan.

Restore awal berhasil reopen preview, tetapi attempt baru gagal karena parent `runs` belum dibuat. Recovery kini
membuat direktori kosong itu; test restore juga membuat attempt baru dan memastikan lease lama tetap ditolak.
Root privat yang sudah terpulihkan diperbaiki dengan pembuatan direktori kosong saja, tanpa reset DB/Git/usage.
Kegagalan lama tetap tercatat; scheduler melanjutkan job pada base accepted baru dengan cap scope yang sama.

Transaksi v1 juga mengungkap test-plan salah: setiap test runner memakai context/page baru, tetapi test bernama
“after-two-clicks” hanya berisi satu click. Kandidat mendapat 3/4 browser pass dan tidak boleh Accepted. Repair
menyentuh paragraph yang telah diterima menu; technical review model sempat menganggap penghapusannya sesuai scope
transaksi, meskipun bertentangan dengan UAC menu. Run kemudian berhenti pada cap total token. Ini memperlihatkan
batas kualitas plan/review model, bukan QA pass atau bukti bug toggle yang pasti: ekspektasi testcase itu sendiri salah.

Instruksi QA kini menjelaskan fresh context per test dan seluruh urutan click/assert dalam satu case. Keputusan
test-user `--revise-transaction` meminta PO memperjelas scope v2: paragraph asli tetap, total baru berupa span terpisah,
dan urutan 8.00 → click → 7.20 → click → 8.00 lengkap. Proposal/approval versi baru eksplisit; cap, usage dan kegagalan
v1 tidak diedit/dihapus. Versi baru diperlukan karena kontrak preservation/markup diperjelas; bukan retry otomatis
untuk mereset budget. Hasil v2 tetap memerlukan model suite baru dan regression release gabungan.

PO revision pertama menambahkan `depends_on_keys` yang hanya valid untuk breakdown; validator menolak sesudah bounded
repair tanpa mengubah scope. Structured runtime kini menyertakan JSON Schema yang sama dengan validator untuk setiap
task dan repair, dan instruksi PO membedakan dependency ticket IDs. Regresi memeriksa schema/rejection serta proposal
tanpa approval. Setelah fix, satu retry operator eksplisit mempertahankan parent/budget/usage chat lama; PO nyata
menghasilkan proposal v2 valid dalam satu call. Tidak ada coercion field atau silent acceptance terhadap output invalid.

Developer v2 mengganti `test/cart.test.js` dengan browser test yang mengimpor Playwright, di luar locked dependencies.
Required repo gates menangkap error dan hilangnya kedua baseline Node test IDs. Tiga kandidat ditolak; tidak ada
waiver atau QA/UAT pass. Operator/test-user memberi satu repair extension (limit 3 → 4) dan arahan mengembalikan file
unit test accepted persis. Developer model melakukan perubahan itu; operator tidak mengedit code target atau suite QA.
Instruksi developer diperjelas agar menjaga unit tests/framework dan membiarkan acceptance browser pada runner QA.

Cap token kembali menghentikan pekerjaan. Dua keputusan test-user yang terbatas menaikkan cap transaksi v2 dari
250.000 → 350.000 → 400.000 total tokens, tanpa reset usage atau kenaikan call/tool/time caps. Extension kedua hanya
untuk memverifikasi kandidat yang sudah lulus Node tests, bukan rebuild kode. Semua parent jobs, authorization IDs,
usage-before dan failure receipts dipertahankan. Ini intervensi operator nyata dalam eksperimen, bukan klaim agent
berhasil tanpa bantuan atau budget extension otomatis. Semantik dua klik pada suite final diperiksa tersendiri.

## Checks automated dan fake

| Pemeriksaan | Bukti dan klasifikasi |
| --- | --- |
| Waiting input/restart/duplicate answer/stale lease/cumulative caps/quota | `tests/workers`, `tests/pipeline/test_product_loop.py`; domain/job DB nyata, runtime/provider fixture berlabel. Bukan outage/quota provider berbayar nyata. |
| Target baru dari SHA sama; approval lama tidak berpindah | `tests/domain/test_evidence.py`, `tests/preview/test_lifecycle.py`; kontrak domain serta Docker preview/build nyata, QA fixture. |
| Suite kosong/skipped/report palsu/missing coverage | `tests/pipeline/test_contracts.py`, `test_gates.py`, acceptance runner; kasus adversarial automated, bukan hasil QA model palsu yang dianggap lulus. |
| Baseline waiver spesifik | `tests/onboarding/test_waivers.py`; termasuk dua TAP Node nyata. Pilot baseline green, sehingga tidak membutuhkan waiver. |
| Offline WAL/Git/context/credentials/missing data/corruption | `tests/recovery/test_offline.py`; SQLite/Git nyata, messages/runtime fixture. |
| Restore target release/approval/evidence/cleanup pins | `tests/release/test_recovery.py`; Git/Docker/browser nyata, QA tiket fixture. |
| Explicit operator retry | `tests/workers/test_operator_retry.py`; queue nyata, fake runtime berlabel; cap/usage/parent tetap, stale lease/agent/exhausted budget ditolak. |

| Suite/perintah | Hasil aktual |
| --- | --- |
| WSL + Docker, `python -m pytest tests -q` | **853 passed**, 810,94s; sebelum fix schema terakhir. |
| WSL final, `python -m pytest tests/agents tests/pipeline -q` | **219 passed**, 157,02s; sesudah fix schema. |
| WSL recovery/workers/release targeted | **87 passed**, 23,27s. |
| Windows, `python -m pytest tests -q --ignore=tests/workspace --ignore=tests/runtime_spike` | **577 passed, 22 skipped**, 93,19s; sebelum schema fix. |
| Windows final, `python -m pytest tests/agents tests/workers/test_operator_retry.py -q` | **160 passed**, 20,79s; sesudah schema fix. |
| GUI, `npx playwright test -c playwright.web.config.ts` | **36 passed**, 27,6s. |
| `npm run build` | TypeScript + Vite lulus. |

Satu warning Starlette/httpx adalah deprecation dependency yang sudah ada. Test Git/Docker POSIX memakai WSL;
angka Windows tidak diklaim sebagai dukungan sandbox native Windows. Instruksi developer diperjelas setelah checks
tersebut; instruksi itu kemudian dipakai pada kelanjutan pilot nyata. Tidak ada perubahan UI/kontrak API pada DEV-015.

## Biaya, operasi dan sisa review

Caps pipeline per scope: 48 model calls, 160 tool calls, 1.800 active seconds, 4.096 output tokens per call,
250.000 total tokens. Interactive chat memiliki pool terpisah. Retry/restart/restore mempertahankan akumulasi usage;
revisi scope membuat versi baru yang eksplisit, dengan histori biaya lama tetap ikut total eksperimen. Tidak ada budget
extension otomatis; dua extension transaksi v2 di atas merupakan keputusan test-user eksplisit. Skrip memiliki deadline
per invocation dan guard reported cost USD 1; guard bukan cap billing provider. Resume tidak menghapus usage/cap scope,
tetapi deadline pengawas baru berlaku untuk invocation baru; jumlah keputusan operator dicatat pada receipt.
Usage unknown tidak dianggap gratis. Audit final memakai DB/usage yang dilaporkan provider, bukan invoice:

| Run | Model calls | Tool calls | Total tokens | Reported USD |
| --- | ---: | ---: | ---: | ---: |
| Pilot utama, termasuk seluruh revisi/retry sebelum dan sesudah restore | 144 | 271 | 972.146 | 0,2072300 |
| Percobaan wiring gagal yang dibuang | 4 | 10 | 10.189 | 0,0068848 |
| Total eksperimen | **148** | **281** | **982.335** | **0,2141148** |

Total input 956.823, output 25.512; unknown usage kosong. Percobaan wiring pertama berhenti sebelum DB/provider,
sehingga tidak memiliki panggilan berbayar. DB sebelum restore tidak dijumlahkan lagi karena histori yang sama sudah
disalin ke root terpulihkan. Harga invoice belum dicocokkan. Dua extension token dan satu repair tambahan eksplisit
di atas adalah bantuan operator, bukan kenaikan budget otomatis atau bukti workflow berhasil tanpa intervensi.

[Runbook](runbooks/local-operations.md) mencakup setup/start/stop/provider/restart, offline backup/restore, retry operator,
lokasi bukti dan batas dukungan. [Keputusan recovery](decisions/recovery.md) menjelaskan inventory, hash/refs, fencing,
waiting request, credential baru, availability dan pin cleanup. Snapshot tidak memuat provider key atau executor homes;
percakapan/source di snapshot tetap data privat.

R8 belum ditutup: review kode DEV-015 dan recheck fix reviewer selesai, tetapi review Claude tidak mengulang pilot
berbayar dan tidak memverifikasi UAT manual; re-review fix DEV-014 juga belum ditutup. UAT manual pengguna, paid quota outage,
multi-host restore, deployment/hosting, payment/database target dan DEV-016/017 belum diverifikasi atau dikerjakan.
