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
