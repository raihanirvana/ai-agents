"""Product pins: which artifacts are protected from cleanup, and why.

Pins are derived from product references, not stored, so they cannot drift:
  - every approval (target + evidence the user saw), forever;
  - every release: its target, evidence and the build/commit/context it was built from, forever;
  - every verification: its target, evidence and the commit/build/context snapshot it was run
    against (acceptance evidence is kept by default in the MVP). A UAT approval or release
    can only exist for a verified target, so its build/context stay protected even after the
    candidate moves on or is superseded;
  - candidates that are submitted/review_approved/verified/accepted
    (commit, build, target, context and evidence);
  - jobs that can still resume (their context snapshot);
  - message attachments (conversation history).
Rejected or superseded candidates stop pinning, unless an approval/verification still
points at the same artifact.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import ACTIVE_JOB_STATUSES, ACTIVE_PREVIEW_STATUSES, PINNING_CANDIDATE_STATUSES


@dataclass(frozen=True)
class PinRef:
    owner_kind: str
    owner_id: str


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


_CANDIDATE_COLUMNS = ("commit_artifact_id", "build_artifact_id", "target_artifact_id", "context_artifact_id")
_PINNING = _quoted(PINNING_CANDIDATE_STATUSES)

# Built from module constants only (no user input), so string assembly is safe here.
_SELECTS = [
    "SELECT target_artifact_id AS artifact_id, 'approval' AS owner_kind, id AS owner_id FROM approvals",
    "SELECT j.value, 'approval', a.id FROM approvals a, json_each(a.evidence_artifact_ids) j",
    *(f"SELECT {col}, 'release', id FROM releases" for col in
      ("target_artifact_id", "build_artifact_id", "commit_artifact_id", "context_artifact_id")),
    "SELECT j.value, 'release', r.id FROM releases r, json_each(r.evidence_artifact_ids) j",
    *(f"SELECT {col}, 'verification', id FROM verifications" for col in
      ("target_artifact_id", "commit_artifact_id", "build_artifact_id", "context_artifact_id")),
    "SELECT j.value, 'verification', v.id FROM verifications v, json_each(v.evidence_artifact_ids) j",
    "SELECT j.value, 'message', m.id FROM messages m, json_each(m.attachment_ids) j",
    f"SELECT j.value, 'candidate', c.id FROM candidates c, json_each(c.evidence_artifact_ids) j WHERE c.status IN ({_PINNING})",
    *(f"SELECT {col}, 'candidate', id FROM candidates WHERE status IN ({_PINNING})" for col in _CANDIDATE_COLUMNS),
    f"SELECT context_artifact_id, 'job', id FROM jobs WHERE status IN ({_quoted(ACTIVE_JOB_STATUSES)}) "
    "OR json_extract(runtime_ref, '$.cleanup') IS NOT NULL",
    "SELECT j.value, 'job_log', b.id FROM jobs b, json_each(b.result, '$.evidence_artifact_ids') j",
    # Only the latest automatic source snapshot is needed for resume. Historical
    # checkpoint log metadata does not pin every full source copy forever.
    "SELECT json_extract(runtime_ref, '$.pipeline_checkpoint'), 'job_checkpoint', id FROM jobs "
    "WHERE status IN ('queued','running','waiting_input','waiting_quota','failed','stopped') "
    "OR json_extract(runtime_ref, '$.cleanup') IS NOT NULL",
    # A preview that is starting, running or being stopped is serving these exact bytes.
    f"SELECT target_artifact_id, 'preview', id FROM previews WHERE status IN ({_quoted(ACTIVE_PREVIEW_STATUSES)})",
    f"SELECT json_extract(details, '$.bundle_artifact_id'), 'preview', id FROM previews WHERE status IN ({_quoted(ACTIVE_PREVIEW_STATUSES)})",
    f"SELECT json_extract(details, '$.build_artifact_id'), 'preview', id FROM previews WHERE status IN ({_quoted(ACTIVE_PREVIEW_STATUSES)})",
    f"SELECT j.value, 'preview', p.id FROM previews p, json_each(p.details, '$.evidence_ids') j WHERE p.status IN ({_quoted(ACTIVE_PREVIEW_STATUSES)})",
]
_PIN_QUERY = text("SELECT artifact_id, owner_kind, owner_id FROM (" + " UNION ALL ".join(_SELECTS)
                  + ") WHERE artifact_id IS NOT NULL")


def pinned_artifacts(session: Session) -> dict[str, list[PinRef]]:
    """{artifact_id: [owners]} for every currently pinned artifact."""
    pins: dict[str, list[PinRef]] = {}
    for artifact_id, kind, owner in session.execute(_PIN_QUERY):
        ref = PinRef(kind, owner)
        if ref not in pins.setdefault(artifact_id, []):
            pins[artifact_id].append(ref)
    return pins


def pin_owners(session: Session, artifact_id: str) -> list[PinRef]:
    return pinned_artifacts(session).get(artifact_id, [])
