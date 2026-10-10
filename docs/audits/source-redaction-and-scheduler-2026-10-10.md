# Koreksi redaksi source dan polling pipeline — 10 Oktober 2026

Scope: lima temuan review pengguna. Implementasi selesai; verifikasi perilaku
belum dilakukan. Review independen: NOT_REVIEWED. Tidak ada mutasi DB proyek,
retry QA, reset usage, perubahan model, commit/push atau restart layanan.

## 1. Source terkorupsi oleh redactor — High

Penyebab terkonfirmasi lewat inspeksi: ToolFacade melakukan pola redaksi generik
sebelum handler write/edit, SourceTools mengubah hasil read/diff, dan Hermes
mengulang redaksi hasil tool. ContextBuilder dan ModelClient juga mengulang pola
yang sama terhadap source yang dikirim ke TL/QA. Assignment seperti
`const token = localStorage.getItem("token");`,
`user.password = form.password.value;`, serta anotasi `const secret: string`
berpotensi diperlakukan sebagai credential, bukan sintaks program.

Perubahan:

- Redactor.redact_source hanya mengganti nilai secret terkonfigurasi yang dikenal.
  redact_value(source=True) menerapkannya pada payload source rekursif.
- Source read/write/edit/diff, inspect_app/read_repo dan suite propose_tests
  memakai jalur source, termasuk wrapper Hermes. Izin, reserve budget,
  digest/CAS, fencing dan checkpoint tetap dilakukan.
- Context menyaring brief/scope/pesan/jawaban dengan pola generik sebelum
  dirangkai. Task source dan repository excerpts memakai nilai dikenal saja;
  tidak ada pola generik terhadap prompt pipeline yang sudah dirangkai.
- Structured pipeline meminta source_safe pada context dan ModelClient agar
  respons yang memuat source witness tidak terkorupsi. Checkpoint source_safe
  memakai key berbeda dari checkpoint lama; key tugas biasa tidak berubah.
- Diff review dan artefak diagnosis/proposal mempertahankan source. Metadata
  campuran mempertahankan source_excerpt, sementara prose/log/event tetap
  memakai redaksi generik. Receipt preflight juga mempertahankan source_excerpt.

Batas: source yang terlanjur rusak atau checkpoint/artefak lama tidak ditulis ulang.
Literal yang persis sama dengan secret terkonfigurasi tetap disamarkan, sehingga
kutipan credential tersebut tidak dijanjikan cocok dengan byte asli. Unknown
credential shapes dalam source tidak lagi dideteksi oleh pola redaksi generik.
Kebijakan penolakan credential-shaped content saat onboarding existing repo
adalah pemeriksaan terpisah dan tidak berubah pada patch ini.

## 2. Prompt khusus Mini Kasir di semua proyek — Medium

Arahan katalog/pembayaran/stok masuk dan double_click payment ditemukan pada
planning dan coverage repair. Diganti dengan aturan umum: jalankan fitur baru,
assert setiap outcome UAC, gunakan transisi state/fixture untuk menghitung
expected, serta periksa state setelah tindakan invalid/berulang bila diwajibkan
scope. Tidak memaksakan layar/domain maupun action tertentu pada proyek lain.
QA policy tetap mempertahankan pemeriksaan sebanding dengan risiko.

## 3. Validasi diagnosis menjadi crash/retry — Medium

PipelineRuntime.run sekarang menangkap DomainError sebagai kegagalan workflow
non-retryable. ValueError dari tugas diagnose menghasilkan Outcome failed
non-retryable dengan reason, verification_id dan penjelasan, bukan crash yang
memicu retry otomatis. Target usang dipisahkan sebagai stale_context; laporan
hilang sebagai infrastructure; coverage gap belum terbukti sebagai test_contract.
ValueError tugas lain tetap tidak disembunyikan. Exception lease/cancel/quota
dan ModelError tetap mengikuti mekanisme supervisor/provider sebelumnya.

## 4. Konteks duplikat — Medium

- TL planning dan QA planning hanya menerima capabilities di dalam
  verification_policy.browser_capabilities. Instruksi peran menunjuk lokasi itu.
- QaPlan schema di task qa_plan dihapus: schema propose_tests tetap tersedia
  sebagai parameter tool. Validasi Pydantic dan runner tidak dihapus.
- TL review mendapat kontrak ringkas berisi suite_digest, ID/purpose/UAC,
  semua steps dengan step_index asli, dan seluruh nilai step yang tidak null.
  Tidak mendapat capabilities umum tambahan. Assert/download expectations
  tetap dibawa; indeks tidak direindex atau dipotong. Full suite tetap dipin
  sebagai artefak untuk Developer dan verifikasi authoritative.

Tidak ada klaim persentase penghematan token sebelum pengukuran request nyata.

## 5. Write lock saat scheduler idle — Medium

tick memindai tiket/jobs dalam db.read, lalu menutup snapshot itu. Hanya calon
dispatch yang membuka transaksi tulis per tiket. Di dalamnya scheduler mengecek
revision dan menghitung ulang eligibility/dependency, job aktif/cleanup,
candidate/target, key, accepted tip serta cap budget sebelum enqueue atomik.
Scheduler lain yang sudah enqueue membuat proposal berikutnya kosong. Perubahan
state/key saat jeda scan menyebabkan skip dan dipertimbangkan ulang tick berikutnya.
Tidak ada upgrade transaksi baca SQLite menjadi transaksi tulis.

Polling tetap memindai tiket/jobs; patch ini mengurangi cakupan write lock,
bukan mengubah satu slot execution, jumlah query atau membuat cache baseline.

## Handoff dan verifikasi aktual

File utama: app/agents/{redaction,tools,context,models,runtime}.py,
app/pipeline/{hermes,source_tools,workspace,runtime,review_context,qa_policy,scheduler}.py,
instruksi QA/TL, serta docs/decisions/{agents,workers,qa-policy}.md.
Diff mencakup implementasi di atas dan audit baru ini.

Perintah dari apps/backend:

```sh
./.venv/bin/python -m compileall -q app/agents app/pipeline
git diff --check
```

Keduanya lulus. Ini pemeriksaan sintaks/diff, bukan bukti tes perilaku.
Tes tidak ditambah atau dijalankan karena pengguna meminta perbaikan tanpa
meminta tes, mengikuti instruksi sesi. Full suite, concurrency runtime dan
provider nyata belum diverifikasi pada patch ini. Sebelum mengandalkan patch,
regresi yang relevan mencakup redaction/source tools, model/context/output
witness, scheduler dua caller dan perubahan scope/target/budget di antara scan
dan enqueue. Restart worker diperlukan untuk memuat kode baru.

## Insiden QA yang masih terbuka

Mini Kasir #4 (`0f1ed9906dd74577889f0d011999269f`) berhenti pada verification
`78cbd33542584e8b995521744295d18f`: dua tes timeout pada step index 8,
click selector hardcoded data-id p-1 dengan escape berlebih. Source menghasilkan
ID dari waktu dan random, bukan urutan p-1/p-2. Suite juga menetapkan tanggal
CSV 2026-10-10 tanpa fixture clock; itu asumsi rapuh yang belum dieksekusi karena
click lebih dulu gagal. Diagnosis mengklasifikasikan test_contract/needs_human;
aplikasi tidak diminta repair. Catatan ini berdasarkan pemeriksaan bukti dan
source pada sesi sebelum patch, bukan eksekusi ulang.

Insiden selector/date tersebut belum diperbaiki oleh patch lima temuan ini.
QA browser tetap merupakan gerbang wajib; usulan menjadikannya opsional belum
diterapkan. Patch ini tidak mengklaim kandidat #4 sudah lulus QA.
