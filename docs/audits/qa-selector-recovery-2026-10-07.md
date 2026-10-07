# Pemulihan selector QA — 7 Oktober 2026

Status implementasi: DONE. Review independen: NOT_REVIEWED.

## Penyebab

Tiket #3 Mini Perpustakaan memakai selector `#borrower-name-input` yang tidak ada.
Input peminjam di source yang sudah diterima memakai ID dinamis dan class
`borrower-input`. QA planning sebelumnya hanya menerima daftar file; membaca
source membutuhkan tool call tambahan. Jalur diagnosis dapat menyatakan test
fault tetapi belum memiliki koreksi untuk missing fill selector, sehingga hanya
mencoba setup dari tes lulus lalu berhenti needs_human.

## Perubahan dan batas

- Planning kini menerima maksimal 20.000 karakter source UI baseline di task,
  dengan inspect_app untuk sisanya. Instruksi mewajibkan selector prerequisite
  dari source aktual; snapshot planning mendapat ruang minimum 32768 token.
- Runner tepercaya merekam alternatif input/textarea jika failed fill memiliki
  nol matches. Alternatif harus unik, visible, enabled, editable, tanpa password
  atau nilai input. Batas 80 kontrol dan 160 alternatif menjaga report bounded.
- `QaSelectorRepair` hanya mengizinkan pilihan indeks kandidat dan alasan.
  Supervisor mengecek fakta runner serta literal ID/class dalam source shipped.
  Model dapat abstain jika fungsi field tidak jelas. Semua failed test harus
  memenuhi kontrak; mixed/unsupported failure tidak dikoreksi lewat jalur ini.
- Hanya selector pada failed fill berubah. Input, assertion, expected, action
  lain, ID/UAC/purpose tetap. Proposal diarsipkan. Target/suite baru memerlukan
  eksekusi baseline/kandidat penuh, bukan pemindahan pass lama.
- Tidak mengubah aplikasi demo, approval pengguna, model, budget atau histori.
  Selector assertion/tombol, kontrol ambigu dan custom widget belum tercakup
  oleh jalur baru; diagnosis semantik model tetap memerlukan review independen.

## Bukti demo nyata

Project `b909df3151604b5a8315dead0d366042`, tiket
`fefb651d15ba461f8551af5f4d396cf3`.

Retry HTTP resmi `6d36837d6fcb4247b5b7b66efad23561` mem-refresh target ke runner
baru. Verification berikutnya merekam DOM aktual. Diagnosis/repair
`afd23edbf7834a23b6432a6f504dfa7f` memilih `.borrower-input` dari candidate_index 4.
Input tetap `Budi`; tidak ada perubahan assertion.

Candidate `b4b05cbe93914efdaf112a1f0fc2002c`, commit
`50ab9dcfeaa191e48e55d05acbf51af65dcf1e6d` tetap sama. Target baru
`6b190b52a8a81a1f461893caf30501cde4ec570e1fc407b3ada0d4d88bf79d94`.
QA job `cb024bdb58224dc786beef2ade937937` succeeded; verification
`0e6b9e7ce86043ceb5890b4ea7adb74c` passed, 2 discovered/executed/passed, 0 skipped.
Baseline 2/2 failed pada fitur yang belum tersedia. Acceptance evidence
`7eb9623bef34438a992938fa004624c7`. Tiket masuk UAT, repair_cycles tetap 0.
Diagnosis+proposal memakai 2 model calls, 28.631 token, biaya USD 0,0078065;
QA terakhir active_s 34,73. Ini biaya tahap tersebut, bukan seluruh demo.

## Verifikasi dan handoff

| Scope | File | Bukti |
| --- | --- | --- |
| Evidence kontrol aktual | contracts/verification/acceptance.py | Browser fixture menangkap kontrol editable unik; hidden/disabled/ambigu tidak ditawarkan |
| Proposal dan guard koreksi | pipeline/contracts.py, qa_repair.py | Unit tests menolak source hilang, evidence berbeda, assertion, mixed, password, indeks tidak diamati dan model-supplied selector |
| Planning/recovery | pipeline/runtime.py; agents/qa/instructions.md | Demo retry membuat target baru dan lulus full verification; existing product loop checks lulus |
| Deteksi bug tetap berlaku | tests/pipeline/test_selector_repair.py | Browser pass setelah selector dikoreksi; seeded wrong-borrower bug tetap fail pada assertion asli |

Perintah dari apps/backend dengan venv dan Docker tersedia:

```
.venv/bin/python -m pytest tests/pipeline/test_selector_repair.py tests/pipeline/test_contracts.py tests/pipeline/test_harness.py -q
.venv/bin/python -m pytest tests/pipeline/test_product_loop.py tests/pipeline/test_review_regressions.py -q
```

Kelompok pertama: 68 passed, 30,50 detik. Kelompok kedua awalnya 9 passed,
2 failed: test legacy masih menunggu tiga repair otomatis setelah browser failed,
padahal policy kini menjadwalkan diagnosis dahulu; fixture `_reject` tidak mengisi
redactor. Ekspektasi/fixture diselaraskan tanpa mengurangi assertion kegagalan
browser, repair cycle, publication atomik dan lease. Kedua tes diulang spesifik:
2 passed, 17,91 detik. Total hasil akhir 79 tes terkait lulus; bukan klaim full suite.
`git diff --check` lulus. Windows/Linux, full suite dan review independen belum
dilakukan. Planning source baru belum dinilai pada proyek/model baru terpisah.

Worker direstart dan berjalan dengan kode baru. API/web tetap hidup. Review
perubahan dengan `git diff` beserta dua file baru (audit ini dan test_selector_repair).
Belum commit/push. Langkah pengguna berikutnya adalah UAT tiket #3.
