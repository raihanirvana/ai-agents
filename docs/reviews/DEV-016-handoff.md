# Handoff DEV-016 — kantor Three.js dari aktivitas nyata

Baseline: `c94b027` (DEV-015 committed/pushed). Implementasi DEV-016 **DONE** berdasarkan checks di bawah.
Review kode Claude **REVIEWED** dan fix R016-A diperiksa ulang Codex. Checkpoint **R9 tetap OPEN** sampai walkthrough provider nyata dan uji GPU/mobile.

## Diff

- `apps/web/src/features/office/projection.ts`: proyeksi empat role dari `Board.runs` (urutan pembuatan backend) dan pesan tersimpan; status terminal tidak meninggalkan agent “bekerja”, dan run lama aktif tidak tertutup status final.
- `apps/web/src/features/office/Office.tsx`: scene React Three Fiber dengan empat workstation/avatar berstatus warna, click-to-context, ringkasan run/tiket/thread, HTML role navigation, persisted animation toggle, reduced motion default, fake label dan error boundary fallback WebGL.
- `apps/web/src/components/Workspace.tsx`, `apps/web/src/style.css`: tab kantor lazy-load; board/detail/chat/activity tetap di UI existing dan scene baru di-mount saat tab dibuka.
- `package.json`, `package-lock.json`: versi `@react-three/fiber` 8.18.0 dan `three` dipin; `@types/three` dipin sebagai devDependency. Fiber 8 dipakai karena project masih React 18, mengikuti pairing versi dalam [panduan instalasi resmi R3F](https://r3f.docs.pmnd.rs/getting-started/installation).
- `tests/web-browser/flow.spec.ts`, `tests/web-browser/states.spec.ts`: browser proof real local HTTP/API/DB/worker/SSE memakai FakeProvider berlabel, fixture event refresh, click avatar WebGL, dan paksa WebGL unavailable.
- `README.md`, backlog, dan handoff ini.

Tidak ada API/domain baru. Board, Kantor, Aktivitas, dan Chat menggunakan snapshot/event provider existing; tab tidak menjadwalkan run atau mengubah approval/state.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| Empat role/status/aktivitas berasal dari snapshot + event, pesan nyata | `projectOffice()` memproyeksikan `Board.runs`/messages. `flow.spec.ts` membuat proyek dan PO job melalui API/queue/supervisor dengan FakeProvider berlabel; SSE menyegarkan job setelah stop, thread tersimpan tampil dan tetap ada sesudah reload. `states.spec.ts` memverifikasi payload state SSE mengubah role ke idle. Tidak ada dialog atau event simulasi di aplikasi. |
| Klik role/tiket membawa ke konteks; reconnect tidak menampilkan state lama | Klik avatar WebGL menampilkan percakapan dari thread proyek; tombol HTML role memberi jalur keyboard/screen reader; run/status dan pesan terbaru tampil. Run bertiket memilih ticket di domain provider sehingga panel detail existing terbuka. Kantor membaca state dari `WorkspaceProvider`; reconnect/refresh SSE tetap memakai mekanisme existing. Tes browser juga mencakup replay/reconnect suite existing. |
| Fallback UI, animasi dapat dimatikan, board tidak terganggu | Browser memalsukan kegagalan `getContext(webgl*)`, lalu menegaskan fallback roles + board terlihat. Toggle berlabel `aria-pressed`, disimpan lokal, default menghormati `prefers-reduced-motion`; board selalu dirender di samping kantor. |

## Menjalankan dan memeriksa

```sh
npm run build
npx playwright test -c playwright.web.config.ts
```

Hasil aktual: TypeScript + Vite lulus; Playwright **39 passed**. Build Vite memberi warning chunk besar: main JS 214,48 KB, lazy Office JS 891,60 KB minified (239,53 KB gzip). Chunk kantor baru diminta ketika tab dibuka, sehingga board awal tak memuat Three.js. Scene memakai geometri sederhana tanpa model/font assets eksternal.

Browser walkthrough real backend memakai runtime **FakeProvider**, bukan provider/model nyata; ia membuktikan aliran produk/UI event dan diberi label fake. Dua tes state tambahan memakai kontrak API fixture untuk kondisi event baru/WebGL disabled. Tidak ada test visual lintas perangkat GPU/mobile.

Known limitation: scene tidak memiliki navigasi kamera bebas atau asset avatar ilustrasi; status role juga disederhanakan dari role/stage/status job. R3F menjadi dependency baru dan ukuran chunk perlu ditimbang sebelum packaging mobile.

## Status review

Claude menyelesaikan code review dan menyatakan AC terpenuhi. Codex memeriksa R016-A dan mem-pin versi dependency baru; build dan browser suite dijalankan ulang. Status code review **REVIEWED**. R9 tetap **OPEN** karena belum ada walkthrough provider nyata atau pengujian GPU/mobile. DEV-017 belum dimulai.
