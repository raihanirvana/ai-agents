# Review kedua: blueprint dan arsitektur MVP

Tanggal: 4 Oktober 2026.
Dokumen yang diperiksa: [MVP-BLUEPRINT.md](./MVP-BLUEPRINT.md) revisi 2,
[ARCHITECTURE.md](./ARCHITECTURE.md) revisi 2,
[IMPLEMENTATION-BACKLOG.md](./IMPLEMENTATION-BACKLOG.md),
[verdict.md](./verdict.md), dan tanggapan GPT 6.1 Sol yang disertakan pengguna.

**Pendapat saya: arah revisi 2 sudah layak diteruskan ke implementasi fondasi.**
Saya setuju dengan sebagian besar koreksi GPT 6.1 Sol terhadap verdict pertama.
Yang masih perlu diperjelas adalah beberapa kontrak implementasi yang menentukan
apakah approval, isolasi, dan bukti pengujian benar-benar dapat dipercaya.
Tidak perlu mengganti stack atau menambahkan framework orchestration sekarang.

Ini review rancangan, bukan hasil audit aplikasi. Folder saat diperiksa hanya
berisi lima dokumen; belum ada kode aplikasi. `git status --short` gagal karena
folder ini belum merupakan repository Git. File ini berisi **usulan**, belum
mengubah spesifikasi maupun status tiket backlog.

## 1. Penilaian terhadap verdict pertama dan jawaban GPT 6.1 Sol

Sebagian besar temuan pertama **sudah ditangani dalam dokumen terbaru**:

| Pokok review | Penilaian terhadap revisi 2 |
| --- | --- |
| Approval setelah rebase | Benar: kandidat berubah harus melalui QA/UAT baru; approval lama menjadi histori. |
| Bukti QA | Benar: harness mencatat eksekusi, suite terpisah, UAC manual tetap dinilai pengguna. |
| Semua test harus merah di base | Koreksi tepat: regression test dapat hijau pada base dan kandidat. |
| Empat peran agent | Tepat: pertahankan identitas dan komunikasi, sesuaikan kemampuan tools dengan pekerjaan. |
| Repo existing | Managed clone melindungi repo asli; masih ada batas Git internal yang perlu ditegaskan di bawah. |
| Chat, crash, cancellation | Dua lane, supervisor, attempt fencing, dan ownership preview sudah tercantum. |
| Migration | Pengujian database kosong dan upgrade dari baseline sudah dicantumkan. |
| Harga/model/runtime | Tepat diperlakukan sebagai keputusan eksperimen; belum terbukti oleh dokumen desain. |

Saya memeriksa ulang sumber resmi untuk dua koreksi faktual yang diperdebatkan:

- Dokumentasi Hermes memang menyatakan `SOUL.md` berasal dari `HERMES_HOME` dan
  context files proyek melewati pemindaian. Ada nuansa: SOUL milik pengguna dapat
  tetap dimuat dengan peringatan saat scanner menemukan pola; jangan mengartikan
  pemindaian sebagai jaminan semua instruksi berbahaya diblokir.
  [Hermes Context Files](https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files/)
- Memori Hermes memang scoped per profile; dokumentasi juga memperingatkan agar
  dua proses agent tidak memakai home yang sama. Isolasi transcript dan tools
  tetap perlu dibuktikan pada versi runtime yang dipilih.
  [Hermes Memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)
- OpenClaw mendokumentasikan `POST /v1/responses` dan `POST /tools/invoke`.
  Endpoint pertama disabled secara default; endpoint tools mengikuti autentikasi
  dan policy. Keberadaan API membantah alasan “tidak ada API”, tetapi belum
  membuktikan kecocokan dengan kontrak adapter kita.
  [OpenResponses API](https://docs.openclaw.ai/gateway/openresponses-http-api),
  [Tools Invoke API](https://docs.openclaw.ai/gateway/tools-invoke-http-api)

Harga dan quota dalam verdict pertama tidak saya validasi ulang di review ini;
jangan menggunakannya sebagai angka biaya aktual. Verifikasi saat spike tetap
merupakan keputusan yang tepat.

## 2. Celah tambahan yang paling penting

Urutan berikut berdasarkan dampak jika detailnya dibiarkan implisit. Contoh
kegagalan adalah skenario yang perlu diuji, bukan bug yang telah ditemukan pada
aplikasi. “Prioritas tinggi” berarti kontraknya perlu disepakati sebelum komponen
terkait dibuat; bukan alasan menghentikan seluruh pekerjaan fondasi.

### A. Identitas kandidat perlu mencakup build yang benar-benar dicoba

**Prioritas tinggi.** Rujukan: arsitektur §7 dan §10; DEV-005/010/011/014.

Dokumen sudah mencatat SHA, environment, checksum artefak, dan fixture. Namun,
aturan pembukaan ulang preview pada DEV-011 masih berbunyi dari SHA yang sama,
sedangkan ikatan approval dengan build dan konfigurasi belum dinyatakan tegas.

**Contoh:** kandidat SHA X lulus QA dan UAT. Preview kemudian dibangun ulang dari
X dengan dependency, build image, atau konfigurasi berbeda. Source masih sama,
tetapi perilaku yang disetujui belum tentu sama. Perubahan fixture yang
memengaruhi UAC sudah memerlukan verification baru; dampaknya terhadap UAT juga
perlu dijelaskan.

**Usulan minimum:**

- Tetapkan satu verification target immutable: candidate ID, source/base SHA,
  scope version, build artifact digest, runner manifest revision, toolchain/image
  identity, konfigurasi pengujian, serta fixture/migration identity.
- Simpan suite digest dan hasil eksekusi dalam verification record yang menunjuk
  target tersebut. Approval UAT menunjuk target dan evidence yang ditampilkan.
- Start ulang menggunakan artefak yang sudah diuji selama artefak tersedia.
  Jika harus rebuild, buat build record baru; jangan membawa approval otomatis
  ketika kesetaraan target tidak dapat dibuktikan. Default MVP: QA/UAT ulang.
- Untuk stack yang membutuhkan proses server, simpan paket/image dan konfigurasi
  eksekusinya. Mencatat SHA saja belum cukup untuk mengidentifikasi preview.

**Tambahan acceptance criterion:** rebuild SHA yang sama dengan dependency atau
konfigurasi berubah tidak boleh muncul sebagai build yang sudah diterima.

### B. Managed clone melindungi repo asli, tetapi shared Git metadata masih perlu dibatasi

**Prioritas tinggi.** Rujukan: arsitektur §6, §9, §10; DEV-005/012.

Worktree dari managed clone tetap berbagi sebagian besar refs dan, secara
default, konfigurasi Git. Ini perilaku Git yang terdokumentasi.
[Git worktree: refs dan konfigurasi](https://git-scm.com/docs/git-worktree#_refs)

**Contoh:** agar `git commit` bekerja di sandbox, implementasi me-mount common
Git directory secara writable. Shell developer kemudian dapat mengubah ref
`accepted` tanpa melalui integrator. Guard database dan project lock tidak
membatasi penulisan langsung ke file Git tersebut.

Ini bukan alasan membatalkan managed clone. Yang kurang adalah aturan siapa
yang boleh menulis metadata repository internal.

**Usulan minimum:** common Git directory dan accepted ref dimiliki supervisor.
Sandbox mendapat source untuk attempt; operasi diff/commit/checkpoint yang
memerlukan Git dilakukan melalui broker terbatas. Broker memvalidasi attempt,
path, dan ref, serta tidak menjalankan hooks atau helper tak tepercaya di host.
Jika runtime wajib memakai Git penuh di sandbox, gunakan repository attempt
independen, lalu impor hasil melalui supervisor. Pilih satu pola pada DEV-005.

**Tambahan acceptance criterion:** shell target gagal mengubah accepted ref,
ref attempt lain, config, hooks, dan metadata proyek lain; commit/checkpoint
yang diizinkan tetap dapat dibuat. Uji juga attempt lama setelah cancel.

### C. Suite read-only belum cukup untuk menjadikan seluruh hasil test authoritative

**Prioritas tinggi.** Rujukan: arsitektur §10 “Kandidat dan verifikasi”; DEV-010.

Rancangan sudah benar memisahkan suite dari developer dan mewajibkan harness.
Yang belum rinci adalah batas antara proses test tepercaya dan kode kandidat,
serta aturan merangkum hasil menjadi “QA pass”.

**Contoh:** candidate mengubah script test menjadi perintah yang exit 0, membuat
semua test di-skip, atau menulis report palsu di lokasi output. Harness mencatat
exit code dan checksum dengan benar, tetapi belum membuktikan assertions yang
diwajibkan benar-benar dijalankan.

**Usulan minimum:**

- Bedakan gate dari repo dengan acceptance run yang invocation, suite revision,
  expected test IDs, dan konfigurasi runner-nya dikendalikan verification service.
- Untuk pilot web, jalankan acceptance E2E dalam runner terpisah dari proses
  aplikasi target; runner hanya memakai endpoint aplikasi dan data testing.
- Target tidak boleh menulis hasil authoritative. Harness merekam identitas run,
  jumlah test discovered/executed/passed/failed/skipped, dan bukti per UAC.
- Nol test, mandatory test yang skipped, report rusak, atau coverage UAC yang
  hilang menjadi incomplete/failed, bukan pass. Status manual-pending terpisah.
- Jika unit/integration test mengimpor kode kandidat dalam proses test yang sama,
  catat batas kepercayaannya. Hasil itu tetap berguna, tetapi pemisahan proses
  E2E memberi bukti independen tambahan untuk perilaku pengguna.

**Tambahan acceptance criterion:** kandidat dengan script test kosong, report
palsu, seluruh test skipped, dan bug perilaku yang sengaja ditanam ditolak oleh
gate yang sesuai. Hash membuktikan integritas artefak, bukan kebenaran test.

### D. Beda port/origin preview belum memisahkan cookie kontrol

**Prioritas tinggi.** Rujukan: arsitektur §10 dan §13; DEV-008/011.

Dokumen sudah mewajibkan origin terpisah, autentikasi, dan pemeriksaan Origin.
Namun, opsi preview pada port lokal berbeda perlu diperjelas: cookie tidak
diisolasi berdasarkan port. Cookie kontrol yang cocok host/path-nya dapat ikut
terkirim ke server preview meskipun origin berbeda. `HttpOnly` membatasi akses
JavaScript, bukan pengiriman cookie ke server.
[RFC 6265 §8.5](https://www.rfc-editor.org/rfc/rfc6265#section-8.5)

**Contoh:** kontrol memakai `localhost:8000`, preview memakai `localhost:5173`,
dan session cookie kontrol berlaku untuk host localhost dengan path `/`.
Preview server dapat menerima cookie kontrol ketika pengguna membuka preview.

**Usulan minimum:** pilih skema autentikasi dan routing lokal yang memastikan
kredensial kontrol tidak pernah dikirim ke target. Untuk skema berbasis cookie,
gunakan host kontrol dan preview yang terpisah dengan host-only cookie; jangan
memperluas Domain ke induk yang mencakup preview. Terapkan pemeriksaan Origin/CSRF
pada mutation. Tetapkan dan uji konfigurasi konkret, termasuk bila memakai iframe.
Perbedaan origin juga tidak menggantikan pembatasan jaringan container terhadap
control plane.

**Tambahan acceptance criterion:** preview uji mencatat header request dan mencoba
mutation kontrol; tidak ada session credential kontrol yang diterimanya dan
mutation ditolak. Test penolakan CORS saja tidak mencakup kebocoran cookie ini.

### E. Menunggu input perlu protokol durable, bukan hanya pesan dan pelepasan slot

**Prioritas menengah.** Rujukan: arsitektur §6, §9, §11; DEV-004/007/008.

Dokumen sudah mengantisipasi deadlock dan menyebut run menunggu input. Masih
perlu diputuskan apakah attempt tetap hidup, bagaimana lease diperlakukan,
dan bagaimana balasan diterapkan sesudah crash.

**Contoh:** developer menunggu jawaban lead, melepas slot, lalu worker restart.
Lease kedaluwarsa membuat scheduler mengulang job, sementara balasan baru tiba
untuk sesi lama. Implementasi bisa menggandakan pekerjaan atau membuang jawaban
yang masih berguna.

**Usulan minimum:** persisted input request mempunyai ID, project/ticket/scope,
run/attempt penerima, pertanyaan, status, dan jawaban idempotent. Bedakan
`waiting_input`, `waiting_quota`, `stopped`, `failed`, dan `cancelled`. Bedakan
juga ownership run dengan penggunaan slot execution.

Saat menunggu manusia lama, simpan checkpoint/context dan hentikan resource
mahal bila runtime tidak dapat disuspend dengan aman. Jawaban tetap tersimpan
di thread; resume/rerun memakai generation baru sesuai kontrak adapter.
Pertanyaan scope lama tidak boleh otomatis melanjutkan scope baru.

**Tambahan acceptance criterion:** restart saat menunggu, balasan dua kali,
balasan setelah cancel, dan balasan setelah revisi scope tidak membuat job
ganda atau memajukan attempt basi.

### F. Batas repair cycle belum membatasi satu run model yang terus berputar

**Prioritas menengah.** Rujukan: arsitektur §4, §5, §9; DEV-004/006/007.

Budget disebut dalam run spec dan eligibility; batas tiga siklus repair juga
sudah ada. Tetapi satu run developer bisa melakukan banyak panggilan model
sebelum menyerahkan kandidat, sehingga tidak pernah mencapai penghitung repair.

**Usulan minimum:** supervisor menegakkan batas durasi aktif, panggilan model,
tool calls, output/token yang tersedia, dan biaya ketika dapat dihitung.
Usage diakumulasi per scope/tiket lintas retry, bukan direset pada setiap attempt.
Jangan menyebut batas uang sebagai jaminan presisi jika provider tidak melaporkan
usage yang diperlukan; request count dan timeout menjadi batas pengaman dasar.

Dua lane juga belum menjamin chat responsif jika keduanya menghabiskan quota
provider yang sama. Sisakan kapasitas request untuk interaksi, batasi konkurensi,
dan tampilkan waiting_quota dengan alasan; jangan menjanjikan respons langsung
saat quota provider habis.

**Tambahan acceptance criterion:** model fake yang terus meminta tool berhenti
pada batas supervisor, state bertahan setelah restart, dan retry tidak
menghapus pemakaian sebelumnya.

### G. Baseline failure repo existing perlu kebijakan kelulusan yang eksplisit

**Prioritas menengah.** Rujukan: arsitektur §10; DEV-010/013.

Rancangan sudah mencatat failure existing terpisah dan tidak menyebutnya lulus.
Yang belum jelas: apakah kandidat bisa masuk UAT jika baseline gagal, serta
siapa yang boleh memutuskan pengecualian tersebut.

**Contoh:** baseline mempunyai satu test gagal; kandidat mempunyai satu test
gagal yang berbeda. Membandingkan jumlah kegagalan akan melewatkan regresi baru.
Sebaliknya, mewajibkan seluruh suite hijau bisa membuat onboarding repo existing
tidak pernah selesai meskipun masalahnya tidak terkait tiket.

**Usulan MVP:** default required checks harus pass. Bila pengecualian diperlukan,
pengguna mengakui failure baseline tertentu berdasarkan test ID, signature,
environment, dan baseline SHA; simpan sebagai waiver terbatas dan terlihat.
Agent tidak dapat memberikan waiver sendiri. Acceptance test untuk UAC yang
sedang dikerjakan tetap wajib memenuhi kriterianya. Infrastructure failure
tidak boleh disamakan dengan known application failure.

**Tambahan acceptance criterion:** failure baru dengan jumlah total yang sama
tetap menggagalkan gate; baseline waiver tidak berpindah otomatis ke error lain.

### H. Retensi artefak dan restore lokal dibutuhkan sebelum VPS

**Prioritas menengah sebelum pilot selesai.** Rujukan: arsitektur §7, §9, §13;
DEV-002/011/015/017.

Checksum dan persistence sudah ada; backup/restore baru menjadi acceptance
criteria eksplisit pada tiket VPS. Padahal pengguna lokal juga menyimpan
approval, commits, build, dan bukti di beberapa lokasi berbeda.

**Contoh:** cleanup menghapus build kandidat yang masih menunggu UAT, atau
database dipulihkan tanpa Git objects dan artefak yang dirujuknya. State kembali,
tetapi preview dan bukti yang diterima hilang.

**Usulan minimum:** pin commit/build/evidence selama masih dirujuk oleh kandidat
aktif, approval, atau release sesuai kebijakan retensi. Cleanup memeriksa refs
produk, bukan sekadar umur direktori. Artefak yang hilang diberi status unavailable.
Sediakan prosedur backup/restore lokal paling sederhana: hentikan writer secara
tertib, salin database, managed repositories, artefak, dan manifest snapshot,
lalu validasi referensi saat restore. Online backup dapat menyusul.

**Tambahan acceptance criterion:** restore satu proyek beserta approval dan
artefaknya, lalu buka ulang kandidat; cleanup tidak menghapus data yang dipin.

## 3. Penyesuaian kecil agar backlog lebih jelas

| Bagian | Usulan |
| --- | --- |
| DEV-001 vs arsitektur §14 | Pilih satu struktur direktori. Backlog menyarankan `frontend/backend/souls`, arsitektur `apps/web/apps/backend/agents`. Keduanya sah; samakan sebelum skeleton dibuat. |
| DEV-006 “spike awal” | Dependency sekarang menunggu DEV-001–005. Tetapkan bagian minimum sandbox/broker untuk spike agar eksperimen runtime tidak tertunda oleh penyempurnaan seluruh fondasi. Jangan melewati isolasi untuk mempercepatnya. |
| DEV-007 saat provider tidak tersedia | AC mencampur fake checks dengan percakapan PO nyata. Nyatakan apakah hasil nyata adalah bukti DEV-006/015, atau wajib untuk DONE DEV-007; ini memengaruhi apakah DEV-008/009 dapat lanjut tanpa key. |
| Proyek baru dan accepted ref | Jelaskan bootstrap: initial empty commit milik sistem boleh menjadi base teknis; foundation code buatan developer tetap mengikuti scope → QA → UAT. Saat ini fondasi disebut dependency, tetapi accepted ref juga disebut mulai dari foundation commit. |
| Dependency pada versi accepted | Tegaskan pin ke scope/version accepted. Revisi atau revert lewat tiket baru tidak menghapus histori penerimaan lama; perubahan kontrak yang dibutuhkan downstream harus terlihat sebagai kebutuhan revalidasi. |
| Sinkronisasi sumber saat release | Tentukan apakah rebase gabungan menghasilkan satu release candidate yang di-UAT ulang atau kandidat baru per tiket terdampak. DEV-014 menyebut reapproval, tetapi unit keputusan pengguna belum tegas. |

Ini sebagian besar perincian acceptance criteria pada tiket yang sudah ada.
Tidak perlu menambah banyak service, tabel, atau tiket besar hanya untuk
mengakomodasi review ini.

## 4. Rekomendasi langkah berikutnya

Pertahankan modular monolith, SQLite, empat peran, satu execution slot, dan
satu runtime yang dipilih lewat eksperimen. Pertahankan kantor Three.js setelah
workflow nyata, sesuai blueprint.

Sebelum komponen terkait diimplementasikan, masukkan kontrak A–D ke
DEV-005/008/010/011/012/014. Perincian E–H dapat masuk tiket worker, context,
onboarding, persistence, dan pilot sesuai urutannya.

Percobaan nyata pertama sebaiknya **satu fitur vertikal kecil**, misalnya
menambah item menu dan menghitung total keranjang, dengan kriteria:

1. Scope disetujui pengguna dan model menghasilkan perubahan yang bisa dijalankan.
2. Acceptance runner menemukan kesalahan total yang sengaja ditanam.
3. Pengguna mencoba artefak yang sama dengan artefak yang memperoleh evidence.
4. Stop/restart dan satu klarifikasi tidak menggandakan pekerjaan.
5. Perubahan base/build menghasilkan review kandidat yang tepat.
6. Durasi, usage, jumlah panggilan, dan keterbatasan runtime dicatat apa adanya.

Setelah jalur ini terbukti, perluas ke profile/menu/transaksi dan repo existing
sebagaimana DEV-015. Keputusan mengenai kualitas model murah dan kecocokan
Hermes harus mengikuti hasil tersebut.

## 5. Verifikasi review

- Membaca kelima dokumen lokal dan membandingkan spesifikasi revisi 2 dengan
  verdict historis serta tanggapan yang diberikan pengguna.
- Memeriksa sumber resmi Hermes, OpenClaw, Git, dan standar cookie yang ditautkan
  pada klaim terkait. Tidak menguji runtime atau mengonfirmasi versi terpasang.
- Memeriksa keberadaan target tautan lokal dan ID tiket yang disebutkan.
- Tidak menjalankan test aplikasi: belum ada kode aplikasi, perubahan hanya
  menambahkan dokumen review. Tidak mengubah blueprint, arsitektur, atau backlog.
