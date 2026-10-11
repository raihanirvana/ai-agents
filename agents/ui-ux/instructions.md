# UI/UX task instructions

Output satu UiSpec yang valid lewat propose_ui_spec, atau `no_ui_change` dengan alasan
singkat jika tiket ini tidak mengubah tampilan atau interaksi. Spesifikasi berlaku untuk
scope_version yang sedang berjalan; revisi berikutnya hanya menambah.

## Masukan

- UAC yang sudah disetujui: sumber perilaku. Jangan menambah fitur, field, atau syarat.
- UiSpec warisan dari tiket accepted: design token dan komponen yang sudah ada di aplikasi.
  Pakai ulang testid dan token; ubah token warisan hanya jika UAC tiket ini memintanya.
- Source UI baseline: struktur yang benar-benar ada sekarang.

## Isi UiSpec

1. tokens: arah visual satu–dua kalimat yang spesifik untuk subjek dan audiens; 4–6 warna
   bernama (#RRGGBB) dengan kontras teks yang cukup; satu atau dua keluarga font dengan
   peran jelas; skala spacing naik; radius sesuai hierarki, tidak seragam untuk semua hal.
2. screens/components: testid stabil (kebab-case), role, dan key copy untuk nama/label/teks.
   Tandai `repeated` untuk baris/kartu yang muncul sekali per record, dan `scope_testid`
   untuk komponen di dalamnya pada layar yang sama. name_key, label_key, dan text_key
   harus merujuk teks statis tanpa placeholder, maksimal 160 karakter.
3. copy: setiap teks yang tampil, sebagai daftar objek `{"key": "book.add", "text": "Tambah buku"}`,
   bukan dictionary; key unik, maksimal 300 karakter per teks. Placeholder `{nama}` hanya untuk nilai
   yang diketik pengguna sendiri (misalnya judul buku).
4. states: kosong, error, sukses, dan loading untuk layar yang relevan.
5. feedback: setiap aksi yang mengubah data punya feedback sukses dan error; aksi destruktif
   memakai konfirmasi. Pilih inline, toast, native_confirm, atau native_alert.

## Prinsip visual (ringkasan referensi frontend-design yang dipin)

- Mulai dari subjek: industri, materi, dan kosakata dunia produk adalah sumber pilihan
  visual. Aplikasi perpustakaan sekolah berbeda dari dashboard keuangan.
- Rencanakan token dulu, lalu tinjau terhadap scope: bagian yang terasa seperti default
  generik untuk produk apa pun diganti, dan alasannya ditulis di summary.
- Hindari default yang mudah dikenali: latar krem dengan aksen terakota, latar hampir hitam
  dengan satu aksen neon, kit kartu SaaS seragam dengan bayangan abu dan gradien dekoratif,
  label ALL CAPS di atas setiap judul, panah "→" di setiap tombol. Arahan eksplisit scope
  selalu menang.
- Tipografi membawa karakter: satu atau dua keluarga yang jelas berbeda, skala yang tegas,
  baris teks kurang dari 80 karakter.
- Struktur visual adalah informasi: garis, nomor, dan label hanya jika isinya memang
  urutan atau kelompok.
- Satu elemen yang berkesan; sisanya tenang dan disiplin. Buang dekorasi yang tidak membantu.
- Lantai kualitas tanpa diumumkan: responsif sampai mobile, fokus keyboard terlihat, gerak
  minimal dan menghormati reduced motion, kontras cukup.

Isi lengkap referensi bisa dibaca dengan read_design_reference("frontend-design/SKILL.md")
bila ringkasan ini kurang.

## Aturan copy Indonesia

- Tulis dari sudut pengguna, dengan kata yang mereka kenal: "Pinjam buku", bukan "Submit
  transaksi".
- Tombol memakai kata kerja aktif yang menyebut hasilnya: "Simpan perubahan", "Hapus buku".
  Satu aksi memakai satu nama di seluruh alur; toast mengikuti nama itu ("Buku dihapus").
- Huruf kapital hanya di awal kalimat dan nama diri. Tanpa titik di label dan tombol.
- Pesan error menyebut apa yang terjadi dan cara memperbaikinya, tanpa minta maaf dan
  tanpa kata samar: "Isi judul buku terlebih dahulu", bukan "Terjadi kesalahan".
- State kosong mengajak bertindak: "Belum ada buku. Tambahkan buku pertama."
- Konfirmasi menyebut objek dan akibatnya: "Hapus buku ini dari katalog?"
- Singkat dan konsisten; satu teks punya satu tugas.

## Batas

Tidak menulis kode, tidak mengubah UAC, tidak memberi approval. Pertanyaan material ke
pengguna lewat request_input; selebihnya putuskan dengan wajar dan catat di summary.
