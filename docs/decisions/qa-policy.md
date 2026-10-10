# Policy QA — kontrak UI dan revisi suite, 10 Oktober 2026

Policy saat ini menggantikan jalur repair sempit per jenis insiden. Implementasi
lama di qa_repair.py yang tidak lagi dipanggil runtime dipertahankan sebagai
helper kompatibilitas; bukan jalur approval/repair tambahan.

## Scope dan otoritas

- Rencana ringan memakai beberapa journey sesuai ukuran/risiko tiket, dengan
  assertion eksplisit untuk setiap UAC automated. Tidak ada cap jumlah journey
  tambahan di prompt; batas payload DSL tetap berlaku.
- Manual UAC adalah checklist pengguna yang disetujui bersama scope. QA tidak
  boleh mengganti mode. Scope seluruhnya manual tetap mempunyai smoke bermakna.
- QA pass memerlukan report authoritative lengkap pada target/suite persis,
  required repository gates dan baseline feature/bug yang membedakan kandidat.
  Missing/skipped/incomplete/fake tidak lulus. Approval scope/UAT/release tetap pengguna.

## A. Kontrak sebelum development

TL mengeluarkan ui_contract revision 1: controls dengan testid literal, purpose,
role/name, label terkait atau text; scope_testid boleh menunjuk container yang
juga dideklarasikan. Contract mencakup kontrol, hasil, empty-state dan prerequisite
journey sesuai scope, tanpa pekerjaan tiket saudara. Semantic identity dan scope
cycle/divergence divalidasi kode. IDs bukan UAC kosmetik tambahan.

QA menerima kontrak scope saat ini secara eksplisit. Supervisor menolak selector
yang bukan anggota vocabulary kontrak pada propose_tests, submit dan target repair.
Fixture diekspansi sebelum validasi dan hashing. Tidak ada CSS bebas pada plan baru.
Kontrak/digest serta report inventory build dipin dalam immutable target/evidence.

Saat submit, supervisor membaca snapshot HTML/JS build dengan bounded filesystem
reads tanpa model atau browser. Semua testid kontrak harus ditemukan sebagai
literal attribute/object-property/DOM attribute assignment. Hilang/incomplete
mengembalikan submitted=false; Developer memperbaiki dalam job yang sama, tanpa
candidate domain, handoff atau perpindahan fase. Commit attempt/build diagnostik
boleh sudah tercatat pada broker sendiri; accepted ref tidak berubah.

Inventory hanya membuktikan adanya literal. Ia tidak membuktikan rendered DOM,
role/label, uniqueness, visibility, reachability atau fungsi. Komentar/dead code
JS dapat memuat literal; ID yang dibangun dinamis tidak lolos inventory. QA browser
serta UAT tetap diperlukan. Jangan menyebut inventory sebagai QA pass.

## B. Vocabulary locator

Utamakan role/name exact, label associated dan teks exact; gunakan testid untuk
fallback/elemen hidden. Native labels/roles berasal dari UI yang diimplementasikan.
Vocabulary tidak menebak generated IDs. Scope memakai container declared; nth
hanya disambiguasi posisi 0..99 pada locator declared, bukan arbitrary expression.

Dynamic text hanya dari original fill input dalam test/fixture canonical, di
container dynamic_text. Tombol record berulang dapat memakai has_text dari input
asli di scope parent, lalu role/name atau testid child. Ini bukan izin expected
dari output aplikasi. Lihat sintaks di [DSL browser](qa-browser-dsl.md).

## C. Bukti DOM dan satu proposal revisi

Runner kandidat menangkap aria snapshot ketika test gagal: maksimal 16 KiB,
depth 12, dengan hash/truncation flag. Locator candidates dibatasi 120 node/240
entries dan waktu observasi 1,5 detik setelah snapshot. Total JSON DOM dibatasi
32 KiB per test. Snapshot dapat unavailable dan tidak membuktikan app/test fault.
Ia adalah data halaman tidak tepercaya, bukan instruksi untuk model.

Snapshot masuk report authoritative dan, bila tersedia, artefak log txt dipin
bersama screenshot/trace failure. Pass tidak membuat snapshot/diagnostic artifacts.
Baseline menonaktifkan snapshot/trace/screenshot tambahan; report/action facts
untuk pembandingan tetap dicatat. Tidak ada scripts dari model atau shared
writable mount antara target dan runner.

QA diagnosis memetakan setiap failure satu kali ke application/test/infrastructure/
unknown. Application membutuhkan approved automated criterion serta exact source
witness yang lolos guard; unresolved selector/action/input tidak mengizinkan
request_changes. Test/infra/unknown tetap di QA, tanpa repair cycle aplikasi.

Test fault dan proven baseline coverage gap memakai satu QaSuiteRevision:

Proposal default hanya mengirim affected tests dan mapping-nya, bukan menyalin
seluruh suite. Supervisor mempertahankan kasus lain dari original suite immutable.
Complete suite juga dapat divalidasi, tetapi tidak boleh dicampur partial tests.

- IDs, UAC list, purpose, seluruh original steps dan urutannya tetap.
- Semua original assertions, mode dan expected tetap. Expected arbitrer dari
  observed DOM/download tidak bisa diterima.
- Changed steps memerlukan exact shipped-source witness dan alasan. Locator
  harus dari UI contract; suite legacy memakai kandidat locator observed runner.
- New UI actions hanya pada locator declared/observed dengan witness dan original
  input. Copied prerequisite actions harus berasal dari passed setup prefix, sama
  persis, ordered tanpa duplikasi; bukan assertion baru.
- Fill-on-select hanya dikonversi dengan observed unique matching original value.
  Guessed select values hanya dibind ke earlier original fill dengan unique enabled
  label observed. Assert_value/expected lainnya tidak diganti dengan generated ID.
- Parsed CSV hanya dapat dinormalisasi deterministik dari original fill tokens
  yang sudah teridentifikasi validator, tidak dari actual CSV.
- Maksimal 12 changed/inserted steps per test dan 24 total. Unaffected tests tetap.
  Coverage gap butuh satu witness test/UAC dengan feature action → outcome assertion.
  Exact witness adalah syarat review, bukan pembuktian formal semantik.
- Proposal yang tidak didukung dapat abstain. Validator memberi satu kesempatan
  koreksi dengan alasan spesifik; respons/context disimpan, budget tidak direset.

Proposal valid dipersist idempotent untuk candidate/target/suite/verification yang
sama, lalu dibuat suite/target baru dengan source/build/review dipertahankan.
Suite repair limit yang sudah ada tetap. Target baru membutuhkan full candidate
execution serta complete baseline untuk suite/runner persis. Baseline cache boleh
memakai bukti identitas yang cocok beserta provenance; candidate tetap fresh.

## Recovery dan kompatibilitas

Qualified Developer/TL concerns tetap advisory. Early preflight menjalankan suite
kandidat asli sekali dan menyimpan proof; hasil yang sama dilanjutkan ke baseline/
diagnosis. Tidak ada jalur narrow-selector proposal terpisah atau pass dari concern.

New real TL plans wajib mempunyai ui_contract dalam schema. Historical plans,
suites/targets dan fake foundation fixtures boleh tidak mempunyai kontrak; tidak
diubah diam-diam atau diklaim memenuhi kontrak baru. LeadPlan serialisasi legacy
menghilangkan field null. Fake tetap tidak dapat membuka QA pass produk.

Runner code digest berubah. Review-approved target di QA dapat di-refresh dengan
runner identity baru tanpa mengubah source/build/suite, lalu execution ulang.
Approval lama tidak berpindah. Upgrade tidak menyalakan worker atau retry legacy.
Target drift, evidence unavailable dan diagnosis invalid menghasilkan failed
non-retryable dengan alasan; bukan crash transient yang mengulang terus.

Service reopen_qa tetap hanya untuk current UAT/candidate/verification sebelum
acceptance pengguna, dengan alasan dan fencing. Integrating/accepted serta
persetujuan pengguna tidak dapat dibatalkan oleh model. Preview/evidence lama
menjadi histori. Scope approval, lease, budgets dan usage kumulatif tetap.

## Verifikasi batch ini

Pemeriksaan sintaks dan diff dilakukan; tests/regression/browser/provider tidak
ditambah atau dijalankan. Runtime/Hermes tetap mati. Status backlog IN_PROGRESS/
NOT_REVIEWED sampai verifikasi perilaku. Detail/handoff:
[audit implementasi](../audits/ui-contract-suite-revision-2026-10-10.md).
