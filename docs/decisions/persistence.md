# Keputusan persistence — DEV-002

Tanggal: 5 Oktober 2026. Status: diimplementasikan; review R4 menunggu DEV-003/004.
Review DEV-002: **REVIEWED**, Codex, setelah perbaikan dan recheck
([laporan](../reviews/DEV-002-review.md)); bukan penutupan seluruh R4.
Paket: `apps/backend/app/persistence/`, migrasi: `apps/backend/migrations/`.

## Keputusan

- **SQLite WAL** lewat SQLAlchemy `2.1.3` dan Alembic `1.20.0` (dipin di
  `requirements.lock`). Satu file database lokal; tidak ada server database.
- Setiap koneksi: `foreign_keys=ON`, `journal_mode=WAL` (engine menolak start bila WAL
  tidak tersedia, mis. SQLite in-memory), `synchronous=FULL`, `busy_timeout=30s`.
- **Transaksi tulis memakai `BEGIN IMMEDIATE`** (`Database.write()`): writer yang bersamaan
  antre pada kunci database, bukan gagal di tengah read-then-write. Karena itu urutan
  cursor event sama dengan urutan commit. Pembaca memakai snapshot WAL dan tidak diblok
  (`Database.read()`).
- Migrasi memakai DDL transaksional: migrasi yang gagal tidak meninggalkan skema setengah jadi
  (teruji). `python -m app.persistence {upgrade,current,check}`.
- API/worker belum memakai database; keduanya tetap bisa start tanpa DB (AC-001-02).
  Wiring ke API/worker adalah DEV-003/004/008.

## 12 entitas

Tabel: `projects`, `tickets`, `ticket_versions`, `approvals`, `dependencies`, `messages`,
`jobs`, `candidates`, `verifications`, `artifacts`, `releases`, `events`. Tidak ada tabel ke-13.
Bentuk yang dipilih (ARCHITECTURE §7 mengizinkan relasi/JSON):

| Kebutuhan | Bentuk |
| --- | --- |
| Target/evidence ref | `target_artifact_id` + `target_digest` sebagai **FK komposit** ke `artifacts(id, checksum)`; digest salah ditolak storage. Evidence ID = array JSON di approval/verification/candidate/release. |
| Build/target manifest immutable | Artifact `build_record` / `target_manifest` (JSON kanonik, checksum sama untuk dokumen sama). |
| Input request/answer | `messages.kind` = `input_request`/`input_answer`, `reply_to`, `metadata`; indeks unik: **satu jawaban per request**. `jobs.waiting_request_id` wajib terisi pada `waiting_input`. |
| Usage per scope | `jobs.usage` (JSON counters); `scope_usage()` menjumlah semua job/attempt scope yang sama. Counter yang tidak dilaporkan provider dicatat **unknown**, bukan 0. |
| Baseline waiver | `approvals.type='baseline_waiver'`, target = artifact fingerprint, `details` = base SHA/environment/scope. |
| Integration operation | `candidates.integration` (JSON) + `integrated_sha`. |
| Versi scope | `ticket_versions` immutable, unik `(ticket_id, version)`; `tickets.current_version` hanya maju dan harus menunjuk versi yang ada. |

## Aturan yang ditegakkan storage (trigger/CHECK/FK/unique)

| Aturan | Mekanisme |
| --- | --- |
| Histori tidak ditimpa | `ticket_versions`, `approvals`, `events`, `messages`, `verifications`: UPDATE/DELETE ditolak. Kolom identitas `tickets`/`artifacts`/`candidates`/`releases` tidak bisa diubah. Tidak ada DELETE pada `projects`, `tickets`, `jobs`, `candidates`, `releases`, `artifacts`. |
| Revision | Entitas bernomor revision (`projects`, `tickets`, `candidates`, `releases`, `dependencies`): trigger mewajibkan revision naik **tepat satu** per update; writer dengan data basi ditolak walau tidak memakai `apply_change`. ORM memakai `version_id_col` (`StaleDataError`). `jobs` dipagari `lease_generation` (tidak boleh turun). |
| Approval scope | Harus menyebut versi **saat ini** dari tiket; satu approval per versi. |
| Approval UAT | Hanya untuk kandidat `verified` dengan **target digest yang sama**; satu per (kandidat, target). |
| Approval release | Hanya untuk release `draft` dengan target yang sama; release baru boleh `approved/exported/deployed` bila ada approval untuk **target yang sama persis**. |
| Kandidat `verified` | Butuh verification `passed` untuk target **saat ini**. `accepted` butuh status `verified`, approval UAT untuk target saat ini, dan `integrated_sha`. Target tidak bisa diganti saat `verified/accepted` (rebuild = QA+UAT baru, SHA sama sekalipun). `accepted/rejected/superseded` final. |
| Snapshot release | `releases` menyimpan `build_artifact_id` (wajib), `commit_artifact_id`, `context_artifact_id` yang beku bersama scope dan target, dan hanya boleh menunjuk artifact available milik proyek yang sama. |
| Snapshot verifikasi | `verifications` menyimpan snapshot `commit/build/context` selain target dan evidence. Trigger mewajibkan snapshot sama dengan keadaan kandidat **saat itu** (target saat ini, tidak ada referensi yang dihilangkan); hasil terlambat untuk target lama ditolak. |
| QA pass | Verification `passed` hanya bila `executed>0`, `discovered=executed=passed`, `failed=skipped=0`, ada expected test dan evidence. Tanpa itu hanya `failed`/`incomplete`. |
| Referensi artifact | Insert approval/verification/release/candidate/pesan hanya ke artifact yang **ada, available, dan satu proyek**. |
| Lainnya | FK, unique idempotency key (job, candidate, pesan), CHECK enum/SHA/JSON, `running` wajib lease, kandidat/release mulai dari `submitted`/`draft`. |

Trigger dan kolom snapshot berada di revisi `0001`, bukan di model. **Migrasi batch SQLite membuat ulang tabel dan
menghapus trigger-nya**: migrasi berikutnya yang mengubah tabel ber-trigger harus membuat ulang
trigger tersebut (catatan juga ada di `migrations/env.py`). `python -m app.persistence check`
memeriksa revisi, drift skema terhadap model, `integrity_check`, dan `foreign_key_check`
(drift tidak mencakup CHECK/trigger; keduanya diuji perilakunya).

## Memakai dari service (DEV-003 dst.)

```python
db = Database(DATABASE_PATH)                 # setelah: python -m app.persistence upgrade
with db.write() as s:                        # satu transaksi pendek; rollback bila ada error
    row = apply_change(s, Ticket, ticket_id, expected_revision=rev,
                       values={"phase": "ready"},
                       event=EventSpec("ticket.phase_changed", "user:local", {"to": "ready"}))
# state + event commit bersama; RevisionConflict/NotFound tidak menulis apa pun.
db.transact(work)                            # ulang hanya pada RevisionConflict; work membaca ulang state
```

- `append_event` / `read_events(after=cursor)` untuk SSE; cursor tidak pernah dipakai ulang
  dan transaksi yang di-rollback tidak meninggalkan celah.
- `append_message` (idempotency key) dan `answer_input_request` (satu jawaban, retry no-op). Retry dianggap
  identik hanya bila **seluruh** payload sama (thread, pengirim, penerima, kind, body, tiket, reply_to,
  metadata, lampiran; JSON dinormalisasi). Key sama dengan isi berbeda, misalnya generation lain, ditolak
  `IdempotencyConflict`. Apakah generation tersebut masih aktif tetap diperiksa domain/scheduler.
- `ArtifactStore.put_bytes/put_json/put_git_commit`, `verify`, `require_available`.
  File ditulis dulu, baris DB di transaksi pemanggil. Setiap file terikat pada transaksi yang membuatnya:
  rollback savepoint hanya membuang file miliknya, savepoint yang dilepas ikut nasib transaksi luar, rollback
  luar membuang semua yang belum commit.
- Aturan domain (siapa boleh, transisi fase, siklus dependency, batch all-or-nothing) tetap milik DEV-003.

## Pin dan cleanup

Pin **diturunkan** dari referensi produk, tidak disimpan, sehingga tidak bisa drift (`pins.py`):
approval (selamanya), **release** (target, evidence, dan build/commit/context asalnya; selamanya),
**verification** (target, evidence, dan snapshot commit/build/context yang diverifikasi; selamanya), kandidat `submitted/review_approved/verified/accepted` (commit, build, target,
context, evidence terkini), job yang masih bisa resume (context), lampiran pesan. Kandidat
`rejected/superseded` berhenti mem-pin referensi terkininya. Closure bukti yang pernah disetujui tidak
bergantung pada kolom kandidat yang bisa berubah: UAT dilindungi snapshot
verification walau kandidat di-supersede atau di-rebuild. Release mem-pin snapshot
build/commit/context-nya sendiri; tidak bergantung pada verification kandidat.
`cleanup_unpinned` hanya memproses satu proyek, hanya artifact tak terpin, berumur ≥ `min_age`
(default 1 jam), dengan path di bawah proyek itu. Dry run adalah default. Urutan: DB (tandai
`unavailable/cleaned` + event) lalu hapus file. Artifact `cleaned` tidak bisa hidup lagi walau
file muncul kembali. File hilang/rusak ditandai `unavailable` oleh `verify()`; approval baru
yang membutuhkannya ditolak storage; bacaan tetap memeriksa checksum.

## Batas dan known issues

- Bukan scheduler: claim/lease/heartbeat/retry adalah DEV-004; `jobs` hanya menyediakan kolom dan
  constraint. Slot execution tunggal belum dipaksa di storage.
- Daftar status kandidat/dependency/release adalah usulan dari alur ARCHITECTURE §4 dan dapat
  disesuaikan DEV-003 lewat migrasi baru.
- Crash antara menulis file dan commit DB dapat meninggalkan file yatim (tidak punya baris, tidak
  pernah disajikan). Belum ada penyapu file yatim.
- Cleanup menghapus file setelah commit DB; crash di antaranya menyisakan file tanpa dampak.
- Diuji pada NTFS Windows dan ext4 Linux (WSL, `/tmp`). Database di `/mnt/c` dari WSL **tidak
  diuji**; simpan database pada filesystem lokal. Artifact `git_commit` diverifikasi oleh lapisan
  integrasi (DEV-005/012), bukan oleh store ini.
- Backup/restore lokal (DB + Git + artefak) adalah DEV-017; DEV-002 hanya menyediakan penanda
  unavailable dan pin.
