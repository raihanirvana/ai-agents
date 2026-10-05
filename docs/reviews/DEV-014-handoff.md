# DEV-014 implementation handoff

Status pada handoff implementer: DONE, NOT_REVIEWED. Review independen dan perbaikan sesudah handoff:
[DEV-014-review.md](./DEV-014-review.md). Angka verifikasi di bawah adalah histori implementer.
Pelaksana: Claude (Sonnet 5.5), 2026-10-05. Baseline: `ef30905` (DEV-013).
Tidak ada commit/push DEV-014. Ini handoff implementer, bukan independent review.

## Hasil dan file

Release kini dibekukan, diverifikasi pada satu target gabungan, disetujui pengguna, dan dapat diekspor lokal; sinkronisasi
gabungan membuat release pengganti bila repo sumber bergerak. Keputusan dan batas: `docs/decisions/release.md`.

| File | Tanggung jawab |
| --- | --- |
| `app/release/requests.py` | Intent freeze/export/sync sebagai job (DB saja, dipakai API), DTO release |
| `app/release/runtime.py` | Worker: build tip beku, repo gate, regression gabungan, target release, draf; blokir permanen terlihat |
| `app/release/export.py`, `sync.py` | Ekspor patch + bundle; sinkronisasi ke HEAD sumber baru (hanya baca) |
| `app/domain/service.py` | `freeze_release_scope`, `draft_release`, `discard_release`, `record_export`, `approve_release` (checklist, digest scope, riwayat tip), `tip_history` |
| `app/integration/integrator.py` | Integrator menahan tip selama job release aktif |
| `app/workspace/{supervisor,gitbroker}.py`, `onboarding/source.py` | Build commit `refs/releases/*`, `fetch_source_head` (hanya baca) |
| `http/{application,queries,schemas}.py`, `contracts/api/*`, `worker.py` | Endpoint release, board `releases`, registrasi runtime `release` |
| `apps/web/src/components/Releases.tsx`, `Workspace.tsx`, `style.css` | Tab Release: freeze, checklist, approval, ekspor, sinkronisasi |
| `tests/domain/test_release.py`, `tests/release/**`, `tests/http/test_releases.py`, `tests/web-browser/states.spec.ts` | Tes domain, Git/Docker nyata, HTTP, GUI |
| `tests/domain/test_evidence.py` | Helper release memuat digest scope (kontrak target diperketat) |

Berkas baru perlu dibaca langsung (`git status --short`); `git diff` tidak mencakup untracked files.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| Release mengunci scope dan SHA accepted tip; tiket setelah freeze masuk release berikutnya; bukan subset commit | `test_freeze_lists_accepted_tickets...`, `test_the_freeze_holds_the_integrator_and_a_ticket_accepted_meanwhile_joins_the_next_release` (Git nyata: integrator menahan, release kedua hanya tiket baru), `test_the_frozen_tip_stays_approvable_after_later_tickets_move_the_accepted_tip` |
| Regression/integration dan UAC manual mengacu target release yang sama (SHA, build/config/toolchain/fixture identity, evidence IDs) | `test_freeze_verifies_one_combined_target...` (target memuat build/toolchain/config/fixture/migrasi/runner/suite digest; receipt cocok target; coverage UAC; checklist manual) |
| Approval release terpisah, mem-pin target/evidence; rebuild/config berubah = target dan approval baru | Tes yang sama (approval mem-pin digest/evidence), `test_a_rebuild_or_changed_configuration_is_a_new_target...` (digest berbeda; approval lama ditolak untuk target baru; config digest berubah), `test_approval_pins_target_and_evidence...`, HTTP `wrong_sha` 409 |
| Export branch/patch eksplisit; tanpa push/PR/deploy; approved bukan deployed | `test_export_produces_a_patch_and_a_bundle...` (patch dan bundle direproduksi pada klon repo pengguna; `pushed/deployed false`; repo pengguna utuh), `test_a_new_project_exports_its_whole_history...`, `test_export_needs_an_approved_release...`, HTTP `deployed false`, GUI |
| Base tujuan export berubah: revalidasi dan approval baru; repo sumber tidak diubah | `test_export_is_refused_when_the_source_repository_moved...`, sidik jari repo sumber tidak berubah pada semua tes sync/export |
| Sinkronisasi gabungan: satu kandidat pengganti, diff, checklist UAC terdampak, regression gabungan, approval baru; approval lama histori; UAC tidak berubah | `test_sync_onto_an_unrelated_source_change...`, `test_sync_with_overlapping_changes_marks_affected_tickets...` (checklist hanya tiket terdampak, ekspor basis HEAD baru), `test_sync_that_conflicts...` (diblokir, tanpa release baru) |
| Verifikasi: regression gagal, SHA salah, build/config, export lokal, restart | `test_a_failing_regression...`, `test_repository_tests_that_fail...`, `test_a_release_of_a_tip_the_project_never_accepted_is_refused`, `test_freeze_verifies...` (koneksi DB baru membaca approval/target/evidence), `test_a_crash_before_the_draft_is_published...` (crash lalu retry: tepat satu release) |

## Cara menjalankan

```sh
cd apps/backend
python -m pytest tests/domain/test_release.py tests/http/test_releases.py -q      # Windows/Linux
python -m pytest tests/release -q                                                   # WSL/Linux + Docker + image runner
cd ../..
npm run build && npx playwright test --config playwright.web.config.ts
```

Produk: worker `python -m app.worker --runtime pipeline`, lalu tab Release.

## Hasil verifikasi aktual

- WSL + Docker, suite backend lengkap: **832 passed** (618 detik), dijalankan sebelum dua tes tambahan (ekspor proyek baru dan
  crash/retry); sesudahnya `tests/release tests/domain tests/http tests/integration`: **220 passed** (163 detik), tanpa container tersisa.
- Windows (`--ignore=tests/workspace --ignore=tests/runtime_spike`): **572 passed, 21 skipped** (tes Git/Docker butuh POSIX).
- GUI `playwright.web.config.ts`: 35 passed (3 baru). Preview browser 4, smoke DEV-001 5, browser DEV-008 3 passed. `npm run build`, `tsc`,
  `git diff --check` lulus.
- Uji mutasi: menghapus penahanan integrator selama freeze menggagalkan tes freeze; menghapus riwayat tip menggagalkan tes tip beku.
- Bug yang ditemukan tes dan diperbaiki: receipt sinkronisasi diparse dari semua artefak producer `verification` termasuk screenshot
  biner (kini hanya `report` JSON); tes tip beku awalnya tidak benar-benar menggeser tip (kini memakai SHA berbeda).

## Known issues

- Repo tests wajib lulus penuh pada release; baseline merah tidak dapat mencapai approved (waiver per tiket tidak berlaku).
- Setelah sinkronisasi, basis sumber baru hanya berlaku untuk release pengganti; sumber yang bergerak lagi butuh sinkronisasi baru.
- Tidak ada tag Git otomatis dan tidak ada deployment/hosting (tahap tersendiri).
- Alasan kegagalan release hanya ada di laporan verifikasi (`evidence_ids[0]`), belum diringkas di DTO.
- Hanya stack statis React/Vite stateless; regression memakai suite QA tiket yang tersimpan.
- Satu operasi release per proyek; QA/regression tidak memakai model sehingga tidak ada klaim provider nyata.

## Verifikasi setelah review Codex

Review awal independen menemukan tujuh bug; fix dan hasil rinci tersedia di [DEV-014-review](DEV-014-review.md). Implementasi kembali DONE; fix reviewer menunggu re-review independen. WSL backend 841 passed sebelum dua guard domain terakhir; targeted WSL 241 passed setelah guard discard sebelum fallback tip; final domain release/HTTP 17 passed pada WSL dan Windows sesudah semua perubahan. Windows full 573 passed/21 skipped; GUI 36 passed; build dan whitespace lulus. Diff lengkap termasuk file baru disertakan pada commit DEV-014. Commit/push diotorisasi pengguna, kemudian assignment DEV-015 dimulai terpisah.
