# DEV-009 review — Codex, 2026-10-05

Baseline: implementasi staged di atas `62a25ac`. Verdict awal **NEEDS_FIX**: AC2
(intent approval), AC3/4 (detail, input, evidence), dan AC5 (reconnect/conflict) belum
terpenuhi. Tiket dibuka IN_PROGRESS selama perbaikan, lalu kembali DONE setelah tes.
Seluruh temuan berikut sudah diperbaiki oleh reviewer; verifikasinya self-check perbaikan,
bukan re-review independen. **R5 belum ditutup.** Tidak ada commit/push; index implementasi
awal dipertahankan, perbaikan dapat dilihat lewat `git diff` dan berkas review baru.

## Temuan dan perbaikan

| ID | Level | Reproduksi sebelum fix | Perbaikan |
| --- | --- | --- | --- |
| F1 | P1 | Klik setujui scope v2, SSE mengganti ke v3: konfirmasi tetap aktif dan memakai v3/revision baru. Pilihan batch bisa berubah setelah klik pertama. | `ui.tsx`, `Board.tsx`, `Ticket.tsx`: confirmation remount saat identitas/revision/pilihan berubah; disabled berlaku juga pada tombol konfirmasi. |
| F2 | P1 | Edit draf, tab lain merevisi scope, tunggu SSE, simpan draf: API membalas **200**, menimpa perubahan lain karena editor mengirim revision terbaru. | Editor menyimpan dokumen dan revision awal; API membalas **409** untuk draf lama, draf tetap terlihat. State panel dipisah menurut ticket ID. Brief memakai fencing yang sama. |
| F3 | P1 | Centang UAC manual dan buka konfirmasi; target diganti lewat SSE. Centang/konfirmasi mengikuti target baru. Uncheck setelah konfirmasi dibuka juga tidak men-disable tombol. | Checklist/form UAT dipasang menurut scope/candidate/target/verification/evidence/UAC; konfirmasi juga terikat revision dan menghormati disabled. |
| F4 | P2 | Candidate evidence berisi QA + preview smoke, verification hanya QA. GUI mengirim evidence verification sehingga API nyata menolak UAT **409**. | Kirim `candidate.evidence_ids`, tampilkan evidence kandidat tambahan; API nyata menerima **200**, phase integrating. |
| F5 | P2 | DTO menyimpan description di versi, metadata scope hanya dependencies/revert. `asScope` menghasilkan description kosong, editor juga membuang `reverts_candidate_id`. | Gunakan fallback description versi, pertahankan metadata revert pada edit dan tampilkan perubahannya dalam diff. |
| F6 | P2 | Buka editor brief yang sudah berisi teks: textarea kosong, simpan bisa menghapus brief tanpa pengguna mengeditnya. | Isi textarea dari brief saat editor dibuka, simpan revision awal. |
| F7 | P2 | Respons POST 502: retry chat atau create project mendapat key baru. Jika command pertama telah commit, tindakan bisa terduplikasi. | Pertahankan key untuk network/5xx; key ditentukan path/body, body berbeda mendapat key berbeda. Tidak ada automatic command retry. |
| F8 | P2 | Pertanyaan blocking PO pada thread `job:*` tidak masuk chat; form chat selalu memakai endpoint input proyek yang menolak pertanyaan blocking. | Tampilkan pertanyaan user lintas thread, pilih run yang waiting pada request tersebut dan kirim revision/scope/generation ke `/runs/{id}/input`. Endpoint proyek khusus eskalasi nonblocking. |
| F9 | P2 | Pesan muncul sesudah GET messages awal tetapi sebelum GET cursor terpisah. Stream dimulai di cursor baru dan pesan tidak tampil tanpa event berikutnya. | Baca cursor sebelum refetch seluruh resource; replay dimulai dari cursor itu. Refresh tidak terus ditunda oleh event beruntun; respons/error request lama difence. |
| F10 | P3 | Stream putus lalu open lagi tanpa event state baru: indikator tetap “Menyambung ulang”. | Handler EventSource open memperbarui indikator dan memicu refresh. |
| F11 | P2 | Reply lead kepada developer disaring karena memiliki `reply_to`; “Pesan dan handoff” kehilangan jawaban. | Saring hanya proposal/decision yang sudah punya tampilan khusus, pertahankan balasan. |
| F12 | P2 | Artifact awalnya available, event unavailable diterima dan detail dimuat ulang; chip masih menampilkan tautan buka. State error/metadata juga bisa terbawa saat ID berubah. | Reset state saat ID berubah; invalidasi pada event artifact, reconnect, dan snapshot reset. Heartbeat tidak memicu hash ulang. |
| F13 | P3 | Buka detail run queued generation 0: GUI memanggil logs generation=0, API mensyaratkan >=1 dan membalas 422. | Jangan meminta log sebelum ada generation; tetap muat detail run. |
| F14 | P3 | UI menawarkan 5 siklus repair, API hanya menerima 1–3. | Max/step/clamp input mengikuti kontrak 1–3 integer. |
| F15 | P3 | Proposal telah diterima: diff membandingkan proposal dengan scope saat ini, perubahan historis judul hilang. | Bandingkan dengan versi `base_version` proposal. |

## Bukti dan metode

`tests/web-browser/review-regressions.spec.ts` menambah **20 tes**: 18 dengan respons API
tiruan dan 2 dengan API/scheduler/domain/DB/file nyata. Fixture UAT memakai receipt trusted
sintetis dari fixture domain, berlabel `contract_fixture`; ini bukan QA harness/provider
nyata. Fixture scope tidak memanggil model. Port fixture kontrol tidak ada dalam produk.

Reproduksi kode staged dilakukan dengan backup byte sumber, mengambil versi index, menjalankan
probe browser terpilih, kemudian mengembalikan seluruh sumber dalam `finally`; index tidak
diubah. Delapan regresi awal gagal sebelum fix. Probe tambahan menangkap batch/reconnect,
handoff/log/availability/retry/repair/diff; probe cursor diperketat dengan menahan respons
board kedua sampai pembacaan messages awal selesai (versi tes awal masih bisa lulus karena
urutan request). Tes real UAT menunjukkan 409 sebelum fix versus 200 sesudah. Tes real
scope menunjukkan 200 sebelum fix versus 409 sesudah. Tes fallback description juga gagal
pada sumber asli dan lulus setelah perbaikan.

Perbaikan menjaga otorisasi di backend, tidak menambah status setter atau approval agent.
Stop tetap body `{}` tanpa expected_revision; tes backend nyata dan mock tetap lulus.

## Verifikasi akhir (Windows, Chromium)

| Perintah | Hasil |
| --- | --- |
| `npm run build` | Lulus |
| `npx playwright test --config playwright.web.config.ts` | 29 lulus: 9 awal + 20 regresi |
| `npx playwright test --config playwright.api.config.ts` | 3 lulus |
| `npx playwright test` | 5 lulus |
| `git diff --check` | Bersih |

AC1 tetap dibuktikan alur FakeProvider berlabel; AC2 ditambah fencing approval dan UAT
API nyata; AC3/4 ditambah transcript/input/evidence/revert; AC5 ditambah cursor/reconnect,
retry key dan conflict editor nyata; AC6 tetap dibuktikan tiga ukuran layar dari tes awal.
Suite pytest backend/WSL/Docker tidak dijalankan ulang: backend produk tidak diubah.
Beberapa run fixture Uvicorn Windows mencetak `WinError 10054` saat koneksi browser
ditutup; run akhir tetap exit 0 dan seluruh tes lulus. Tidak diklaim sebagai bug produk
yang sudah diperbaiki.

## Batas yang tetap berlaku

Model/PO nyata milik DEV-015, pipeline/harness nyata milik DEV-010, preview lifecycle DEV-011,
dan release DEV-014. Tidak ada token streaming atau paging chat mundur. Waiver masih memakai
ID fingerprint manual. Key retry GUI belum bertahan lintas reload/unmount; backend tetap
mendukung receipt idempotent. R5 gabungan dan re-review independen atas fix masih terbuka.
