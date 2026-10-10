# Audit demo Mini Perpustakaan selesai — 7 Oktober 2026

Scope: tiga tiket accepted pada project `b909df3151604b5a8315dead0d366042`.
Belum ada release di database. Perbaikan selector QA telah dipush sebagai
`f05d004`. Audit ini mencatat rekomendasi; tidak mengubah runtime atau model.

## Metode dan hasil

Usage dijumlahkan sekali dari `Job.usage` per job, bukan public scope usage
yang sudah terakumulasi lintas retry. `input_tokens` dan alias `prompt_tokens`
tidak dijumlahkan bersama. Biaya adalah reported provider cost, bukan invoice
independen. Active time adalah akumulasi waktu aktif job, bukan elapsed proyek.
Histori mencakup run sebelum dan sesudah beberapa perbaikan platform.

| Bagian | Model calls | Total token kumulatif | Biaya USD | Active time |
| --- | ---: | ---: | ---: | ---: |
| Tiket #1 katalog | 38 | 820.643 | 0,104935 | 2,35 menit |
| Tiket #2 peminjaman | 353 | 16.358.829 | 1,164893 | 14,80 menit |
| Tiket #3 pencarian/ringkasan | 78 | 2.352.736 | 0,165814 | 7,46 menit |
| PO/setup/prefetch | 1 | 6.023 | 0,002730 | 1,60 menit |
| Total | 470 | 19.538.231 | 1,438371 | 26,21 menit |

Developer: 452 calls, 19.285.712 tokens, USD 1,369947 (95,24% biaya dan 98,71%
tokens seluruh demo). QA planning + execution/diagnosis: USD 0,035754 (2,49%).
Sekitar 99,19% token total merupakan input; sekitar 79,5% input tercatat cached.
Angka besar ini bukan seluruhnya kode yang dihasilkan atau context satu request.

Tiket #2 memuat dua overflow lama (total 10.175.211 tokens/USD 0,704006376),
recovery dengan generation lama yang masih membaca berulang, dan tiga review
gate yang alasan missing baseline test-nya dahulu terpotong. Karena itu total
demo ini tidak boleh menjadi estimasi biaya normal semua proyek berikutnya.
Perbandingan sesudah fix memerlukan proyek sebanding, bukan hanya repair kecil.

Jeda QA #2 lulus pada 13:03:38 UTC sampai plan #3 mulai 13:58:02 UTC sekitar
54 menit. #3 memerlukan accepted dependency #2. Jeda workflow/pengguna ini tidak
boleh diklaim sebagai latency model. Penelusuran log tidak menemukan pesan
provider HTTP 429 pada demo ini; rate limit bukan penjelasan yang didukung bukti.

## Bottleneck yang masih terlihat pada tiket #3

Developer `cf1eeb7f3a32469ab4929b6d9a71d474` menyelesaikan kandidat dalam
248,55 detik aktif, 69 calls, 2.228.967 tokens, USD 0,133634. Selang log
model.started → model.completed berjumlah 203,54 detik (81,9% waktu aktif),
median 2,154 detik/call dan maksimum 18,63 detik. Ini pengukuran relay termasuk
transport/stream, bukan pengukuran inference murni provider.

Arsip `e7a7b789b26643cd98649a5c041fbcf8` menunjukkan 82 hasil tool model:
50 read_file, 23 edit_file, 4 run_command, 3 inspect_diff, 2 submit_candidate.
Sebanyak 11 hasil berupa error: 4 edit kehilangan path, 4 old_text tidak cocok
tepat sekali, 1 continuation tanpa digest, 1 digest stale, 1 commit message
lebih dari 2000 karakter. Build juga sempat exit 127 karena belum install.
Tidak semua read berulang mubazir: ada paging dan source berubah; log read
tervalidasi tidak menunjukkan unchanged receipts pada job ini.

Proyeksi terakhir 244.620 → 169.618 karakter, source working set 38.182 karakter.
Cache token sudah bekerja. Peluang utama adalah mengurangi jumlah putaran model
dan error tool sambil membawa source relevan, bukan sekadar memperbesar konteks.

Developer sudah mencatat mismatch borrower selector dalam pesan submit; TL juga
menyebut QA-suite selector concerns. Handoff publik hanya mengatakan kandidat
disubmit. Peringatan terstruktur yang bisa dijadwalkan menjadi pekerjaan QA belum
ada, sehingga error yang diketahui tetap baru ditangani setelah browser gagal.

## Prioritas pembaruan

1. **Tool Developer lebih mudah dipakai.** Pakai handle hasil read yang mengikat
   path/digest, error edit berisi jumlah matches dan potongan konteks relevan,
   serta batch read/edit dengan validasi semua perubahan sebelum publikasi.
   Pertahankan CAS/fencing dan path isolation. Pisahkan commit message singkat
   dari handoff detail; schema min/max sudah ada tetapi model masih melanggarnya.
   Ukur tool errors dan calls/accepted candidate, bukan sekadar panjang prompt.
2. **Peringatan lintas agent menjadi pekerjaan.** Handoff/Review memuat concern
   bertipe test_contract dan evidence/source locator. QA memeriksa concern sebelum
   execution penuh, membuat suite/target baru bila koreksi terbukti. Developer
   tidak boleh mengedit acceptance suite atau menyatakan QA pass. Ukur known
   contract failures yang seharusnya bisa dihindari sebelum browser dijalankan.
3. **Snapshot baseline dan dependency yang bisa digunakan ulang.** base_build
   sekarang menjalankan install/build/test kembali pada QA planning dan setiap
   verifikasi. Cache baseline build berdasarkan SHA, manifest, image/toolchain,
   lock, env/config dan fixture identity; verifikasi digest sebelum reuse.
   Suite/runner berubah tetap menjalankan browser baseline dan kandidat ulang.
   Jangan menggunakan ulang approval atau menjadikan source snapshot writable.
4. **Persiapan install deterministik.** Supervisor mengetahui dependency sudah
   siap untuk digest lock/config saat ini; jalankan install sebelum build yang
   membutuhkannya. Cache tarball sudah tersedia, tetapi setiap npm ci masih
   mengekstrak/memasang dependencies. Pengoptimalan snapshot dependency harus
   mempertimbangkan image/platform, lifecycle scripts dan source tampering.
5. **Telemetry biaya/waktu yang mudah dibaca.** Tampilkan model wait, tools,
   install/build/browser, queue/dependency wait, UAT wait, dan failure recovery.
   Perlihatkan error/calls per attempt dan biaya produktif vs attempt gagal.
   Soft stall detection harus mengintervensi read/error loop tanpa reset usage
   atau membatalkan pekerjaan hanya karena fixed token budget habis.
6. **Concurrency setelah pengukuran.** Execution lane saat ini memang satu slot.
   Ketiga tiket demo bergantung berurutan pada accepted base, sehingga empat slot
   tidak otomatis mempercepat rantai tersebut. Pisahkan kapasitas planning/
   review/verification untuk tiket independen setelah shared workspace, lease,
   dependency-cache locking dan resource Docker diverifikasi. Approval tetap user.

Urutan yang disarankan: 1+2, kemudian 3+4, lalu 5+6. PO dan TL dalam demo ini
relatif murah/cepat; mengganti semua model atau menghapus QA bukan prioritas yang
didukung angka. Evaluasi model Developer dapat dilakukan belakangan pada scope
dan baseline sama dengan metrik kualitas, calls, waktu serta biaya per kandidat.
Tidak ada estimasi persen penghematan sebelum perubahan dan benchmark dilakukan.

## Bukti dan keterbatasan

- Source: `pipeline/{runtime,workspace,source_tools}.py`, `workspace/dependency_cache.py`,
  `workers/queue.py`, `pipeline/scheduler.py`.
- DB: Job usage/stage/task, Ticket phases, Release count, runtime Message log.
- Arsip conversation dan command evidence immutable di artifact store. Command
  duplikat lintas report didedup dengan run_id/generation/seq: 18 installs sekitar
  216,01 detik, 22 builds sekitar 26,07 detik, 21 tests sekitar 15,93 detik.
  Ini hanya commands di repo-gates/baseline-build artifacts, bukan semua tool atau
  browser time. Pada Developer #3 install dua kali menghabiskan 26,10 detik.
- QA #3 terakhir 2/2 pass, baseline 2/2 fail; tidak memakai repair cycle aplikasi.
  Rincian ada di audit qa-selector-recovery-2026-10-07.md.
- Tidak menjalankan provider/test baru untuk audit ini. Tidak membandingkan model
  atau mengklaim hasil berlaku untuk aplikasi POS yang jauh lebih besar.
