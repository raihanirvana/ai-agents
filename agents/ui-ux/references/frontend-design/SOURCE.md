# Sumber referensi frontend-design (dipin)

| Field | Nilai |
| --- | --- |
| Repo | https://github.com/anthropics/skills |
| Path | `skills/frontend-design/` |
| Commit | `dbd4588f9e1033efb41dad4bef2f7947c8993d44` (2026-10-09T18:45:49Z) |
| Diambil | 2026-10-12, sparse clone `--depth 1` |
| Lisensi | Apache License 2.0 (`LICENSE.txt` dari folder yang sama) |
| SHA-256 `SKILL.md` | `d91970639e9f5c37682ac7ab60094d35f1c7c1f38d731bd56396563aee10c1d3` |
| SHA-256 `LICENSE.txt` | `0d542e0c8804e39aa7f37eb00da5a762149dc682d7829451287e11b938e94594` |

File disalin tanpa perubahan. Tidak ada auto-update atau fetch saat runtime;
pembaruan hanya lewat commit yang direview, dengan commit/hash di atas diperbarui.

## Hasil review isi (DEV-018)

`SKILL.md` dibaca penuh sebelum dipin. Isinya panduan desain visual dan penulisan
copy. Tidak ada perintah tool, akses jaringan, permintaan secret, atau instruksi
yang mencoba mengubah perilaku platform.

Isi ini adalah **data referensi**, bukan instruksi yang mengikat platform. Bagian
yang tidak berlaku atau perlu disesuaikan di pipeline ini:

- "Confirm with the client" — UI/UX tidak memblokir pipeline untuk konfirmasi.
  Subjek dan audiens diambil dari scope/UAC yang sudah di-approve; pertanyaan
  material lewat `request_input` sesuai aturan role.
- "Information in your memory about the client's preferences" — role tidak punya
  memori lintas proyek; preferensi berasal dari scope, UiSpec warisan, dan feedback UAT.
- "Taking screenshots to review" — UI/UX tidak menjalankan browser; kritik visual
  dilakukan terhadap rencana token/layout, lalu dinilai pengguna saat UAT.
- "Start to write the code" — UI/UX tidak menulis kode. Hasilnya UiSpec; Developer
  yang mengimplementasikan.
- Contoh copy berbahasa Inggris ("Save changes"); prinsipnya (kata kerja aktif,
  sentence case, konsisten satu nama per aksi, error tanpa minta maaf) diterapkan
  ke copy Indonesia oleh instruksi role.
- Daftar ciri desain generik (warna krem/terakota, kit kartu SaaS, dsb.) dipakai
  sebagai pemeriksaan "jangan pakai default", bukan larangan mutlak; arahan
  eksplisit dari scope pengguna selalu menang.
