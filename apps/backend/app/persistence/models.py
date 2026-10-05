"""The 12 core entities (ARCHITECTURE §7) with the constraints SQLite can enforce.

Rules that need more than a column constraint (immutability, "accepted needs the
matching approval", artifact availability guards) are SQLite triggers created by the
initial migration. The ORM has no relationship() on purpose (SQLAlchemy still orders
inserts by foreign key); cross-entity domain rules live in the services (DEV-003+).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, MetaData,
    String, Text, UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .columns import Json, UtcDateTime, new_id, utcnow

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

PROJECT_MODES = ("new", "existing")
PHASES = ("draft", "scope_review", "ready", "development", "technical_review",
          "qa", "uat", "integrating", "accepted", "cancelled")
APPROVAL_TYPES = ("scope", "uat", "release", "baseline_waiver")
DEPENDENCY_STATES = ("waiting", "satisfied", "needs_revalidation")
MESSAGE_KINDS = ("message", "input_request", "input_answer", "system")
LANES = ("interactive", "execution")
# Execution state is separate from the ticket phase (ARCHITECTURE §9).
JOB_STATUSES = ("queued", "running", "waiting_input", "waiting_quota",
                "stopped", "failed", "cancelled", "succeeded")
ACTIVE_JOB_STATUSES = ("queued", "running", "waiting_input", "waiting_quota")
CANDIDATE_STATUSES = ("submitted", "review_approved", "verified", "accepted",
                      "rejected", "superseded")
# Candidates in these states keep their build/target/context/evidence pinned.
PINNING_CANDIDATE_STATUSES = ("submitted", "review_approved", "verified", "accepted")
VERIFICATION_STATUSES = ("passed", "failed", "incomplete")
ARTIFACT_KINDS = ("build_record", "target_manifest", "evidence", "log", "screenshot",
                  "trace", "context", "git_commit", "report", "other")
ARTIFACT_STORAGE = ("file", "git")
ARTIFACT_UNAVAILABLE_REASONS = ("missing", "corrupt", "size_mismatch", "unreadable", "cleaned")
RELEASE_STATUSES = ("draft", "approved", "exported", "deployed", "failed")
# Preview lifecycle (DEV-011). Only one row may be non-terminal at a time (the MVP has one local preview).
PREVIEW_STATUSES = ("requested", "starting", "ready", "stopping", "stopped", "failed")
ACTIVE_PREVIEW_STATUSES = ("requested", "starting", "ready", "stopping")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _sha(column: str) -> str:
    return f"length({column}) IN (40, 64) AND {column} NOT GLOB '*[^0-9a-f]*'"


def _json(column: str, kind: str) -> CheckConstraint:
    # NULL passes (json_valid(NULL) is NULL), so optional columns stay optional.
    return CheckConstraint(f"json_valid({column}) AND json_type({column}) = '{kind}'",
                           name=f"{column}_json")


def _both_or_neither(a: str, b: str) -> str:
    return f"({a} IS NULL) = ({b} IS NULL)"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    brief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brief_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    repo_ref: Mapped[str | None] = mapped_column(String)
    runner_manifest: Mapped[Any] = mapped_column(Json, nullable=True)
    workflow: Mapped[Any] = mapped_column(Json, nullable=False, default=dict, server_default=text("'{}'"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Optimistic locking: every ORM UPDATE is 'WHERE revision = <loaded>' and bumps it; a stale
    # writer gets StaleDataError. A trigger enforces the same for raw SQL (migration 0001).
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="name"),
        CheckConstraint(_in("mode", PROJECT_MODES), name="mode"),
        CheckConstraint("mode = 'new' OR repo_ref IS NOT NULL", name="existing_has_repo"),
        CheckConstraint("brief_version >= 1 AND revision >= 1", name="versions"),
        _json("runner_manifest", "object"),
        _json("workflow", "object"),
    )


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    phase: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Points at ticket_versions(ticket_id, version); a trigger checks existence
    # (a composite FK would make tickets <-> ticket_versions circular).
    current_version: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocker: Mapped[Any] = mapped_column(Json, nullable=True)  # {"reason": ..., "resolution": ...}
    workflow: Mapped[Any] = mapped_column(Json, nullable=False, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Optimistic locking: every ORM UPDATE is 'WHERE revision = <loaded>' and bumps it; a stale
    # writer gets StaleDataError. A trigger enforces the same for raw SQL (migration 0001).
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        CheckConstraint("number >= 1 AND revision >= 1", name="counters"),
        CheckConstraint("length(trim(title)) > 0", name="title"),
        CheckConstraint(_in("phase", PHASES), name="phase"),
        CheckConstraint("current_version IS NULL OR current_version >= 1", name="current_version"),
        CheckConstraint("phase = 'draft' OR current_version IS NOT NULL", name="phase_has_version"),
        _json("blocker", "object"),
        _json("workflow", "object"),
        Index("ix_tickets_project_phase", "project_id", "phase"),
    )


class TicketVersion(Base):
    """Immutable scope/UAC snapshot (trigger forbids UPDATE and DELETE)."""

    __tablename__ = "ticket_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    uac: Mapped[Any] = mapped_column(Json, nullable=False)  # [{"id": ..., "text": ...}]
    scope: Mapped[Any] = mapped_column(Json, nullable=False, default=dict, server_default=text("'{}'"))
    content_digest: Mapped[str] = mapped_column(String, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("ticket_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(_sha("content_digest"), name="content_digest"),
        _json("uac", "array"),
        _json("scope", "object"),
    )


class Artifact(Base):
    """File (or git object) reference with checksum. Identity columns are immutable;
    only availability may change (triggers)."""

    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    storage: Mapped[str] = mapped_column(String, nullable=False, default="file")
    path: Mapped[str | None] = mapped_column(String)  # relative to the artifact root
    checksum: Mapped[str] = mapped_column(String, nullable=False)  # sha256, or git object id
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(String)  # supervisor run id (no FK: owned by DEV-005)
    meta: Mapped[Any] = mapped_column("metadata", Json, nullable=False, default=dict)
    availability: Mapped[str] = mapped_column(String, nullable=False, default="available")
    unavailable_reason: Mapped[str | None] = mapped_column(String)
    verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("id", "checksum"),  # target for composite digest FKs
        CheckConstraint(_in("kind", ARTIFACT_KINDS), name="kind"),
        CheckConstraint(_in("storage", ARTIFACT_STORAGE), name="storage"),
        CheckConstraint(_sha("checksum"), name="checksum"),
        CheckConstraint("(storage = 'file' AND path IS NOT NULL AND size_bytes IS NOT NULL AND size_bytes >= 0) OR "
                        "(storage = 'git' AND path IS NULL AND size_bytes IS NULL)", name="storage_shape"),
        CheckConstraint("(availability = 'available' AND unavailable_reason IS NULL) OR "
                        "(availability = 'unavailable' AND unavailable_reason IS NOT NULL AND "
                        f"{_in('unavailable_reason', ARTIFACT_UNAVAILABLE_REASONS)})",
                        name="availability"),
        _json("metadata", "object"),
        Index("ix_artifacts_project_kind", "project_id", "kind"),
        Index("ux_artifacts_path", "path", unique=True, sqlite_where=text("path IS NOT NULL")),
    )


class Message(Base):
    """Append-only conversation/thread record, including input requests and answers."""

    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # per-thread order
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    sender: Mapped[str] = mapped_column(String, nullable=False)
    recipient: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="message")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String)
    # Input request/answer metadata: job_id, generation, scope_version, request key, ...
    meta: Mapped[Any] = mapped_column("metadata", Json, nullable=False, default=dict)
    attachment_ids: Mapped[Any] = mapped_column(Json, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        UniqueConstraint("thread_id", "seq"),
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint("seq >= 1", name="seq"),
        CheckConstraint(_in("kind", MESSAGE_KINDS), name="kind"),
        CheckConstraint("kind <> 'input_answer' OR reply_to IS NOT NULL", name="answer_replies"),
        _json("metadata", "object"),
        _json("attachment_ids", "array"),
        # One answer per input request: retries are idempotent, a second answer fails.
        Index("ux_messages_one_answer", "reply_to", unique=True, sqlite_where=text("kind = 'input_answer'")),
        Index("ix_messages_thread", "thread_id", "seq"),
    )


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    scope_version: Mapped[int | None] = mapped_column(Integer)
    lane: Mapped[str] = mapped_column(String, nullable=False)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    parent_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    available_at: Mapped[datetime | None] = mapped_column(UtcDateTime)  # earliest claim (backoff/quota)
    waiting_request_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"))
    runtime_ref: Mapped[Any] = mapped_column(Json, nullable=True)
    context_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    limits: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    usage: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    result: Mapped[Any] = mapped_column(Json, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    __table_args__ = (
        ForeignKeyConstraint(["ticket_id", "scope_version"], ["ticket_versions.ticket_id", "ticket_versions.version"]),
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint(_both_or_neither("ticket_id", "scope_version"), name="scope_pair"),
        CheckConstraint(_in("lane", LANES), name="lane"),
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("attempt >= 1 AND lease_generation >= 0 AND revision >= 1", name="counters"),
        CheckConstraint("status <> 'running' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
                        name="running_has_lease"),
        # Contract E: the request is persisted before the slot is released.
        CheckConstraint("status <> 'waiting_input' OR waiting_request_id IS NOT NULL", name="waiting_has_request"),
        _json("runtime_ref", "object"),
        _json("limits", "object"),
        _json("usage", "object"),
        _json("result", "object"),
        Index("ix_jobs_claim", "status", "lane", "created_at"),
        Index("ix_jobs_scope", "ticket_id", "scope_version"),
    )


class Candidate(Base):
    """A submitted commit. Identity columns are immutable (trigger); status moves are
    guarded: 'verified' needs a passed verification and 'accepted' needs a UAT approval,
    both for the candidate's CURRENT target digest."""

    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    scope_version: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    base_sha: Mapped[str] = mapped_column(String, nullable=False)
    attempt_ref: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="submitted")
    commit_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    build_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    target_artifact_id: Mapped[str | None] = mapped_column(String)
    target_digest: Mapped[str | None] = mapped_column(String)
    context_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    evidence_artifact_ids: Mapped[Any] = mapped_column(Json, nullable=False, default=list)
    preview: Mapped[Any] = mapped_column(Json, nullable=True)
    # Integration operation: expected accepted SHA, target SHA, pending/done (ARCHITECTURE §9).
    integration: Mapped[Any] = mapped_column(Json, nullable=True)
    integrated_sha: Mapped[str | None] = mapped_column(String)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Optimistic locking: every ORM UPDATE is 'WHERE revision = <loaded>' and bumps it; a stale
    # writer gets StaleDataError. A trigger enforces the same for raw SQL (migration 0001).
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        ForeignKeyConstraint(["ticket_id", "scope_version"], ["ticket_versions.ticket_id", "ticket_versions.version"]),
        ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"]),
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint(_sha("commit_sha"), name="commit_sha"),
        CheckConstraint(_sha("base_sha"), name="base_sha"),
        CheckConstraint(f"integrated_sha IS NULL OR ({_sha('integrated_sha')})", name="integrated_sha"),
        CheckConstraint(_in("status", CANDIDATE_STATUSES), name="status"),
        CheckConstraint(_both_or_neither("target_artifact_id", "target_digest"), name="target_pair"),
        CheckConstraint("status <> 'accepted' OR integrated_sha IS NOT NULL", name="accepted_integrated"),
        CheckConstraint("scope_version >= 1 AND revision >= 1", name="counters"),
        _json("evidence_artifact_ids", "array"),
        _json("preview", "object"),
        _json("integration", "object"),
        Index("ix_candidates_scope", "ticket_id", "scope_version"),
    )


class Verification(Base):
    """Immutable QA/acceptance result for one target. 'passed' needs consistent counts."""

    __tablename__ = "verifications"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    target_artifact_id: Mapped[str] = mapped_column(String, nullable=False)
    target_digest: Mapped[str] = mapped_column(String, nullable=False)
    # Snapshot of what was verified: the candidate's commit/build/context at that moment (a trigger
    # checks it matches). Candidate columns are mutable; this record is not, so pins can rely on it.
    commit_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    build_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    context_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    evidence_id: Mapped[str] = mapped_column(String, nullable=False)  # runner invocation id
    suite_digest: Mapped[str] = mapped_column(String, nullable=False)
    expected_test_ids: Mapped[Any] = mapped_column(Json, nullable=False)
    counts: Mapped[Any] = mapped_column(Json, nullable=False)  # discovered/executed/passed/failed/skipped
    uac_coverage: Mapped[Any] = mapped_column(Json, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    results: Mapped[Any] = mapped_column(Json, nullable=False)
    evidence_artifact_ids: Mapped[Any] = mapped_column(Json, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"]),
        UniqueConstraint("evidence_id"),
        CheckConstraint(_in("status", VERIFICATION_STATUSES), name="status"),
        CheckConstraint(
            "status <> 'passed' OR ("
            "ifnull(json_extract(counts, '$.executed'), 0) > 0 AND "
            "ifnull(json_extract(counts, '$.discovered'), -1) = ifnull(json_extract(counts, '$.executed'), -2) AND "
            "ifnull(json_extract(counts, '$.passed'), -1) = ifnull(json_extract(counts, '$.executed'), -2) AND "
            "ifnull(json_extract(counts, '$.failed'), 1) = 0 AND "
            "ifnull(json_extract(counts, '$.skipped'), 1) = 0 AND "
            "json_array_length(expected_test_ids) > 0 AND json_array_length(evidence_artifact_ids) > 0)",
            name="passed_has_execution"),
        _json("expected_test_ids", "array"),
        _json("counts", "object"),
        _json("uac_coverage", "object"),
        _json("results", "object"),
        _json("evidence_artifact_ids", "array"),
        Index("ix_verifications_candidate", "candidate_id"),
    )


class Release(Base):
    """Frozen scope and target. Frozen columns are immutable (trigger); status beyond
    'draft' needs a release approval for exactly this target digest."""

    __tablename__ = "releases"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    scope_snapshot: Mapped[Any] = mapped_column(Json, nullable=False)
    accepted_tip: Mapped[str] = mapped_column(String, nullable=False)
    target_artifact_id: Mapped[str] = mapped_column(String, nullable=False)
    target_digest: Mapped[str] = mapped_column(String, nullable=False)
    # What the combined target was built from. Frozen with the release, so pins and cleanup never
    # depend on mutable rows. The build record is mandatory; commit and context are optional.
    build_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"), nullable=False)
    commit_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    context_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"))
    evidence_artifact_ids: Mapped[Any] = mapped_column(Json, nullable=False)
    export_result: Mapped[Any] = mapped_column(Json, nullable=True)
    deployment_result: Mapped[Any] = mapped_column(Json, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Optimistic locking: every ORM UPDATE is 'WHERE revision = <loaded>' and bumps it; a stale
    # writer gets StaleDataError. A trigger enforces the same for raw SQL (migration 0001).
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"]),
        CheckConstraint(_in("status", RELEASE_STATUSES), name="status"),
        CheckConstraint(_sha("accepted_tip"), name="accepted_tip"),
        CheckConstraint("revision >= 1", name="revision"),
        _json("scope_snapshot", "array"),
        _json("evidence_artifact_ids", "array"),
        _json("export_result", "object"),
        _json("deployment_result", "object"),
    )


class Approval(Base):
    """Append-only approval history (trigger forbids UPDATE and DELETE). An approval
    names the exact version/candidate/target digest and the evidence the user saw."""

    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    ticket_id: Mapped[str | None] = mapped_column(ForeignKey("tickets.id"))
    scope_version: Mapped[int | None] = mapped_column(Integer)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"))
    release_id: Mapped[str | None] = mapped_column(ForeignKey("releases.id"))
    target_artifact_id: Mapped[str | None] = mapped_column(String)
    target_digest: Mapped[str | None] = mapped_column(String)
    evidence_artifact_ids: Mapped[Any] = mapped_column(Json, nullable=False, default=list)
    batch_id: Mapped[str | None] = mapped_column(String)
    # Baseline waiver: fingerprint, base SHA, environment and scope it applies to.
    details: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        ForeignKeyConstraint(["ticket_id", "scope_version"], ["ticket_versions.ticket_id", "ticket_versions.version"]),
        ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"]),
        CheckConstraint(_in("type", APPROVAL_TYPES), name="type"),
        CheckConstraint("length(trim(user_id)) > 0", name="user_id"),
        CheckConstraint(_both_or_neither("ticket_id", "scope_version"), name="scope_pair"),
        CheckConstraint(_both_or_neither("target_artifact_id", "target_digest"), name="target_pair"),
        CheckConstraint(
            "(type = 'scope' AND ticket_id IS NOT NULL AND scope_version IS NOT NULL) OR "
            "(type = 'uat' AND candidate_id IS NOT NULL AND target_artifact_id IS NOT NULL) OR "
            "(type = 'release' AND release_id IS NOT NULL AND target_artifact_id IS NOT NULL) OR "
            "(type = 'baseline_waiver' AND target_artifact_id IS NOT NULL)", name="type_requirements"),
        _json("evidence_artifact_ids", "array"),
        _json("details", "object"),
        Index("ux_approvals_scope_once", "ticket_id", "scope_version", unique=True, sqlite_where=text("type = 'scope'")),
        Index("ux_approvals_uat_once", "candidate_id", "target_artifact_id", unique=True, sqlite_where=text("type = 'uat'")),
        Index("ux_approvals_release_once", "release_id", unique=True, sqlite_where=text("type = 'release'")),
        Index("ix_approvals_batch", "batch_id"),
    )


class Dependency(Base):
    """ticket_id waits for depends_on_ticket_id; the accepted_* columns pin what satisfied it."""

    __tablename__ = "dependencies"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    depends_on_ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False, default="waiting")
    accepted_scope_version: Mapped[int | None] = mapped_column(Integer)
    accepted_candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id"))
    integration_sha: Mapped[str | None] = mapped_column(String)
    revalidation: Mapped[Any] = mapped_column(Json, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    # Optimistic locking: every ORM UPDATE is 'WHERE revision = <loaded>' and bumps it; a stale
    # writer gets StaleDataError. A trigger enforces the same for raw SQL (migration 0001).
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        ForeignKeyConstraint(["depends_on_ticket_id", "accepted_scope_version"],
                             ["ticket_versions.ticket_id", "ticket_versions.version"]),
        UniqueConstraint("ticket_id", "depends_on_ticket_id"),
        CheckConstraint("ticket_id <> depends_on_ticket_id", name="not_self"),
        CheckConstraint(_in("state", DEPENDENCY_STATES), name="state"),
        CheckConstraint(f"integration_sha IS NULL OR ({_sha('integration_sha')})", name="integration_sha"),
        CheckConstraint(
            "(accepted_scope_version IS NULL AND accepted_candidate_id IS NULL AND integration_sha IS NULL) OR "
            "(accepted_scope_version IS NOT NULL AND accepted_candidate_id IS NOT NULL AND integration_sha IS NOT NULL)",
            name="pin_complete"),
        CheckConstraint("state <> 'satisfied' OR accepted_candidate_id IS NOT NULL", name="satisfied_has_pin"),
        CheckConstraint("revision >= 1", name="revision"),
        _json("revalidation", "object"),
    )


class Event(Base):
    """Append-only, ordered by an autoincrement cursor that is never reused."""

    __tablename__ = "events"
    cursor: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String)
    entity_type: Mapped[str | None] = mapped_column(String)
    entity_id: Mapped[str | None] = mapped_column(String)
    payload: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (
        CheckConstraint("length(trim(type)) > 0 AND length(trim(actor)) > 0", name="type_actor"),
        _json("payload", "object"),
        Index("ix_events_project_cursor", "project_id", "cursor"),
        {"sqlite_autoincrement": True},
    )


ENTITY_TABLES = ("projects", "tickets", "ticket_versions", "approvals", "dependencies", "messages",
                 "jobs", "candidates", "verifications", "artifacts", "releases", "events")


class LocalSession(Base):
    __tablename__ = "local_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class RuntimeCredential(Base):
    __tablename__ = "runtime_credentials"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    __table_args__ = (CheckConstraint("generation >= 1", name="generation"),)


class ApiCommand(Base):
    """Receipt committed atomically with the command, shared across processes and restarts."""
    __tablename__ = "api_commands"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    actor_key: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)
    response: Mapped[Any] = mapped_column(Json, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    __table_args__ = (UniqueConstraint("actor_key", "idempotency_key"), _json("response", "object"))


class Preview(Base):
    """On-demand preview of one verified target. Owned by the supervisor, never by a job.

    The row is the request/lifecycle record; the container, socket and loopback proxy are lifecycle
    metadata and can be recreated from the pinned artifacts. Approval never reads this table."""

    __tablename__ = "previews"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id"), nullable=False)
    scope_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_artifact_id: Mapped[str] = mapped_column(String, nullable=False)
    target_digest: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="requested")
    port: Mapped[int | None] = mapped_column(Integer)
    container_name: Mapped[str | None] = mapped_column(String)
    owner: Mapped[str | None] = mapped_column(String)
    stop_reason: Mapped[str | None] = mapped_column(String)
    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[Any] = mapped_column(Json, nullable=False, default=dict)
    requested_by: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)
    ready_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    stopped_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    __mapper_args__ = {"version_id_col": revision, "version_id_generator": lambda current: (current or 0) + 1}
    __table_args__ = (
        ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"]),
        CheckConstraint(_in("status", PREVIEW_STATUSES), name="status"),
        CheckConstraint("port IS NULL OR port BETWEEN 1 AND 65535", name="port"),
        CheckConstraint("revision >= 1 AND scope_version >= 1", name="counters"),
        _json("details", "object"),
        Index("ix_previews_candidate", "candidate_id"),
        Index("ix_previews_status", "status"),
    )


SYSTEM_TABLES = ("local_sessions", "runtime_credentials", "api_commands", "previews")
