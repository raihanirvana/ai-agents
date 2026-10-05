# Review DEV-016 (reviewer: Claude) — 2026-10-05

Baseline `c94b027` + perubahan DEV-016 di working tree (belum commit). Hasil: **tidak ada AC yang gagal**; satu
perbaikan efisiensi kecil (R016-A); sisanya observasi. R9 tidak ditutup oleh review ini karena tidak ada walkthrough
dengan provider nyata maupun uji GPU/perangkat nyata.

## Yang sudah baik

- Kantor murni proyeksi: `projectOffice()` memakai `Board.runs` + `messages` dari `WorkspaceProvider` yang sama dengan
  Aktivitas/Chat. Tidak ada status setter, scheduler, atau data simulasi; run FAKE diberi label.
- Asumsi urutan terverifikasi di backend: `_runs` mengurut `Job.created_at` naik, sehingga "run aktif terbaru, kalau
  tidak ada run terakhir" benar; format sender `agent:<role>` / recipient `role:<role>` cocok dengan backend; stage
  `technical_review`/`technical_plan` dipetakan benar.
- Reconnect memakai mekanisme existing (snapshot + refetch); indikator koneksi tampil. Run lama gagal tidak membuat
  role idle tampak terhambat.
- Fallback WebGL (context null → error boundary) teruji; tombol HTML role memberi jalur keyboard/screen reader; toggle
  animasi tersimpan lokal dan menghormati `prefers-reduced-motion`.
- Probe reviewer: membuka/menutup tab Kantor 25 kali tidak menghasilkan context leak atau error console (hanya error
  SSE yang sengaja di-abort fixture). Tes avatar WebGL benar-benar berjalan (tidak di-skip). `npm audit --omit=dev`: 0.
- Three.js hanya dimuat saat tab dibuka (chunk lazy 891 KB / 239 KB gzip; bundle awal 214 KB).

## Perbaikan reviewer

**R016-A (P3, efisiensi):** `frameloop="always"` membuat scene dirender 60 fps terus-menerus walaupun semua role idle
(kepala hanya beranimasi untuk role non-idle), memakai GPU pada dashboard yang dibiarkan terbuka. Kini `always` hanya bila
animasi aktif dan ada role non-idle; selain itu `demand`. `AnimatedHead` mereset posisi kepala dan meng-invalidate sekali
saat animasi berhenti (tanpa itu kepala membeku di tengah gerakan pada mode demand). Tes browser 39 passed.

## Observasi (tidak diubah)

1. `selectTicket` memindahkan panel ke tab Tiket (efek di Workspace), jadi klik role yang punya tiket meninggalkan Kantor;
   bila tiket itu sudah terpilih, tab tidak berpindah. Konsisten dengan "buka konteks terkait", tetapi tidak seragam.
2. `/messages` mengembalikan 100 pesan terbaru proyek; pesan terakhir sebuah role yang lebih lama bisa tidak terlihat
   ("Belum ada aktivitas"). Board mengirim semua run tanpa batas (pre-existing).
3. Status disederhanakan: run `queued` tampil seperti sedang bekerja; role di luar empat peran (release/export/onboarding)
   tidak tampil.
4. Tidak ada handler `webglcontextlost`; kehilangan context saat runtime tidak jatuh ke fallback.
5. Dependensi baru memakai `^` (dependensi lain dipin) dan `@types/three` ada di dependencies; chunk >500 KB memicu warning.
6. Handoff menyebut README diubah, padahal README tidak berubah (ditambahkan catatan singkat oleh review ini).
7. Walkthrough nyata hanya dengan FakeProvider; tidak ada uji visual lintas GPU/mobile; bukan bukti provider nyata.

## Verifikasi reviewer

| Pemeriksaan | Hasil |
| --- | --- |
| `npm run build` (TypeScript + Vite) | lulus (warning chunk besar) |
| `npx playwright test -c playwright.web.config.ts` | 39 passed (sebelum dan sesudah R016-A) |
| Probe open/close tab ×25 (sementara, dihapus) | tanpa leak/error |
| `npm audit --omit=dev` | 0 vulnerabilities |

File diubah reviewer: `apps/web/src/features/office/Office.tsx`, `README.md`, backlog, file ini.

## Recheck Codex

Codex memeriksa perbaikan R016-A: loop kontinu hanya dipakai saat animasi aktif dan ada role non-idle; saat berhenti,
kepala direset dan frame di-invalidate agar mode demand tidak membekukan pose antara. Temuan dependency (observasi 5)
ditangani dengan mem-pin `@react-three/fiber` 8.18.0, `three` 0.186.1, dan `@types/three` 0.186.0; types dipindahkan ke
devDependencies. Verifikasi ulang: `npm run build` lulus (warning chunk kantor 891,76 KB minified/239,59 KB gzip),
`npx playwright test -c playwright.web.config.ts` 39 passed, `npm audit --omit=dev` 0 vulnerabilities, `git diff --check` lulus.
R9 tetap OPEN sampai walkthrough provider nyata dan pengujian GPU/mobile dilakukan.
