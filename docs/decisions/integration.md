# Integrasi accepted, dependency, dan recovery Git/DB — DEV-012

Status implementasi: DEV-012 DONE. Review Codex menemukan bug yang diperbaiki langsung;
patch menunggu re-review independen, R7 belum ditutup. Laporan: [DEV-012-review](../reviews/DEV-012-review.md).
Sumber kebutuhan: ARCHITECTURE §9 (Git dan SQLite tidak atomik) dan §10 (integrasi dan release), AC DEV-012.

## Alur

1. **Accept (pengguna, API).** `accept_uat` memvalidasi scope, kandidat, target, verifikasi, bukti yang ditampilkan,
   UAC manual, dan base. Dalam satu transaksi ia mencatat approval UAT immutable dan operasi integrasi
   `{operation_id, status: pending, expected_base, target_sha, target_artifact_id, approval_id}`, lalu tiket menjadi
   `integrating`. Tidak ada Git yang disentuh, tidak ada status Accepted, dan dependency belum terbuka.
2. **Integrator (supervisor).** `app/integration/integrator.py`, hook maintenance worker `--runtime pipeline`, berjalan
   di thread terpisah dan bukan job execution. Ia mengambil operasi `pending` secara serial per proyek (urut waktu
   acceptance), di bawah flock `integration.lock` proyek dan lock ref broker yang sama dengan commit attempt.
3. **Preflight domain** (`integration_plan`): approval harus tepat mem-pin kandidat, scope, target, dan bukti; verifikasi
   lolos pemeriksaan UAT yang sama; semua bukti masih terbaca; operasi cocok dengan kandidat. Gagal → `blocked`.
4. **Keputusan dari ref aktual**:

| Ref `accepted` teramati | Tindakan |
| --- | --- |
| = expected base (dan DB tip = expected) | target harus ada dan fast-forward dari base; `git update-ref accepted <target> <expected>` (compare-and-swap), lalu finalisasi |
| = target, DB tip = expected, target fast-forward dari expected | ref sudah berpindah sebelum crash: finalisasi saja, tanpa update kedua (`recovered`) |
| = target operasi pending lain dari DB tip yang sama | tunda sampai operasi tersebut direkonsiliasi (`retry`) |
| = DB tip ≠ expected (integrasi lain sudah final) | kandidat basi: `integration_diverged`, tiket kembali ke development untuk rebase |
| lainnya | `integration_blocked` dengan bukti; ref tidak di-reset dan tip asing tidak diadopsi |

5. **Finalisasi** (`finish_integration`, satu transaksi): kandidat `accepted` dengan `integrated_sha`, accepted tip
   proyek, event `ticket.integrated`, dependency downstream di-refresh (baru sekarang terbuka), kandidat tiket lain yang
   dibangun di base lama di-supersede (lihat bawah), dan revert memicu perubahan kontrak.

Setiap hasil menyimpan laporan `integration.json` (producer `integrator`: expected/target, ref sebelum/sesudah,
DB tip, hasil, alasan) yang dipin lewat lampiran pesan thread `integration:<ticket>`. Artifact row, hasil domain,
evidence ID pada operasi, dan lampiran pesan disimpan dalam satu transaksi DB. Gagal pin berarti finalisasi DB
ikut rollback; ref Git yang sudah bergerak direkonsiliasi pada retry. GUI menampilkan status
operasi, ref yang terlibat, dan bukti di detail kandidat.

## Crash dan rekonsiliasi

Karena keputusan diambil dari ref aktual setiap kali, restart di titik mana pun aman: crash sebelum update-ref
membiarkan ref di base dan operasi `pending` (diproses lagi); crash sesudah update-ref membiarkan ref di target dan DB
di base lama (finalisasi tanpa menulis ref lagi). Operasi yang `blocked` tidak dicoba ulang otomatis. Retry command
accept dengan key yang sama memutar ulang receipt; accept kedua ditolak karena tiket sudah `integrating`;
Urutan pending memakai waktu approval immutable, bukan updated_at tiket yang berubah saat edit prioritas.
Ref accepted yang hilang/tidak dapat dibaca menghasilkan blocked dengan bukti. Kandidat sebelum approval UAT
tidak memiliki DTO operasi integrasi walaupun provenance source_attempt sudah ada.
Dua accept bersamaan untuk tiket yang sama menghasilkan tepat satu operasi. Cancel dan revisi scope saat `integrating`
ditolak sampai rekonsiliasi selesai.

## Base yang bergerak

Accept memeriksa base saat klik, tetapi beberapa tiket bisa diterima pada base yang sama sebelum integrator berjalan.
Yang pertama di-fast-forward; berikutnya terlihat sebagai "DB tip ≠ expected" dan kembali ke development. Approval UAT
lamanya tetap sebagai histori dan tidak pernah dipindahkan. Saat finalisasi, tiket lain di technical review/QA/UAT yang
kandidatnya dibangun pada base lama juga di-supersede (bukan siklus repair) dengan pesan `rebase_request`.

Developer berikutnya memulai dari base baru dan menerapkan ulang diff kandidat lama (`rebase_onto`: `git apply`
atomik di snapshot tanpa `.git`). Jika konflik, workspace adalah base baru yang bersih dan developer menerima catatan
`rebase_result`. Hasilnya selalu kandidat baru dengan build, technical review, QA, dan UAT baru. Job development
scheduler kini terikat ke accepted base (`...@<base>`), sehingga base baru mendapat job baru, bukan macet di job lama;
budget scope tetap sama.

## Otoritas ref

Hanya integrator yang menulis `refs/heads/accepted`. Broker attempt menolak commit ke ref selain
`refs/heads/attempts/run-*` dan mendeteksi perubahan ref lain selama commit; sandbox developer mendapat snapshot tanpa
`.git`. Penulisan langsung ke ref oleh pihak lain dideteksi integrator sebagai divergence dan diblokir dengan bukti.

## Dependency dan kontrak

Dependency mem-pin versi scope, kandidat, dan integration SHA upstream saat terbuka (sesudah finalisasi, bukan
setelah QA atau klik accept). Tiket yang scope-nya me-revert kandidat upstream (`reverts_candidate_id`) mencatat
perubahan kontrak pada upstream ketika diintegrasikan; downstream yang belum accepted kembali ke development dengan
blocker `dependency_revalidation`, downstream yang accepted mendapat catatan follow-up, dan downstream yang sedang
`integrating` hanya ditandai karena operasinya akan basi saat direkonsiliasi.

## Batas yang diketahui

- `integration_blocked` membutuhkan pemeriksaan operator; belum ada command pemulihan di GUI. Tidak ada reset otomatis.
- Revalidasi dependency membutuhkan receipt verifikasi (`revalidate_dependency`); job pipeline yang menjalankan
  pemeriksaan revalidasi otomatis belum ada, sehingga downstream tetap terblokir sampai receipt itu dibuat.
- Perubahan kontrak otomatis hanya untuk revert; tiket perubahan lain mencatatnya lewat `contract_changed` integrator.
- Rebase developer memakai `git apply` atas diff kandidat (maksimal 16 MB); tidak ada merge tiga arah otomatis.
- Satu worker integrator per host; lock flock tidak melindungi lintas host (VPS adalah DEV-017).
- Repo existing/onboarding (DEV-013) dan release/export (DEV-014) belum ada; proyek tanpa repo terkelola diblokir.
