# Review DEV-021 (F–H) — 12 Oktober 2026

Reviewer: Codex. Baseline: `1c0e770bf26f0dbcd8d4d64fae0780269e59d263`.
Target: working tree DEV-021, termasuk test stage baru dan perubahan eksternal yang
telah ada pada awal review. Pengguna mengotorisasi review, fix, commit dan push.
Fix/recheck dilakukan reviewer yang sama; bukan independent review kedua atas fix.
DEV-022 dan aktivasi UI/UX untuk DB proyek demo berada di luar scope.
Verdict akhir: DEV-021 DONE / REVIEWED setelah fix dan recheck; tidak ada blocker
tersisa pada scope F–H yang diuji.

## Temuan terkonfirmasi dan perbaikan

| ID | Severity | Lokasi | Pemicu / dampak | Fix dan bukti |
| --- | --- | --- | --- | --- |
| F-01 | P2 | `runtime.py`, `_ui_spec.rules` | Amandemen menambah copy, tetapi menghapus/mengubah state atau feedback lama. Memindahkan komponen ke screen lain atau mengubah purpose screen juga lolos. Perbandingan hanya melindungi komponen/copy/token dan menghitung pertumbuhan daftar. | Pertahankan setiap screen/purpose dan keanggotaan komponennya; seluruh state/feedback lama harus hadir persis, termasuk multiplicity state. Enam kasus parametrik mereproduksi bypass sebelum fix, kemudian masuk putaran repair dan menyimpan spesifikasi aditif yang benar. |
| F-02 | P2 | `runtime.py`, `_requests_visual_change` | UAC daftar yang menyebut "tampilan" atau "tema buku" membuka semua token proyek. Ketentuan agar warna/font tidak berubah juga dianggap meminta perubahan. | Perlu subjek visual dan instruksi perubahan/pemakaian dalam klausa UAC yang sama; negasi umum tidak membuka token. Test UAC display mempertahankan warna warisan; UAC eksplisit perubahan warna tetap mengizinkan perubahan. |
| F-03 | P2 | `runtime.py`, `_implement` / `_await_qa_plan`; `hermes.py` | QA meminta amandemen ketika Developer sudah berjalan paralel. Scheduler menunggu Developer selesai, sedangkan Developer menunggu suite yang tidak akan dibuat; hasilnya gagal atau menghabiskan budget. QA juga bisa mengajukan suite setelah meminta amandemen pada job yang sama. | Request persisten diperiksa sebelum tool Developer dan saat menunggu suite. Handoff menghentikan turn, menyimpan source checkpoint, dan menghasilkan request Developer yang dapat dilanjutkan scheduler. Hermes meneruskan sinyal handoff sebagai penghentian turn, bukan error tool yang perlu diperbaiki model. Submit/amandemen Developer memakai lock yang sama; tool setelah request tidak melanjutkan mutasi. QA menolak suite sesudah request. Regresi memakai job QA dan Developer nyata dalam DB, source workspace nyata, serta fake driver; kedua jalur tool dan wait memulihkan isi file draft. |
| F-04 | P2 | `scheduler.py`, `_proposal` | Setiap development setelah spesifikasi revisi >1 mengambil checkpoint amandemen lama, termasuk repair setelah kandidat lengkap sudah disubmit. Snapshot draft menimpa jalur pemulihan kandidat dan membuang perubahan sesudah amandemen. | Resume hanya dari development sukses terakhir yang masih berupa request amandemen pada siklus repair yang sama. Payload mem-pin repair_cycle. Setelah submit, repair memakai kandidat; rangkaian amandemen QA sebelum submit tetap membawa checkpoint draft. Kedua kasus diuji. |
| F-05 | P2 | `runtime.py`, `_inherited_ui_spec` / `_ui_spec.rules` | Gabungan komponen accepted dipotong menjadi 200 sebelum pemeriksaan supervisor. Sesudah tiga spesifikasi besar, identitas di luar potongan itu boleh didefinisikan ulang tanpa pemeriksaan role/repeated/scope. | Supervisor mempertahankan seluruh identitas; hanya konteks model dibatasi ke 200 komponen/300 copy terbaru, dengan jumlah omission tercatat. Repair menyertakan struktur accepted yang wajib dipakai, termasuk jika kontrol tidak ada dalam excerpt. Test memakai tiga acceptance contract fixture dalam DB (288 kontrol) dan memperbaiki perubahan role atas kontrol yang tidak ada pada excerpt model. |

Sepuluh regresi baru gagal pada tree awal (7 test implementer tetap lulus),
membuktikan empat temuan di atas. Setelah fix, 29 test stage/contract-amendment/
scheduler lulus. Run antara sempat memiliki tujuh failure pada assertion test baru:
normalisasi default Pydantic dan fake QA yang meminta amandemen lagi tanpa sengaja;
assertion/driver dikoreksi, kemudian run 29 test tersebut lulus.

Perbaikan pendukung: prompt structured UI/UX memakai ringkasan prinsip role, dan
tidak memerintahkan pemanggilan tool referensi yang tidak tersedia dalam turn
structured. Tambahan regresi mencakup retry UiSpec/no_ui_change tanpa panggilan
model/pesan baru, larangan suite setelah request, serta checkpoint lintas amandemen.

## Pemetaan acceptance criteria

| AC / scope | Bukti |
| --- | --- |
| Stage interactive/light; scope approved | Mapping task/role dan pemeriksaan eligibility di runtime; LIGHT_STAGES memasukkan ui_spec; scheduler memilih interactive dan scope yang eligible. Test urutan awal menegaskan role/lane/revisi dan konteks TL. |
| UAC, warisan accepted, source baseline | ContextBuilder mem-pin approved scope; `_inherited_ui_spec` mengambil kandidat accepted menurut approval UAT; helper source membaca accepted Git base, termasuk CSS untuk UI/UX, tanpa mengeksekusi source. Test pewarisan struktur/token memakai fixture warisan. |
| UiSpec/no_ui_change, intent per scope, idempotent | Union schema + check pada repair/backstop; pesan intent dan hasil job ditulis dalam satu transaksi berlease. Test no_ui_change serta retry memeriksa satu pesan dan satu model call. |
| Token warisan konsisten | Supervisor memulihkan token tanpa permintaan visual eksplisit; test UAC display, negasi, perubahan warna dan konflik repeated row. |
| Revisi +1 dan hanya menambah | Validasi revisi, copy/token/komponen serta fix F-01; output invalid diperbaiki dalam job yang sama. TL menerima revisi dan membuat kontrak aditif, termasuk tanpa kontrol baru. |
| Scheduler dan amandemen QA/Developer | ui_spec → TL → QA/development; legacy scope tidak disisipi. Request persisten, checkpoint/resume, QA replan, handoff paralel dan repair diuji. Budget pool pipeline dan approval/scope tetap mengikuti queue/domain yang ada. |

## Verifikasi aktual

Dari `apps/backend`:

```sh
env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest tests/pipeline tests/agents tests/domain tests/workers tests/persistence -q --tb=short
env PATH=/Users/23061535/homebrew/bin:/usr/local/bin:/usr/bin:/bin .venv/bin/python -m pytest tests/pipeline/test_ui_spec_stage.py tests/pipeline/test_qa_plan_wait.py -q --tb=short
```

| Selection / tahap | Hasil aktual |
| --- | --- |
| Lima grup regresi di atas; collection sebelum tambahan negasi/warisan besar dan update stub | 866 passed, 2 failed, 10 warnings, 623.42 detik |
| `test_ui_spec_stage.py`, tree dengan guard negasi | 24 passed |
| `test_ui_spec_stage.py` + `test_qa_plan_wait.py`, tree final termasuk F-05 | 29 passed (25 + 4), 25.03 detik |

Dua failure run besar adalah test wait yang memakai `SimpleNamespace(suite=...)`
tanpa metode workspace baru `ui_spec`. Stub diperbarui dengan `ui_spec -> None`
untuk scope legacy; keempat test wait lulus ulang. Tidak ada failure tersisa.
Total cakupan akhir: **871 kasus unik** (868 pada collection run besar, dengan dua
failure diperbaiki/rechecked, ditambah tiga kasus setelah collection). Recheck
terarah mencakup seluruh perubahan runtime sesudah run besar dimulai: guard negasi,
excerpt warisan versus validasi penuh, prompt structured dan stub wait.
Sepuluh warning run besar berasal dari refleksi expression index SQLite oleh
SQLAlchemy/Alembic. Test fixture F-05 sempat gagal karena metadata identity fixture
kurang lengkap; fixture diperbaiki, test tersebut dan selection final lulus.

`git diff --check` lulus; file baru juga dibaca dalam review.
Log regresi: `/private/tmp/dev021-regression.log` (lokal, tidak di-commit).
Fake provider/driver diberi label; Docker/Git/DB workspace dipakai nyata. Hasil ini
tidak membuktikan kualitas model UI/UX nyata, kompatibilitas provider, atau QA/UAT
nyata. Provider nyata tidak dipanggil. Suite backend di luar lima grup tersebut
dan frontend tidak dijalankan karena perubahan runtime backend saja.

## Diff dan handoff

Sesudah commit, diff termasuk file baru tersedia melalui `git show`.
Patch review juga disiapkan di `/private/tmp/DEV-021-review.patch`.
File baru: `app/pipeline/handoff.py`, `tests/pipeline/test_ui_spec_stage.py`
(keduanya di `apps/backend`), dan catatan review ini. File tracked: policy tool,
runtime, scheduler, setup, workspace, queue, stub test tunggu QA, backlog dan keputusan UI/UX.

Known limits: flag proyek existing/demo tetap mengikuti konfigurasi semula.
Frontend label/office/type UiSpec dikerjakan DEV-025; Activity sudah memiliki
fallback label role. Konsumsi UiSpec oleh Developer/QA dan penurunan kontrak TL
tetap DEV-022. Generic reply ke ui-ux tetap diblokir; amandemen kini memakai stage
terstruktur khusus, bukan reply generic yang belum didukung. Deteksi permintaan
visual sementara berbasis kata pada UAC, belum klasifikasi semantik/flag PO.
