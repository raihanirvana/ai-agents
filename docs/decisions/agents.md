# SOUL, context, model client, dan pesan antar-agent — DEV-007

## Source dan redaksi — 10 Oktober 2026

Source read/write/edit/diff, task pipeline berisi source, dan source_excerpt
memakai exact masking nilai credential terkonfigurasi. Pola generik assignment
token/password/secret tidak boleh mengubah sintaks aplikasi. Brief, scope,
pesan/jawaban, log dan error tetap disaring dengan pola generik sebelum konteks
dirangkai. ModelClient menerima source_safe dari trusted pipeline; agent tidak
dapat memilih policy redaksi melalui argumen tool. Jalur source_safe tidak
mengubah izin, lease, digest, budget atau otoritas approval. Artefak lama tidak
dimodifikasi. Detail dan batas verifikasi:
[audit redaksi/pipeline](../audits/source-redaction-and-scheduler-2026-10-10.md).

## Fallback OpenRouter yang dikonfigurasi — 7 Oktober 2026

Role dapat menetapkan `fallback_models` berupa daftar ID cadangan, urut prioritas.
Model `model` tetap utama pada setiap request. Contoh override lokal untuk Developer
dan Technical Lead:

```json
{
  "provider": "openrouter",
  "model": "deepseek/deepseek-v4.1-flash",
  "fallback_models": ["z-ai/glm-5.3-flash"],
  "allow_provider_fallbacks": true
}
```

Cadangan nonkosong secara default mengaktifkan `allow_provider_fallbacks`;
override boolean dapat membatasinya. ID kosong/duplikat/utama di daftar cadangan,
nilai bukan boolean, dan routing cadangan di luar OpenRouter ditolak. Contoh repo
memakai daftar kosong: tidak mengotorisasi model berbayar tambahan tanpa konfigurasi.

Structured client dan relay Hermes mengirim `models: [utama, ...cadangan]` sesuai
[kontrak resmi OpenRouter](https://openrouter.ai/docs/guides/routing/model-fallbacks).
Router mencoba utama dahulu, lalu cadangan ketika utama error, termasuk rate limit,
downtime, validasi konteks atau refusal. Ini failover request model; tidak memainkan
ulang tool, membuat job baru, mengubah approval, atau mereset usage. Runtime tidak
dapat menyisipkan daftar `models`/`fallbacks` sendiri: relay menggantinya dengan
konfigurasi supervisor, dan tetap memeriksa identitas model utama.

Satu HTTP request dirouting oleh OpenRouter dan ditagih pada model yang akhirnya
dipakai. Aplikasi mereservasi request tersebut sebelum forwarding dan mencatat
usage/cost respons; pemakaian yang hilang tetap unknown. Retry HTTP oleh aplikasi
tetap punya reservasi masing-masing. Model respons aktual tersimpan pada metadata
structured dan `jobs.runtime_ref.pipeline_model_responses` untuk Hermes, serta
log `model.response requested=... actual=...`; `pipeline_models` adalah konfigurasi
attempt beserta daftar cadangan, bukan klaim semua request memakai model utama.

Output parameter satu request harus didukung seluruh model yang diotorisasi.
Untuk `max_output_tokens: null`, maksimum berasal dari metadata OpenRouter semua
model tersebut, memakai maksimum bersama yang kompatibel; bukan angka cap aplikasi
baru. Timeout, fencing, reservasi, cancellation dan transport bounds tetap berlaku.

Jika seluruh rute gagal dengan 429, Hermes menyimpan detail yang sudah di-redact
dan memakai `Retry-After` (detik atau HTTP date; fallback 30 detik), lalu masuk
`waiting_quota`. Fallback tidak memperbaiki koneksi lokal yang putus, key salah,
atau saldo akun yang tidak dapat membiayai rute mana pun. Error setelah stream
dimulai tidak mengulang partial response. Router tidak mengekspos setiap percobaan
internal: model aktual membuktikan model yang menjawab, bukan penyebab failover.
Fallback tetap harus dikualifikasi pada run nyata; static checks bukan bukti
bahwa 429 pernah memicu GLM atau bahwa kualitas model setara.

Tanggal: 5 Oktober 2026. Implementasi: Claude (Sonnet 5.5); perbaikan review: Codex.
Status implementasi: DONE dengan fake/contract checks. Review awal Codex: **NEEDS_FIX**;
F1–F7 diperbaiki dan diverifikasi melalui self-check, menunggu re-review independen.
Temuan, perbaikan, dan bukti: `docs/reviews/DEV-007-review.md`. Checkpoint R5 masih terbuka.
Kode: `apps/backend/app/agents/`, berkas peran `agents/<role>/{SOUL,instructions}.md`,
`agents/models.example.json`. Bukti nyata percakapan PO dan handoff adalah kewajiban DEV-015.

## Komponen

Penyempurnaan prompt empat peran (2026-10-06):

- PO memilih jumlah tiket paling sedikit yang masih mudah diimplementasikan dan
  diterima sebagai hasil utuh. Aplikasi daftar belanja sederhana dengan add,
  check, delete, dan penyimpanan lokal menjadi satu tiket dengan beberapa UAC.
  Pemisahan pekerjaan besar tetap tersedia; alasan pemisahan memakai `summary`
  dan batas scope memakai `description`, tanpa field kontrak baru. Tiket yang
  sudah disetujui tidak digabung otomatis.
- Lead membuat rencana proporsional, menjawab pilihan teknis rutin, dan memberi
  blocker konkret beserta lokasi/koreksi. Saran gaya atau abstraksi opsional
  tidak menjadi alasan repair. Required gates dan waiver spesifik tetap berlaku.
- Developer membaca feedback repair terbaru, melanjutkan kandidat yang dipulihkan,
  menjaga scope tiket, serta memperbaiki penyebab error sebelum mengulang tools.
- QA memetakan seluruh UAC ke tes yang diperlukan, memilih selector sesuai
  perilaku, dan membedakan defect aplikasi, suite, serta runner. Koreksi suite
  tetap membutuhkan jalur resmi dan bukti baru; kontrol UI yang valid tidak
  dihapus agar memenuhi assertion teks seluruh baris yang keliru.

Kebijakan berada di delapan file `SOUL.md`/`instructions.md`. Loader membaca
definisi ketika runtime dibuat, dan digest tetap masuk context snapshot. Worker
yang sudah berjalan perlu dibuat ulang untuk memuat file baru; snapshot/job
historis tidak ditulis ulang. Perubahan prompt tidak mengurangi tahap pipeline,
mengubah izin, memindahkan approval, atau menjamin perilaku model/harga/durasi.
Evaluasi dengan provider nyata belum dijalankan untuk revisi ini.

Perbaikan Batch 3 (2026-10-06): rekonsiliasi reply menyaring pesan pending di SQL
dan memuat origin melalui join; tidak ada query per pesan historis yang sudah
punya reply. Eligibility tetap diperiksa lagi saat enqueue. Context history
memfilter proyek/tiket, runtime log dan kind/intent di SQL; ringkasan beserta digest
sumber dan batas konteks tetap berlaku. Retry-After invalid/nonfinite memakai
fallback 30 detik dan nilai yang diterima dibatasi 1–3600 detik. Perubahan belum
melalui tes regresi atau benchmark pada sesi ini; bukti aktual adalah sintaks,
kontrak API dan TypeScript, dicatat pada backlog Batch 3.

| Modul | Tanggung jawab |
| --- | --- |
| `souls.py` | Memuat empat `SOUL.md` + `instructions.md`, divalidasi (ada, tidak kosong, <= 16 KiB, UTF-8, tanpa pola secret) dan diberi digest konten yang dicatat di setiap snapshot. SOUL hanya mengatur identitas/perilaku; ia tidak memberi izin. |
| `tools.py` | Matriks tool per peran (ARCHITECTURE §6) dan `ToolFacade`. Otorisasi dari identitas run yang diverifikasi queue; argumen yang membawa identitas ditolak. Tidak ada tool approval atau status setter. |
| `models.py` | Konfigurasi provider/model per role, `FakeProvider` berlabel, adapter chat-completions (OpenRouter/DeepSeek), normalisasi usage/cost, klasifikasi error, redaction. |
| `outputs.py` | Kontrak output terstruktur (pydantic, `extra=forbid`, batas ukuran): proposal tiket, revisi, klarifikasi, rencana teknis, jawaban lead. |
| `context.py` | Context builder berlapis, batas token, snapshot + hash per run, selalu dibangun dari database. |
| `threads.py` | Pesan terarah, input request antar-peran, keputusan, ringkasan; reply job; rekonsiliasi. |
| `effects.py` | Retry pesan mempertahankan provenance penulisan pertama, dengan payload semantik tetap diperiksa persis. |
| `runtime.py` | `StructuredAgentRuntime` untuk PO/lead di atas Supervisor DEV-004 (`structured` / `structured:fake`). |
| `wiring.py` | Merakit runtime dari konfigurasi (dipakai `python -m app.worker --runtime structured`). |

## Aturan yang ditegakkan

- **Otorisasi dari identitas run.** `queue.verify(lease)` mengembalikan project/ticket/scope/job/generation/role
  hanya bila lease masih milik attempt itu. Penulisan efek memeriksa identitas lagi dalam transaksi yang sama;
  pemeriksaan responder dan penyimpanan jawaban/resume atomik. Nama role tanpa capability run ditolak.
  Tool tidak membaca identitas dari argumen. Setiap panggilan tool
  direservasi terhadap budget **sebelum** otorisasi, sehingga model yang terus memanggil tool terlarang tetap
  berhenti di cap. Tool yang masuk kebijakan tetapi baru ada di DEV-010 (workspace, QA harness) gagal eksplisit
  `NotWired`, bukan diam-diam.
- **PO/lead hanya mengusulkan.** PO membuat tiket `scope_review` tanpa approval (`create_ticket`, kini dengan
  `idempotency_key`) atau proposal revisi (`propose_scope`, idempotent). Lead menyimpan rencana teknis sebagai
  pesan `authoritative=false` dan keputusan sebagai `decision_proposal`; hanya yang di-accept pengguna
  (`Threads.decide`) masuk konteks sebagai keputusan. Dependency plan lead hanya dicatat, tidak diterapkan.
- **Output tervalidasi.** Jawaban model di-parse ke kontrak; graf dependency antar-tiket dicek acyclic dan
  referensi tiket yang ada dicek **sebelum** tiket apa pun dibuat. Output tidak valid mendapat **satu** panggilan
  perbaikan yang menampilkan galat validasi; bila masih tidak valid, job gagal terlihat (`needs_human`,
  kedua panggilan tetap terhitung) dan tidak ada efek. Kebutuhan klarifikasi adalah output sah yang menjadi
  input request ke pengguna (`waiting_input`), bukan tebakan.
- **Efek stabil terhadap retry.** Job akar ditelusuri melalui `parent_job_id`, tanpa memotong key pengguna.
  Output tervalidasi, metadata usage, dan snapshot asal disimpan sebelum efek diterapkan. Retry membaca
  checkpoint persisten untuk task/jawaban yang sama; tidak memanggil model lagi atau mengganti proposal.
  Ticket, pesan hasil, keputusan, dan reply memakai kunci stabil; provenance efek pertama dipertahankan.
  Payload berbeda dengan key yang sama tetap ditolak. Jawaban pengguna tetap tersedia pada retry setelah
  resume. Model yang selesai setelah pencabutan ditolak oleh fence transaksi tulis.
- **Konteks (ARCHITECTURE §8).** Urutan: instruksi peran, scope, brief + keputusan **accepted**, dependency pin,
  referensi repo (potongan, bukan seluruh repo), lalu ekor volatil: pesan terbaru, ringkasan, task, identitas
  run. Prefix stabil di depan (hash prefix dicatat). Technical lead/developer/QA menolak mulai pada scope yang
  belum disetujui pengguna; PO melihat draft berlabel "NOT approved". Batas token adalah **estimasi**
  (karakter/4): lapisan dipangkas dengan prioritas pesan terlama, referensi repo, keputusan terlama; peran,
  scope, dependency, dan task tidak pernah dibuang (terlalu besar -> `ContextTooLarge`). Yang terpotong tanpa
  ringkasan valid muncul sebagai `gaps` di manifest. Pesan panjang dipotong per pesan. Urutan pesan mengikuti
  `created_at` lalu `seq` thread (timestamp kembar tidak mengacak percakapan).
- **Ringkasan tidak menghapus histori.** `record_summary` menyimpan ringkasan sebagai pesan baru yang merujuk ID
  pesan yang dicakup + digest sumbernya; ringkasan hanya dipakai bila digest masih cocok, selain itu dilaporkan
  sebagai gap. Transkrip runtime tidak diduplikasi: manifest menandai `runtime_transcript` sebagai gap eksplisit.
- **Snapshot per run.** JSON kanonik (`kind=context`, meta producer/job/generation/sha256) disimpan sebagai
  artifact, dirujuk `jobs.context_artifact_id`, dipin selama job aktif, dan dilampirkan ke pesan hasil
  (sehingga tetap terpin). Hash tercatat di hasil job. Replay proposal memakai snapshot asal, bukan
  mengklaim konteks baru dilihat model; referensinya diwariskan ke job retry dan dipin juga selama cleanup.
- **Pesan terarah dan input request.** Pesan disimpan sebelum ada yang dijadwalkan. Hanya clarification/
  handoff/bug terarah dengan `needs_reply` yang membuat **satu** reply job (kunci `reply:<message_id>`);
  note, log, dan broadcast tidak pernah memicu soul, reply tidak bisa memicu pekerjaan lagi, dan peran tidak
  bisa bertanya ke dirinya sendiri (tidak ada loop). Developer yang bertanya ke lead memakai input request
  beralamat peran (`recipient=role:technical-lead`): job menunggu (slot execution dilepas), lead menjawab
  di lane interaktif, dan reply job memakai budget scope yang sama dengan penanya. Jawaban hanya boleh dari
  peran yang dituju dengan lease aktif atau pengguna. Jawaban selalu menjadi histori; attempt hanya di-resume bila masih valid
  (duplikat no-op, setelah cancel/revisi scope tidak menghidupkan attempt). Reply job melewati panggilan model
  bila penanyanya sudah tidak menunggu. `Threads.input_request` menampilkan ID, scope, penerima,
  attempt/generation, status (`open/answered/stale_scope/cancelled/orphaned`), dan jawaban. Rekonsiliator
  (`ensure_reply_jobs`, hook `Supervisor.maintenance`) membuat reply job yang hilang karena crash di antara
  dua transaksi, untuk input request maupun clarification/handoff/bug biasa; lane dan scope asal dijaga.
  Origin yang dicancel atau scope-nya berubah tidak dijadwalkan. `needs_user` membuat permintaan pengguna
  yang persisten dan event `input.escalated`, bukan jawaban lead yang membuka resume. Developer tetap
  menunggu pada request baru; hanya jawaban pengguna valid yang membukanya. Pesan nonblocking juga bisa
  menghasilkan pertanyaan pengguna yang terlihat, tanpa me-resume job asal.
- **Model per role dan akuntansi.** `ModelRegistry` (default + override per role; timeout 0-600 s dan
  `max_output_tokens` finite wajib). Setiap panggilan lewat `RunContext.model_call`: direservasi sebelum
  dipanggil, dibatasi limiter dan cap token job, lalu usage difinalisasi. Usage/cost yang tidak dilaporkan
  provider adalah **unknown**, bukan nol; lower bound input/output yang diketahui tetap menegakkan cap total.
  `prompt_tokens` dinormalisasi ke alias input queue tanpa menggandakan total. Panggilan yang gagal tetap terhitung. Timeout dan provider
  tidak tersedia = retryable (satu retry), ditolak = tidak, quota (HTTP 429) = `waiting_quota` bersama.
- **Redaction.** Nilai key dari environment dan pola umum (sk-, Bearer, key=value, token VCS, private key)
  disamarkan pada prompt, output, error, seluruh manifest snapshot, argumen/hasil tool, metadata/checkpoint,
  pesan/proposal, dan identitas provider/model hasil sebelum disimpan atau dikirim.
- **Label fake.** `structured:fake` (atau `fake`) berarti fake di event/log/hasil job; provider fake di bawah
  label nyata, atau sebaliknya, ditolak. Ketiadaan API key tidak memblokir worker: job role terkait gagal
  dengan pesan jelas.

## Perubahan kecil pada kode tiket lain (kompatibel)

DEV-004: `is_fake_runtime` (label `:fake`), `recipient` pada `request_input`, `JobQueue.verify`/`set_context`,
`Supervisor.maintenance`, kolom `idempotency_key` pada `verify`. DEV-003: `idempotency_key` opsional pada
`create_ticket` dan `propose_scope`. Test suite kedua tiket tetap lulus.

## Konfigurasi dan cara menjalankan

Role model menerima opsi boolean `stream` (default false); contoh mengaktifkan
stream untuk technical lead. Adapter SSE menangani comments, multi-line data,
usage chunk kosong/terminal berulang, `[DONE]`, dan error di tengah stream.
Progress log hanya memuat counts chunk/answer/reasoning, tanpa raw reasoning atau
potongan teks yang berpotensi membelah secret. Jawaban dikumpulkan, diperiksa
terminal/finish reason, lalu divalidasi kontrak JSON sebelum efek domain.
Byte bounds, deadline wall-clock socket, cancellation check dan accounting per
request tetap berlaku. `length`, `tool_calls`, content filter, terminal hilang,
dan jawaban kosong/non-text tidak menjadi verdict. Content parts berupa teks
dinormalisasi; usage respons gagal dicatat jika tersedia, sisanya unknown.
Dokumentasi integrasi:
[OpenRouter streaming](https://openrouter.ai/docs/api_reference/streaming) dan
[reasoning tokens](https://openrouter.ai/docs/guides/best-practices/reasoning-tokens).
Revisi ini tidak mengubah jalur relay Hermes yang terpisah.

`reasoning_effort` opsional dikirim sebagai `reasoning.effort` hanya untuk
OpenRouter; provider lain ditolak eksplisit bila opsi ini diminta. Pilih effort
sesuai metadata model resmi, bukan mengasumsikan semua model mendukungnya.
Diagnosis demo TL: stream dengan output cap 4096 berakhir `length`, answer_chars
0, reasoning hadir. Metadata `GET /api/v1/models` untuk DeepSeek v4.1 Flash
menunjukkan default effort high dan dukungan max/high/low. Konfigurasi lokal TL
awalnya diubah menjadi stream=true, cap 8192, effort low. Review kandidat yang sama
berhasil dengan finish stop, output 6012 tokens, sekitar 24 detik; bukan bukti
streaming saja menghilangkan limit atau bahwa low akan sama cepat untuk semua tugas.

Untuk demo lokal, seluruh role sekarang memakai `max_output_tokens: null`:
cap output aplikasi dihapus. Structured client dan Hermes mengambil
`top_provider.max_completion_tokens` dari metadata resmi OpenRouter `/models`
(cache 5 menit), lalu meminta maksimum provider tersebut. Tidak memakai default
Hermes/provider yang mungkin lebih kecil. Batas budget job yang finite tetap
berlaku pada proyek lain. Metadata tidak tersedia menghasilkan error eksplisit,
bukan fallback ke cap kecil. Provider lain belum mendukung opsi null; gunakan
nilai eksplisit sampai tersedia adapter metadata. Timeout, lease, batas transport,
accounting, dan approval tetap berlaku. Konfigurasi contoh mempertahankan default
finite untuk penggunaan umum; override demo lokal tidak di-commit.

```sh
cd apps/backend
./.venv/bin/python -m pytest tests/agents -q
# Model per role: salin agents/models.example.json ke agents/models.json (gitignored) atau set AGENT_MODELS_FILE.
# Key hanya dari environment variable yang disebut berkas itu, mis. OPENROUTER_API_KEY.
./.venv/bin/python -m app.worker --runtime structured   # PO/lead lewat konfigurasi itu (BELUM TERVERIFIKASI nyata)
```

Catatan: `app.config` memuat `.env.local`, sehingga key di berkas itu ikut terbaca worker.

## Batas dan known issues

- Hanya fake/contract checks; adapter chat-completions diuji terhadap server stub lokal dan **belum
  diverifikasi** terhadap provider nyata. Kualitas PO/lead dengan model nyata dibuktikan DEV-010/015.
- Developer dan QA berjalan lewat Hermes (DEV-010); di sini hanya kebijakan tool, SOUL/instruksi, dan jalur
  tanya-jawab mereka yang ada. Tool workspace/harness belum di-wire (`NotWired`).
- Keputusan accepted disimpan sebagai pesan. Menuliskannya ke `docs/decisions` di clone managed sebagai
  kandidat yang direview (ARCHITECTURE §7) menunggu integrasi (DEV-010/012).
- Belum ada peringkas otomatis: `record_summary` menerima ringkasan dari pemanggil tepercaya. Estimasi token
  kasar (karakter/4) dan tidak sama dengan tokenizer provider.
- Satu panggilan perbaikan per jawaban tidak valid; PO tidak bisa memanggil tool bebas di dalam run
  terstruktur (tool facade tersedia untuk runtime yang akan memakainya, termasuk Hermes pada DEV-010).
- Reply job berbagi budget scope dengan penanya: budget kecil bisa habis oleh percakapan; perpanjangan
  tetap keputusan pengguna (DEV-004).

### Konteks runtime setelah audit demo

Developer tetap menerima scope, feedback dan identitas run lengkap. Pengurangan
histori dilakukan deterministik pada relay produk (`pipeline/transcript.py`),
bukan melalui summary LLM berbayar atau compression Hermes. Log projection
memuat ukuran/digest sebelum-sesudah; transcript asli tetap menjadi bukti lokal.
Konteks review memakai dependency/gate summary dengan diff lengkap dipin.
Instruksi developer menjelaskan write_file/edit_file, digest dan paging.
Rincian kontrak, cache dan keterbatasan verifikasi ada pada keputusan pipeline.
Penghematan token/waktu aktual belum diukur pada demo baru.
# Perbaikan efisiensi setelah dua demo — 7 Oktober 2026

- Konteks developer mempertahankan user/system/decisions/feedback/error dan enam
  messages terbaru. Completed read di luar working set 64.000 karakter dan
  argument mutasi lama diganti digest/range. Transcript asli tetap diarsipkan;
  tool read dapat mengambil source terbaru. Ini batas observasi lama, bukan
  pembatas token provider. Penghematan aktual belum diukur pada demo ulang.
- Relay Hermes mencoba HTTP 408/502/503/504 dan transport transient hanya
  sebelum header respons diterima. Structured provider mencoba 502/503/504.
  Backoff 5/15/30 detik (relay menambah jitter), maksimal tiga retries request;
  quota 429 tetap memakai waiting_quota, error auth/config tidak dicoba berulang.
  Setiap request punya reservation terpisah, usage gagal tetap unknown dan
  lease/cancel diperiksa saat menunggu. Partial stream tidak diputar ulang.
- Setelah mutasi source, supervisor menyimpan snapshot untuk resume tanpa
  mengubah accepted/attempt Git ref. Checkpoint bukan kandidat atau bukti QA.
  Latest checkpoint job resumable dipin; snapshot lama dapat dibersihkan jika
  tidak dipin oleh referensi lain. Log menyimpan ID/checksum, tanpa membanjiri chat.


### Perbaikan relay dan read receipts — 7 Oktober 2026

Ingress chat relay produk dengan projection menerima paling banyak 8 MiB
request mentah; request provider sesudah projection tetap maksimal 1 MiB.
Tools/nonprojected relay tetap memakai ingress 1 MiB. Ini batas payload/memori,
bukan pembatas budget token demo. Canary diperiksa pada request asli sebelum
projection; bearer, generation, admission dan accounting tetap berlaku.
Log context.size mencatat bytes kedua sisi, bukan hanya jumlah karakter.
Penolakan lokal memakai error relay_request_limit dan failure_kind relay_context,
terpisah dari kegagalan provider; tidak diteruskan sebagai generic HTTP 413 yang
Hermes salah tafsirkan sebagai context overflow. Compression upstream tetap off.

SourceTools selalu memvalidasi file/digest melalui supervisor. Read halaman yang
sama pada digest yang sama menghasilkan unchanged_read receipt tanpa salinan
content kedua. Refresh eksplisit boleh mengambil content kembali bila halaman
lama tidak ada dalam konteks aktif. Cache hanya menyimpan metadata maksimal
128 page identities per instance/attempt; digest berubah berarti halaman baru.
Projection mempertahankan read yang berisi source dan tidak menggantinya dengan
receipt. Pasangan read receipt completed yang sudah lama dihapus dari request
projected, sementara transcript asli tetap diarsipkan. Pending/recent calls,
mutasi, keputusan, scope, feedback, dan checks tetap dipertahankan.
Working set dinaikkan menjadi 64.000 karakter agar source+tests yang relevan
bisa dibaca bersama, mengurangi pergantian halaman yang mengusir source lain.

Feedback repository gate memprioritaskan missing baseline IDs, failed IDs dan
counts sebelum detail command. Developer repair juga membaca ringkasan dari
artefak gate target kandidat feedback yang dipin (project/ticket/scope harus
sesuai), sehingga feedback historis yang terpotong masih bisa dipulihkan.
Nama/ID tes baseline harus tetap; tambahkan tes feature secara terpisah.
Hasil run_command test tidak menggantikan coverage baseline pada submit gate.
