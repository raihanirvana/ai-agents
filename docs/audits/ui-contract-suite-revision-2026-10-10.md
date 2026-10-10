# Kontrak UI dan suite revision QA — 10 Oktober 2026

Status implementasi: IN_PROGRESS / NOT_REVIEWED. Worker/Hermes tetap dimatikan.
Pengerjaan dimulai 10 Oktober; pemeriksaan akhir dilanjutkan 11 Oktober 2026.
Tidak ada startup, provider call atau perubahan database demo pada batch ini.
Commit/push dilakukan atas instruksi pengguna setelah pemeriksaan implementasi.

## Pemetaan permintaan ke perubahan

| Permintaan | Implementasi |
| --- | --- |
| A. TL menetapkan UI sejak awal | UiLeadPlan mewajibkan UiContract pada real pipeline plan. Literal testid, semantic identity, scope valid/acyclic; source baseline diberikan pada TL. |
| A. QA selector divalidasi tanpa model | UiContract.check_suite menolak vocabulary di luar kontrak pada propose_tests, candidate submit dan target repair. Canonical fixtures diekspansi terlebih dahulu. |
| A. Static inventory sebelum publish | ui_inventory membaca guarded build snapshot HTML/JS. Missing/incomplete mengembalikan submitted=false pada tool Developer; tidak membuat candidate domain atau berpindah fase. Report dipin sebagai evidence. |
| B. Role, label, text | Runner locate memetakan canonical semantic syntax ke public Playwright locator APIs exact. Testid/scoping/nth tetap terbatas; CSS/engine legacy tidak diubah. |
| C. Bukti DOM | Failure kandidat menyimpan bounded aria snapshot/hash/truncation serta locator candidates di authoritative report dan artefak txt. Bukan expected-value source. |
| C. Satu revisi suite | Runtime memakai satu QaSuiteRevision/validator untuk selector, action, setup, encoding/binding dan proven coverage gaps. Concern preflight hanya observasi/reuse proof, tanpa proposal narrow tambahan. |

## File dan alur

- `agents/ui_contract.py`, `agents/outputs.py`: kontrak UI structured; semua IDs
  unik, scope harus declared dan tidak siklik; role berasal dari vocabulary bounded.
- `pipeline/runtime.py`: TL → QA → Developer menerima kontrak scope yang sama.
  Pembacaan source planning dibatasi 256 KiB per file/12 ribu karakter konteks;
  file tidak tersedia atau ditolak broker dicatat tanpa menjatuhkan planning.
  Handler submit mempertahankan tool loop saat submitted=false. Diagnosis dan
  suite revision tetap pada candidate/target/verification pins yang sama.
- `pipeline/workspace.py`, `ui_inventory.py`: inventory build sebelum candidate
  publication. Contract/digest/check artifact dipin dalam target manifest.
- `pipeline/contracts.py`, `suite_revision.py`: schema/mapping/origin witnesses,
  expectation/input preservation dan bounded diff.
- `contracts/verification/acceptance.py`, `pipeline/harness.py`: exact locator APIs,
  aria failure diagnostics dan log artifacts. Browser metric sekarang mengikuti
  parameter diagnostics, bukan panjang list hasil diagnostics yang menimpa namanya.
- Instruksi TL/Developer/QA, `qa_policy.py`, keputusan QA/DSL/pipeline diperbarui.
  Known-value masking dipakai pada contract/source-safe acceptance literals,
  agar pola generik tidak mengubah testid/observasi kode yang sah.

Tidak ada dependency/package, schema DB, migrasi atau target-code privileges baru.
Runner script tetap read-only mount; perubahan code digest memerlukan refresh
target/QA yang belum diterima, bukan membawa approval lama ke runner baru.

## Batas dan invariant

Static inventory maksimal 32 MiB per file / 64 MiB parsed total. File lebih besar
boleh dilewati; bila semua required literals ditemukan di file lain, inventory
lengkap untuk vocabulary itu. Missing IDs dengan byte yang diabaikan menjadi
incomplete. IDs dinamis perlu literal assignment yang dapat ditemukan di build.
HTML attributes, JS object property, setAttribute dan dataset.testid didukung.
Dead code/komentar JS dapat memuat literal: inventory bukan parser/reachability
proof, tidak memvalidasi role/name/visibility/uniqueness, dan tidak meluluskan QA.
Source commit/build milik attempt dapat tercatat sebelum inventory gagal; accepted
ref dan workflow phase tidak berubah. Report gagal tetap disimpan/pin sebagai log.

Semantic selector menggunakan JSON string exact untuk role/name, label, text.
Dynamic text hanya original fill inputs; record scope dapat memakai bounded
has_text derived dari input pada parent dynamic_text. nth hanya 0..99. Native
label/role tetap memerlukan implementasi UI yang benar; untuk hidden node pakai
testid agar tidak hanya menguji absennya accessible locator.

Snapshot aria maksimal 16 KiB, depth 12; keseluruhan DOM observation JSON maksimal
32 KiB per failure. Kandidat locator maksimal 120 node/240 entries dengan deadline
observasi 1,5 detik setelah snapshot. Snapshot error/partial tetap ditandai;
unavailable bukan bukti app fault. Pass dan baseline diagnostics-disabled tidak
membuat snapshot tambahan. Snapshot berasal dari target, sehingga isinya dianggap
data tidak tepercaya. Tidak ada JavaScript/selector bebas dari model di plan baru.

Suite revision mempertahankan semua original steps sekali dalam urutan asal,
IDs, UAC, purpose, assertion modes dan expected. Proposal default hanya mengirim
test yang berubah; supervisor menyusun suite lengkap dari original immutable
cases, mengurangi kebutuhan menyalin kasus lain. Every changed original step
mempunyai source witness; insertions adalah exact proven setup atau witnessed UI
action memakai original input. Unaffected cases tetap byte-equivalent model dump.
Budget diff: 12 changed/inserted steps per test, 24 total. Nilai dari actual app
tidak dapat menjadi replacement expected. Pengecualian representasi hanya parsed
CSV dari original input tokens dan binding label select dari original fill yang
observed unique/enabled. Fill-on-select hanya konversi berdasarkan fakta runner.
Coverage gaps tetap butuh satu action → assertion/source witness per test/UAC.

Ini lebih konservatif daripada penulisan ulang test bebas. Salah expected substantif
atau kebutuhan fixture/input baru yang tidak dapat dibuktikan tetap memerlukan
perencanaan ulang; tidak otomatis dinormalisasi dari actual. Witness/source excerpt
tidak membuktikan maksud semantik secara formal. Full browser execution dan baseline
discrimination tetap menentukan hasil; diagnosis/revision tidak meluluskan QA.

Satu validator-feedback correction dapat dilakukan dalam job/budget sama. Proposal
valid dipersist idempotent; retry memakai proposal/evidence persis. Abstain/proposal
invalid tetap gagal di QA dengan alasan, tanpa meminta perubahan source aplikasi.
MAX_SUITE_REPAIRS existing tetap; usage, lease, approval dan histori tidak direset.
Helper narrow lama tetap untuk compatibility consumers/fixtures; runtime tidak
lagi memanggil jalur per-insiden tersebut. Historical contract-less targets dan
fake foundation plans tidak dimigrasi/diberi klaim memenuhi kontrak baru.

## Verifikasi dan handoff

Pemeriksaan akhir 11 Oktober: sintaks 12 file Python menggunakan compile(source)
tanpa import/eksekusi modul dan git diff --check lulus. Pemeriksaan proses tidak
menemukan worker/Hermes berjalan. Tidak menambah/menjalankan tests sesuai instruksi sesi.
Tidak mengklaim independent review, regresi, browser QA atau kecepatan terbukti.

Sisa verifikasi perilaku sebelum DONE:

1. Schema real TL wajib UI; fake/historical shape dan suite digest tetap kompatibel.
2. Reject undeclared CSS, role/name/label dan fixture-derived scoped locator;
   accept declared exact locators, repeated-record scope dan bounded nth.
3. Inventory HTML/React/Vite/vanilla assignment, missing IDs, symlinks/limits,
   repeated submit setelah perbaikan; submitted=false tidak mematikan Hermes loop
   atau mengubah phase/accepted ref. Report/check pins bertahan recovery.
4. Browser role/label/text exact, Unicode/quotes/teks mengandung >>, hidden nodes,
   aria failure capture/unavailable/truncation dan accepted log kind.
5. Reject expected replacement, assertion deletion/type weakening, unmapped/reordered
   steps, new inputs, guessed option IDs, fabricated witnesses, unaffected changes
   dan diff overflow; accept qualifying setup/selector/binding/CSV normalization.
6. Coverage gap dengan candidate failures campuran, fresh target/baseline/candidate,
   stale lease/scope/base, double publication, retry/crash dan approval pins.
7. Bandingkan jumlah model calls/repair cycles dan fase browser pada workload sama;
   jangan memakai waktu sintaks sebagai benchmark runtime.

API yang digunakan diperiksa di dokumentasi primer Playwright:
[locators](https://playwright.dev/python/docs/locators),
[aria_snapshot](https://playwright.dev/python/docs/api/class-locator#locator-aria-snapshot).
Dockerfile existing mem-pin Playwright 1.63.0; image/provider compatibility tidak
dijalankan pada batch ini.
