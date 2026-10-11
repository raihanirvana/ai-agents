# Role UI/UX dan spesifikasi UI (UiSpec) — 12 Oktober 2026

Status: keputusan desain disetujui pengguna; implementasi bertahap mulai DEV-018.
Rencana lengkap: [docs/plans/ui-ux-role-2026-10-12.md](../plans/ui-ux-role-2026-10-12.md).

## Masalah

Tanpa spesifikasi UI, tiga role menebak hal yang sama secara terpisah: TL menulis
kontrak UI sekaligus wording, Developer memilih gaya dan teks, dan QA menebak
expected text dari kalimat UAC. Di demo 11 Oktober seluruh kegagalan QA berasal
dari test atau platform; tidak ada bug aplikasi yang tertangkap.

## Tugas UI/UX

UI/UX menghasilkan satu UiSpec per scope_version tiket:

- **Arah visual dan design token**: palet warna, font, skala spacing, radius, dan
  arah visual singkat. Token proyek diwariskan antar tiket supaya tampilan konsisten.
- **Layar dan komponen**: testid, role, label/nama aksesibel, apakah berulang
  (baris/kartu), dan scope (komponen di dalam komponen lain).
- **Copy**: key → teks berbahasa Indonesia, dengan placeholder seperti `{title}`
  yang hanya diisi input asli.
- **State**: kosong, error, sukses, loading untuk setiap layar yang relevan.
- **Feedback per aksi**: `inline`, `toast`, `native_confirm`, atau `native_alert`,
  masing-masing merujuk key copy.

UI/UX tidak menulis kode, tidak mengubah scope/UAC, dan tidak memberi approval.

## Kapan berjalan dan kapan dilewati

- Berjalan setelah scope di-approve pengguna dan sebelum TL:
  `ui_spec → technical_plan → (qa_plan ∥ development)`.
- Dilewati untuk tiket tanpa perubahan UI. UI/UX dapat mengeluarkan
  `no_ui_change`; setelah DEV-023, PO juga menandai tiket ada/tidak perubahan UI.
- Amandemen spesifikasi diminta QA atau Developer ke UI/UX dengan pola yang sama
  seperti amandemen kontrak QA → TL: revisi naik tepat satu, hanya menambah, dan
  tidak mengubah scope/approval/budget.

## Implementasi stage (DEV-021)

- Aktif per proyek lewat `workflow.pipeline.ui_ux = true`. Proyek baru dari setup
  referensi otomatis aktif; proyek lama dan scope yang sudah mulai technical plan
  tanpa UI/UX tetap memakai urutan lama, sehingga tidak ada tiket berjalan yang
  disisipi stage baru.
- Job `ui_spec` (role `ui-ux`, lane interactive, LIGHT_STAGES) memakai structured
  output `UiSpec | NoUiChange | Clarification` dengan putaran repair untuk aturan
  tambahan. Hasil disimpan sebagai pesan intent `ui_spec` per scope_version
  (`spec` null untuk `no_ui_change`); job menyimpan hasilnya untuk retry idempotent.
- Warisan: token tiket accepted terbaru (urutan approval UAT), gabungan komponen dan
  copy. Komponen warisan mempertahankan role/repeated/scope.
  Validasi supervisor mencakup seluruh identitas accepted; excerpt untuk model
  dibatasi ke 200 komponen dan 300 copy terbaru, dengan jumlah omission tercatat.
  Token warisan dipulihkan supervisor kecuali UAC secara eksplisit meminta
  perubahan/pemakaian token visual.
  Deteksi sementara memakai kata visual dan kata perubahan pada klausa UAC yang
  sama; kata tampilan/tema sendiri atau larangan perubahan tidak membuka token.
- Amandemen: QA (qa_plan) atau Developer (implement) memanggil
  `request_spec_amendment(reason)`. UI/UX membuat revisi +1 yang hanya menambah
  (screen, penempatan komponen, copy, state, feedback dan token lama tetap); TL lalu
  menjalankan amandemen kontrak dari revisi itu (boleh tanpa kontrol baru, tetap
  tidak mengubah kontrol lama); QA
  merencanakan ulang; Developer melanjutkan dari checkpoint yang disimpan saat meminta.
  Bila QA meminta saat Developer berjalan, supervisor menghentikan turn Developer
  pada tool/wait berikutnya, menyimpan snapshot sumber, dan menyerahkan alur ke
  UI/UX. Amandemen QA berikutnya tetap mempertahankan draft yang belum disubmit;
  sesudah ada kandidat atau siklus repair berganti, checkpoint amandemen lama
  tidak dipakai lagi. Repair menggunakan kandidat terakhir sesuai alur yang ada.
- `propose_ui_spec` tetap nama tool di policy untuk runtime tool-loop; stage ini
  memakai structured output yang divalidasi dengan schema yang sama.

## Approval copy

Copy **tidak** memerlukan approval terpisah. UiSpec ditampilkan di halaman tiket
(warna, font, layar, tabel copy, state) sehingga pengguna bisa meninjau, dan
pengguna tetap menilai tampilan serta wording saat UAT. Perubahan wording yang
diminta pengguna masuk sebagai revisi scope/UAC atau feedback UAT, bukan approval
copy tersendiri. Keputusan ini bisa ditinjau ulang setelah demo terukur (DEV-027).

## Turunan ke role lain

- `UiContract` diturunkan dari UiSpec (testid, role, label, repeated → dynamic_text,
  scope). TL hanya boleh **menambah** kontrol teknis (misalnya container untuk
  instrumentasi); TL tidak lagi menulis copy atau gaya visual.
- Developer mengimplementasikan token, copy, state, dan feedback persis sesuai
  spesifikasi. Cek statis build memastikan literal copy, testid, dan nilai token
  warna ada di bundle.
- **Expected value QA hanya boleh berasal dari spesifikasi UI/UX (key copy,
  template diisi input asli) atau input asli test.** QA tidak menurunkan expected
  text dari kalimat UAC. `click_dialog` hanya untuk aksi bertipe `native_*`.
- Selama transisi (sebelum DEV-022 selesai) aturan kutipan UAC per assertion dan
  validator kontrak UI yang sudah ada tetap berlaku; tiket tanpa UiSpec memakai
  jalur lama.

## Skill frontend-design sebagai referensi yang dipin

Prinsip visual mengikuti skill `frontend-design` dari repo `anthropics/skills`.
Skill ini **referensi yang dipin**, bukan dependency runtime:

- Disalin ke `agents/ui-ux/references/frontend-design/` beserta LICENSE dan catatan
  commit hash sumber. Isinya dibaca penuh sebelum dipakai karena ini prompt pihak
  ketiga; isinya data referensi, bukan instruksi yang mengikat platform.
- Tidak ada auto-update. Pembaruan hanya lewat commit yang direview.
- Instruksi role UI/UX memuat ringkasan prinsip (sekitar 3 KB); isi lengkap dibaca
  saat perlu lewat tool read-only `read_design_reference`, yang hanya bisa
  membaca folder `agents/ui-ux/references/`.

## Batas

- UiSpec bukan approval dan bukan bukti QA; ia sumber expected value dan acuan
  implementasi.
- Token warisan hanya boleh berubah jika UAC tiket ini memintanya, mengikuti aturan
  pewarisan kontrol UI yang sudah ada.
- Cek statis membuktikan keberadaan literal di bundle, bukan perilaku di browser.
