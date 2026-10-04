# AI Software Development Team

Skeleton lokal untuk platform AI Software Development Team. MVP ini berisi
frontend React, health API FastAPI, dan entry point worker terpisah. Belum ada
scheduler atau agent runtime.

## Toolchain yang digunakan

- Node.js `22.20.0` dan npm `10.9.3` (terdeteksi saat DEV-001, 4 Oktober 2026)
- Python `3.11.6`
- Git `2.56.0`
- Dependensi frontend dipin di `package.json` dan `package-lock.json`.
- Dependensi backend dipin di `apps/backend/requirements.lock` (requirements
  dasar ada di `apps/backend/requirements.txt`).

Versi di atas adalah environment implementasi saat ini. Gunakan Node.js 20+
dan Python 3.11+ untuk checkout lain.

## Persiapan

```sh
npm ci
python3 -m venv apps/backend/.venv
apps/backend/.venv/bin/python -m pip install -r apps/backend/requirements.lock
```

Windows: gunakan `apps/backend/.venv/Scripts/python` sebagai pengganti path
`.venv/bin/python`.

Salin `.env.example` menjadi `.env.local` bila ingin mengganti konfigurasi.
Web dan API membaca file itu dari root checkout. Environment proses memiliki
prioritas tertinggi, lalu `.env.local`, lalu `.env`. Vite juga mengikuti file
mode seperti `.env.development.local`; gunakan `.env.local` untuk konfigurasi
bersama API. Restart layanan setelah mengubah konfigurasi.
File contoh berisi default lokal non-secret. Hanya variabel berawalan `VITE_`
yang diekspos ke frontend; jangan menaruh secret di sana.

## Build frontend

```sh
npm run build
```

## Menjalankan layanan lokal

Jalankan tiap proses pada terminal terpisah dari root checkout.

```sh
# Terminal 1: API pada 127.0.0.1:8000
cd apps/backend
./.venv/bin/python -m app

# Terminal 2: worker terpisah, menunggu tanpa job
cd apps/backend
./.venv/bin/python -m app.worker

# Terminal 3: web pada 127.0.0.1:5173
npm run dev:web
```

Buka <http://127.0.0.1:5173>. API health: <http://127.0.0.1:8000/health>.
Web dan API bind ke `127.0.0.1`; konfigurasi host lain ditolak untuk control
plane lokal. Port dapat diubah melalui `WEB_PORT` dan `API_PORT`;
sesuaikan `VITE_API_BASE_URL` dengan port API. Jika `CORS_ORIGINS` diisi,
sesuaikan dengan exact origin web; jika tidak diisi, API memakai origin
`WEB_PORT` dan `WEB_PREVIEW_PORT`. Port yang terpakai membuat Vite gagal start,
bukan bergeser diam-diam. `localhost` disisihkan untuk preview aplikasi hasil
agent di tahap berikutnya.

Halaman memeriksa health berkala, dengan timeout 3 detik; pada tab aktif,
perubahan status biasanya terlihat dalam 2–5 detik tanpa reload.
Untuk mencoba production build control UI, jalankan `npm run build` lalu
`npm run preview:web` (default `127.0.0.1:5174`, dapat diubah lewat
`WEB_PREVIEW_PORT`). Jalankan API juga.

Worker/API berhenti dengan Ctrl+C (SIGINT); SIGTERM juga ditangani worker.
Tidak dibutuhkan provider key, Hermes, container engine, database, atau VPS.

## Regression smoke tests

```sh
npx playwright install chromium
npm run test:smoke
```

Test menjalankan web pada `127.0.0.1:19832` dan memock health API untuk menguji
koneksi putus/pulih tanpa reload, startup tertunda, dan respons tidak valid.
Ini menguji skeleton UI, bukan QA aplikasi hasil agent. Bukti review API dan
worker aktual ada di `docs/reviews/DEV-001-R1.md`.
