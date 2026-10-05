# Preview kandidat dan UAT — DEV-011

Status implementasi: DEV-011 DONE. Review independen: NOT_REVIEWED.
Sumber kebutuhan: ARCHITECTURE §10 (lifecycle preview, routing/autentikasi, identitas target), AC DEV-011.

## Bentuk

| Bagian | Berkas | Proses |
| --- | --- | --- |
| Permintaan/aturan (DB saja) | `app/preview/requests.py` | API (aman di Windows) |
| Lifecycle container/proxy | `app/preview/service.py`, `proxy.py` | worker (`--runtime pipeline`), POSIX + Docker |
| Server statis preview | `contracts/verification/preview-server.cjs` | di dalam container |
| State | tabel `previews` (migrasi 0005), event `preview.*` | DB |

API hanya menulis baris `previews` dan event (`POST /tickets/{id}/candidates/{candidate}/previews`,
`POST /previews/{id}/stop`, `GET /previews/{id}`; kandidat membawa `live_preview`, board membawa `preview` aktif).
Tidak ada Docker di handler HTTP. Supervisor (`PreviewService`, hook maintenance worker) mengerjakan baris
`requested`/`stopping`; ia berjalan di satu thread terpisah sehingga tidak menahan loop job. Kepemilikan resource
preview (label `aiagent.container-role=preview`, `aiagent.preview=<id>`) terpisah dari job execution: preview tidak
memakai slot execution, tidak membuat job, dan hanya disentuh oleh `PreviewService`.

## Lifecycle

`requested → starting → ready → stopping → stopped`, atau `failed`. Satu preview lokal aktif: permintaan baru
mengubah yang lama menjadi `stopping` (`switched`) dalam transaksi yang sama (yang belum dimulai langsung `stopped`);
supervisor menghentikan yang lama sebelum memulai yang baru. Meminta ulang kandidat yang sama saat masih
`requested/starting/ready` mengembalikan baris yang ada.

Syarat permintaan (diperiksa saat meminta **dan** lagi saat start): tiket di `uat`, kandidat adalah kandidat saat ini
berstatus `verified` untuk scope saat ini, base masih accepted tip, verifikasi yang membuka UAT lolos pemeriksaan
domain yang sama dengan UAT (passed, bukan fake, eksekusi/coverage lengkap, target sama), seluruh artefak
(commit, build record, target, bundle build, bukti) dapat dibaca ulang dan cocok checksum. Artefak hilang atau rusak
menghasilkan `409 artifact_unavailable`; tidak ada baris preview dan tidak ada container. Target yang manifestnya
mendeklarasikan migrasi selain `none` ditolak (lihat batas).

Start: bundle build disimpan sebagai artefak, di-unpack ke salinan baca-saja, dan digest tree-nya harus sama dengan
`build_digest` target. `index.html` wajib ada dan bytes yang disajikan lewat socket harus sama dengan berkas itu
(smoke health) sebelum status `ready`. Kegagalan di langkah mana pun (digest, image, health, port terpakai) menghapus
container, socket, proxy, dan salinan situs lalu menandai baris `failed` dengan alasan singkat.

Berhenti: proxy ditutup, container dihapus hanya bila label pemiliknya terbukti ID preview ini (error inspect bukan
bukti hilang; Docker mati tidak dianggap bersih), baris menjadi `stopped`. Cleanup yang tidak terbukti membiarkan
baris `stopping` dengan error dan dicoba lagi. Preview `ready` diperiksa tiap 3 detik: proses mati menjadi `failed`;
kandidat diganti (feedback, scope berubah, UAT diterima) atau base bergerak menutupnya (`superseded`). Saat worker
mulai, preview yang `starting/ready/stopping` dihentikan (`worker_restart`) karena proxy-nya ikut mati bersama
proses lama; pengguna membuka ulang dari artefak yang sama. Satu worker per host diasumsikan (owner `preview:<host>`).

## Isolasi

- **Target tanpa jaringan.** Container berjalan `--network none` (tanpa interface selain loopback, tanpa port
  terpublikasi, tanpa host gateway), root filesystem read-only, `--cap-drop ALL`, `no-new-privileges`, user 1000,
  memori 256 MB, 128 PID, tanpa env selain `PREVIEW_SOCKET`, tanpa secret/DB. Mount: situs (baca-saja), server
  preview (baca-saja), dan satu direktori socket.
- **Akses hanya lewat proxy supervisor.** Server di container mendengarkan unix socket; `UnixProxy` (loopback
  `127.0.0.1:PREVIEW_PORT`, maks 64 koneksi, idle 30 detik) memvalidasi HTTP sebelum membuka socket target:
  Host persis `localhost:<port>`, GET/HEAD tanpa body, tanpa Cookie/Authorization/Proxy-Authorization/X-CSRF-Token,
  dan Origin (bila ada) persis origin preview. Header Connection dinormalisasi menjadi close; satu request per
  koneksi mencegah request pipeline kedua melewati guard. Tidak ada jalur dari container
  ke kontrol (diuji: `/proc/net/dev` hanya `lo`, `wget` ke gateway/host/loopback gagal).
- **Origin dan cookie.** URL preview selalu `http://localhost:<PREVIEW_PORT>/` (default 5180, `PREVIEW_PORT`);
  kontrol ada di `127.0.0.1`. Cookie sesi host-only 127.0.0.1 tidak dikirim ke `localhost`. Browser test menangkap
  header asli (`Cookie`, `Authorization`, `X-CSRF-Token` tidak ada). Navigasi ke alias `127.0.0.1:19863`
  ditolak 403 oleh supervisor sebelum mencapai container, karena browser dapat mengirim cookie kontrol ke alias itu.
  Halaman preview (kode target) mencoba GET dan POST
  ke API dengan credential: keduanya diblokir, dan sisi server mencatat semua request ber-Origin preview 403 tanpa
  credential. Link GUI hanya dirender untuk hostname `localhost`, dibuka di tab terpisah (`noopener`); tanpa iframe.

## Identitas, pin, dan rebuild

Preview tidak membangun apa pun; ia menyajikan bundle tersimpan yang digest-nya dipin target. Membuka ulang
selalu artefak, config, dan fixture yang sama (`details` baris preview mencatat build/config/toolchain/fixture/migrasi,
image Node, dan bukti). Reset fixture (stateless) bukan target baru. Preview aktif memin target, build record, bundle,
dan bukti pada cleanup (`pins.py`); setelah berhenti pin kandidat/verifikasi/lampiran pesan tetap melindungi artefak
yang dirujuk approval, release, atau kandidat aktif. Approval UAT mengacu kandidat/scope, target digest, verifikasi,
dan ID bukti yang ditampilkan, tidak pernah ke baris preview (diuji: accept tetap sah sesudah preview berhenti).

Rebuild bukan operasi preview. Bila artefak hilang, preview `unavailable` dan jalurnya adalah feedback
(`request-changes`) yang menghasilkan kandidat dan build record baru dengan QA/UAT baru. SHA yang sama dengan
config/dependency efektif berbeda menghasilkan target digest berbeda (diuji: kandidat kedua dengan SHA sama dan
`config_digest` lain mendapat target dan preview sendiri; preview kandidat lama sudah ditutup `superseded` dan
permintaannya 409).

## Batas yang diketahui

- Hanya static React/Vite stateless dengan migrasi `none` (batas DEV-010). Uji migrasi dari kosong/upgrade data base
  tidak berlaku untuk stack itu dan target lain ditolak, bukan dipratinjau tanpa migrasinya. Dukungan database target
  perlu tiket baru.
- `preview-server.cjs` adalah salinan aturan penyajian `static-server.cjs` milik harness dengan listen unix socket
  (agar identitas runner DEV-010 tidak berubah). Keduanya harus dijaga sepadan.
- Proxy terikat `127.0.0.1`; `localhost` yang di-resolve ke `::1` jatuh kembali ke IPv4 di browser. HTTPS, host berbeda
  dengan prefix `__Host-`, dan akses VPS adalah DEV-017.
- Windows: worker/preview butuh WSL. Browser test di Windows mengakses fixture WSL dengan jeda forwarding localhost WSL2;
  test menunggu forwarding itu (bukan perilaku produk).
- Satu worker per host; dua worker di host sama dapat saling menghentikan preview saat recovery.
- Proses preview tidak diuji terhadap kegagalan Docker di tengah operasi selain jalur inspect/cleanup di atas.
