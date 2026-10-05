# DEV-012 implementation handoff

Status implementasi: DONE. Review Codex: NEEDS_FIX awal, temuan diperbaiki langsung;
perbaikan menunggu re-review independen, R7 belum ditutup. Lihat [DEV-012-review](DEV-012-review.md).
Pelaksana: Claude (Opus 5.5), 2026-10-05. Baseline: `cfdd16b` (DEV-011).
Tidak ada commit/push DEV-012. Ini handoff implementer, bukan independent review.

## Hasil dan file

Operasi integrasi `pending` yang dicatat `accept_uat` kini dijalankan oleh integrator milik supervisor: preflight
domain, compare-and-swap fast-forward `refs/heads/accepted`, lalu finalisasi DB. Rekonsiliasi dari ref aktual
menangani crash, kandidat basi, dan divergence. Keputusan: `docs/decisions/integration.md`.

| File | Tanggung jawab |
| --- | --- |
| `app/integration/integrator.py` | Integrator: lock proyek + lock ref broker, keputusan dari ref aktual, CAS, bukti, fault hook untuk tes |
| `app/domain/service.py` | `integration_plan` (preflight), `integration_blocked`, `_base_advanced`; `finish_integration` men-supersede kandidat base lama dan memproses revert; `integration_diverged` hanya mengadopsi tip yang sudah final di DB; `_contract_change` dapat dipakai di dalam transaksi |
| `app/pipeline/workspace.py` | `rebase_onto` (export base baru + `git apply` diff kandidat), start developer dari `rebase_request`/`repair_feedback`, catatan `rebase_result` |
| `app/pipeline/scheduler.py` | Kunci job development menyertakan accepted base |
| `app/pipeline/wiring.py`, `app/worker.py` | `build_integrator`; hook maintenance dan shutdown pada `--runtime pipeline` |
| `app/http/queries.py`, `contracts/api/types.ts` | `integration` dan `integrated_sha` pada DTO kandidat |
| `apps/web/src/components/Ticket.tsx`, `format.ts` | Baris status integrasi + bukti; label blocker `integration_blocked` |
| `tests/integration/**` | 11 tes integrator, 3 tes rebase/scheduler, 1 tes HTTP (Git nyata) |
| `tests/domain/test_evidence.py` | Dua tes divergence disesuaikan: tip tak dikenal kini ditolak, bukan diadopsi |
| `tests/web-browser/states.spec.ts` | Tes GUI tampilan integrasi diblokir |

Berkas baru perlu dibaca langsung (`git status --short`); `git diff` tidak mencakup untracked files.

## Pemetaan acceptance criteria

| AC | Bukti |
| --- | --- |
| Pending operation expected/target, CAS fast-forward, finalisasi DB/event/dependency | `test_accept_moves_the_ref_first_and_only_then_records_accepted...` (ref dan DB belum berubah setelah accept; urutan event `uat_accepted` < `integrated`; laporan bukti) |
| Validasi scope/kandidat/target/bukti/base/integrator; developer tidak bisa menulis accepted | `integration_plan` + `test_missing_approved_evidence_blocks...`, `test_a_candidate_that_is_not_a_fast_forward...`, `test_the_developer_broker_cannot_write_the_accepted_ref...` |
| Git/SQLite tidak atomik; restart merekonsiliasi; divergence diblokir dengan bukti tanpa reset | `test_a_crash_before_the_ref_update...after_it_finalises_without_a_second_update`, `test_a_ref_moved_outside_the_integrator_blocks...neither_reset_nor_adopted` |
| Accepted hanya setelah integrasi; dependency terbuka sesudahnya | Tes pertama: downstream tidak eligible setelah accept, eligible setelah finalisasi; dependency mem-pin kandidat dan integration SHA |
| Base berubah saat UAT → kandidat, review, QA, UAT baru; approval lama tetap histori | `test_two_accepts_on_the_same_base...approval_kept`, `test_a_base_change_during_uat_requires_a_new_candidate...`, `test_rebase.py` (rebase bersih/konflik, job development baru per base) |
| Retry tidak menggandakan; cancel/revisi menunggu rekonsiliasi | Tes pertama (accept ulang 409, integrasi ulang no-op), `test_concurrent_accepts_of_one_ticket...`, `test_cancel_and_scope_revision_wait_while_integrating`, `test_http.py` (receipt sama, key baru 409) |
| Dependency pin dan perubahan kontrak memblokir downstream sampai revalidasi | `test_a_revert_ticket_changes_the_upstream_contract...` dan tes dependency DEV-003 yang ada |

## Cara menjalankan

```sh
cd apps/backend
python -m pytest tests/integration tests/domain -q   # POSIX (Git + fcntl); tidak butuh Docker
python -m pytest -q                                  # suite lengkap (WSL + Docker)
cd ../..
npx playwright test --config playwright.web.config.ts
```

Produk: worker `python -m app.worker --runtime pipeline` menjalankan integrator bersama scheduler dan preview.

## Hasil verifikasi aktual

Angka berikut adalah hasil implementer sebelum patch review; hasil reviewer ada pada laporan terpisah di atas.

- WSL + Docker, suite backend lengkap: **768 passed** (375 detik).
- Windows (`--ignore=tests/workspace --ignore=tests/runtime_spike`): **555 passed, 15 skipped** (tes integrasi butuh POSIX).
- GUI 31 passed (30 + 1 tes integrasi baru), preview browser 4, smoke 5, browser DEV-008 3; `npm run build` lulus.
- Uji mutasi: menghapus `_base_advanced` menggagalkan tes base berubah saat UAT; membuat integrator mengadopsi ref
  apa pun menggagalkan tes divergence.

## Known issues

- Operasi `blocked` butuh pemeriksaan operator; belum ada command pemulihan. Tidak ada reset otomatis (disengaja).
- Revalidasi dependency membutuhkan receipt verifikasi; job pipeline yang menjalankannya otomatis belum ada.
- Kontrak berubah otomatis hanya dari revert; perubahan lain lewat `contract_changed`.
- Rebase memakai `git apply` atas diff kandidat; konflik berarti developer mulai dari base baru.
  Jalur `ProductWorkspace.start` untuk rebase diuji pada fungsi `rebase_onto`, belum dengan model/Hermes nyata.
- Satu integrator per host; repo existing (DEV-013) dan release (DEV-014) belum ada.
