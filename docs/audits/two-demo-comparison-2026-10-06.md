# Perbandingan dua demo — 6 Oktober 2026

Status: audit observasional selesai. Tidak mengubah runtime, model, job atau
approval; tidak menjalankan demo atau tes baru. Data lokal dibaca setelah kedua
proyek selesai. Usulan di bawah belum diimplementasikan dalam audit ini.

## Sumber dan batas pengukuran

- Demo catatan: project `25f7b5e9019646e0b13908fa59fa8b59`, satu tiket.
- Demo stok: project `95880ebdfef54195b38deaab5aaa2df1`, dua tiket berdependency.
- Jumlah berasal dari `Job.usage` per job, bukan DTO usage kumulatif pool.
  `prompt_tokens` alias input tidak dijumlahkan kembali; retries tetap masuk.
- Waktu aktif adalah jumlah active_s job, termasuk tool dan model. Bukan waktu
  kalender pengguna: antrean, menunggu UAT, downtime dan intervensi tidak tercakup.
  Interval log beresolusi detik memberi perkiraan, bukan profiler.
- Beberapa panggilan provider gagal mempunyai usage unknown. Token dan biaya
  merupakan jumlah tercatat/minimum, bukan rekonsiliasi tagihan OpenRouter.
- Workload berbeda, konfigurasi berubah, dan cache mula-mula dingin. Perbedaan
  kedua demo tidak membuktikan persentase penghematan kausal dari optimasi.

## Angka aktual

| Ukuran | Catatan, 1 tiket | Stok, 2 tiket |
|---|---:|---:|
| Model calls | 55 | 71 |
| Input tokens | 1.028.701 | 1.044.145 |
| Output tokens | 35.832 | 30.188 |
| Total tokens tercatat | 1.064.533 | 1.074.333 |
| Biaya tercatat USD | 0,091685426 | 0,089747692 |
| Jumlah waktu aktif | 1.529,65 s / 25,49 menit | 1.001,65 s / 16,69 menit |
| Developer calls | 46 | 61 |
| Developer tokens | 943.358 | 977.575 |
| Developer aktif | 936,29 s | 660,44 s |

Demo stok: developer menghabiskan sekitar 91% token; input sekitar 97,2% total.
Cached tokens tercatat 746.745 (740.835 developer); audit lama belum merekam
cached tokens Hermes secara konsisten, sehingga tidak sah membandingkan nol
cache lama sebagai nol cache provider. Cache mengurangi biaya, tetapi total
token yang dikirim dan banyaknya putaran tetap tinggi.

### Tahap demo stok

| Tahap | Calls | Tokens | Aktif (s) |
|---|---:|---:|---:|
| PO | 1 | 4.350 | 5,80 |
| #1 TL plan | 1 | 4.038 | 4,86 |
| #1 QA plan | 1 | 6.692 | 13,85 |
| #1 developer | 12 | 141.190 | 342,64 |
| #1 TL review | 1 | 27.337 | 14,66 |
| #1 QA execution | 0 | 0 | 9,58 |
| #2 TL plan | 1 | 4.376 | 30,48 |
| #2 QA plan | 3 | 25.580 | 45,96 |
| #2 developer, budget stop | 12 | 173.553 | 47,68 |
| #2 developer, continuation | 18 | 360.738 | 114,72 |
| #2 TL review pertama | 1 | 11.224 | 14,42 |
| #2 QA execution pertama | 0 | 0 | 50,94 |
| #2 developer, provider 504 | 2 | 9.751 | 25,35 |
| #2 developer, retry 504 | 2 | 9.723 | 25,50 |
| #2 developer, repair berhasil | 15 | 282.620 | 104,54 |
| #2 TL review kedua | 1 | 13.161 | 58,97 |
| #2 QA execution kedua | 0 | 0 | 51,67 |
| Reference setup + release | 0 | 0 | 40,03 |

## Temuan yang paling berdampak

### 1. Salah selector QA memicu seluruh siklus repair

QA pertama #2 lulus 3/4. UAC-3 gagal karena `.error-message` cocok dengan dua
elemen, satu hidden dan satu berisi `Stok tidak mencukupi.`. Ini bukti locator
ambigu; tidak membuktikan seluruh UAC-3 benar atau ada bug domain stok.

Feedback otomatis dikirim sebagai repair_feedback ke developer. Diff kandidat
`40f27bd0...` ke `3a9a3f44...` hanya menghapus class `error-message` dari
`#item-form-error` pada index.html. Suite tetap sama, lalu QA lulus.
Jadi aplikasi diubah untuk membuat selector generik unik; tes tidak diperbaiki.

Job setelah QA pertama: tiga developer attempts + TL review + QA ulang memakai
315.255 token, 20 model calls, 37 tool calls, 266,03 s aktif, USD 0,010797947.
Ini biaya aktual seluruh rangkaian, termasuk dua gangguan provider; bukan biaya
intrinsik mengubah satu class atau jaminan semuanya dapat dihilangkan.

Usulan prioritas pertama: klasifikasikan failure sebagai application,
test-contract, infrastructure atau unknown. Diagnosis menggunakan locator
contract, DOM/error, serta bukti perubahan. Strict mode violation perlu triage,
bukan otomatis dianggap semua bug tes. QA memperbaiki suite yang ambigu;
perubahan suite menghasilkan digest baru, baseline execution dan candidate
execution baru, coverage UAC tetap wajib. Jangan mengubah failed menjadi pass
atau melewati approval. Error tes/infra tidak memakai repair-cycle kode.

### 2. Projection masih mempertahankan banyak isi file

Pada continuation #2, call akhir berkurang dari 104.736 menjadi 94.718 karakter
(sekitar 9,6%). Pada repair satu class, call akhir 89.097 menjadi 83.422
(sekitar 6,4%). Sebelum budget stop: 70.310 menjadi 58.818 (sekitar 16,3%).
Angka adalah karakter messages pada call tertentu, bukan seluruh payload/tool
schemas, token provider atau penghematan seluruh demo.

TranscriptProjection mempertahankan latest read setiap (path, offset) dan
latest write per path; jika file dibaca dan tidak berubah, banyak halaman penuh
tetap dikirim pada semua call selanjutnya. Sesudah repair satu class ada banyak
reads, install/test/build dan immutable submission, bukan satu edit saja.

Usulan: konteks kerja berbasis file aktif/hunk yang relevan; setelah isi digunakan,
observasi lama diringkas ke digest + range + hasil checks. Agen tetap dapat
membaca ulang melalui tool, transcript asli tetap diarsipkan. Pertahankan scope,
keputusan, repair feedback, error aktif dan precondition edit. Ukur ukuran
payload asli/proyeksi/token per call serta cache; jangan menghapus data yang
dibutuhkan untuk membuat perubahan benar.

### 3. Unduhan pertama mendominasi tiket #1, cache berikutnya berhasil

Tool install #1 berlangsung sekitar 241 s (15:14:32–15:18:33), dibanding
342,64 s aktif developer. Dependency complete mencatat 115 paket, cache_hits 0,
134.174.221 byte unduhan. Ini termasuk akuisisi, install dan overhead.

Install submit, QA plan #2, continuation, repair, QA baseline dan release
mencatat 115 hits/115 paket, 0 byte unduhan. Cache tarball yang ditambahkan sudah
dipakai; bukan setiap tahap mengunduh ulang dari internet. Namun npm ci dan
container/snapshot tetap memakan waktu walaupun tarball cache hangat.

Usulan: prefetch reference tarball terverifikasi untuk proyek baru saat PO/scope
berjalan; pengunduhan hanya oleh supervisor. Pertimbangkan cache dependency
environment berdasarkan lock, image/toolchain, install command, environment
dan platform. Hasil immutable harus diverifikasi, tidak menerima node_modules
mutable milik target sebagai bukti, dan QA tetap mengeksekusi target yang dipin.

### 4. Provider error harus pulih di titik permintaan

Dua attempt setelah QA gagal mendapat provider_error 504, masing-masing setelah
satu call sukses dan beberapa tools. Worker mencoba otomatis sekali, kemudian
menunggu operator. Error 504 berasal dari jalur provider; log tidak memisahkan
gateway OpenRouter dari upstream. Total dua jobs aktif 50,85 s dan 19.474
token tercatat; kebutuhan keputusan operator menambah jeda kalender.

Usulan: typed transient failure, retry dengan backoff/jitter pada request model
sebelum response streaming dikirim, tetap memeriksa lease/cancellation.
Setiap upaya provider dihitung dan usage unknown tetap dicatat. Jangan mengulang
mutasi tool, menyambung partial stream sebagai jawaban valid, atau mengganti
model diam-diam. Setelah gangguan berkepanjangan gunakan waiting_provider dan
jeda circuit breaker yang terlihat; unlimited budget tidak berarti tight loop.
Checkpoint mutasi source yang durable membatasi kerja yang hilang jika job
memang harus direstart.

### 5. Izin unlimited demo belum menjadi policy proyek baru

#2 memakai pool default 200.000 tokens termasuk technical_plan dan qa_plan.
Ia berhenti walaupun sebelumnya pengguna meminta unlimited untuk semua demo.
Source dipulihkan dari archive terverifikasi dan retry membawa histori/usage.

Usulan: konfigurasi demo eksplisit persisten, diwariskan saat proyek baru dan
pool stage dibuat. Production default finite tetap ada; batas per-call provider,
transport, timeout command, ukuran file dan isolasi adalah konsep terpisah dari
budget kumulatif. UI menunjukkan policy efektif dan unknown usage.

## Prioritas rekomendasi

1. QA failure triage + jalur repair suite oleh QA, tanpa mengirim setiap failed
   browser assertion ke developer.
2. Request retry provider yang aman dan checkpoint durable.
3. Policy demo persisten agar budget tak berhenti secara tak sengaja.
4. Projection berdasarkan konteks kerja relevan, metric per call.
5. Prefetch reference dependency dan evaluasi cache environment immutable.
6. Setelah stabil, uji workflow lebih pendek untuk scope kecil: rencana TL/QA
   ringkas dan tools deterministik untuk checks. Review/QA dapat dijalankan
   paralel setelah candidate/target/suite immutable, bila aturan transition
   tetap menunggu keduanya. Data ini belum menunjukkan planning role sebagai
   bottleneck utama; jangan menghapus QA atau approval hanya untuk mengurangi
   banyaknya tahap.

Kriteria evaluasi selanjutnya: ulangi workload identik dari base kosong dan
cache state yang dinyatakan; bandingkan waktu aktif, waktu tunggu per penyebab,
input/output/cache tokens, jumlah provider attempts, repair cycles kode versus
suite, durasi install dan waktu candidate-to-UAT. Lulus tetap berdasarkan runner
independen. Audit ini belum menjalankan eksperimen tersebut.
