"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-05 00:45:42.881335
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


# --- Storage-level rules (SQLite triggers) -----------------------------------------------
# Triggers are part of this revision, not of the models: keep SQL free of colons (op.execute
# treats ':name' as a bind parameter). A later batch migration on a table must recreate
# that table's triggers.

IMMUTABLE_TABLES = ("ticket_versions", "approvals", "events", "messages", "verifications")
REVISIONED_TABLES = ("projects", "tickets", "candidates", "releases", "dependencies")
NO_DELETE_TABLES = ("projects", "tickets", "jobs", "candidates", "releases", "artifacts")
# (table, columns that may never change after insert)
FROZEN_COLUMNS = {
    "tickets": ("id", "project_id", "number", "created_at"),
    "artifacts": ("id", "project_id", "kind", "storage", "path", "checksum", "size_bytes",
                  "run_id", "metadata", "created_at"),
    "candidates": ("id", "project_id", "ticket_id", "scope_version", "job_id", "idempotency_key",
                   "commit_sha", "base_sha", "attempt_ref", "commit_artifact_id", "created_at"),
    "releases": ("id", "project_id", "scope_snapshot", "accepted_tip", "target_artifact_id",
                 "target_digest", "build_artifact_id", "commit_artifact_id", "context_artifact_id",
                 "evidence_artifact_ids", "created_at"),
}
ABORT = "SELECT RAISE(ABORT, '{}');"


def _trigger(name, when, table, body, condition=None):
    where = f" WHEN {condition}" if condition else ""
    op.execute(f"CREATE TRIGGER {name} {when} ON {table}{where} BEGIN {body} END")


def _artifact_guard(table, *, project, single=(), arrays=()):
    """New rows must reference existing, available artifacts of the same project."""
    checks = [f"NEW.{c} IS NOT NULL AND EXISTS (SELECT 1 FROM artifacts WHERE id = NEW.{c} AND "
              f"(availability <> 'available' OR project_id <> {project}))" for c in single]
    checks += [f"EXISTS (SELECT 1 FROM json_each(NEW.{c}) j LEFT JOIN artifacts a ON a.id = j.value "
               f"WHERE a.id IS NULL OR a.availability <> 'available' OR a.project_id <> {project})" for c in arrays]
    for index, check in enumerate(checks):
        _trigger(f"trg_{table}_artifact_guard_{index}", "BEFORE INSERT", table,
                 "SELECT RAISE(ABORT, 'referenced artifact is missing, unavailable or belongs to another project') "
                 f"WHERE {check};")


def _create_triggers() -> None:
    for table in IMMUTABLE_TABLES:
        _trigger(f"trg_{table}_no_update", "BEFORE UPDATE", table, ABORT.format(f"{table} rows are immutable"))
        _trigger(f"trg_{table}_no_delete", "BEFORE DELETE", table, ABORT.format(f"{table} rows cannot be deleted"))
    for table in NO_DELETE_TABLES:
        _trigger(f"trg_{table}_no_delete", "BEFORE DELETE", table, ABORT.format(f"{table} rows cannot be deleted"))
    for table, columns in FROZEN_COLUMNS.items():
        changed = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c in columns)
        _trigger(f"trg_{table}_frozen_columns", "BEFORE UPDATE", table,
                 ABORT.format(f"{table} identity columns are immutable"), changed)

    # tickets.current_version must exist and only moves forward (old versions never reactivate).
    _trigger("trg_tickets_version_insert", "BEFORE INSERT", "tickets",
             "SELECT RAISE(ABORT, 'current_version must reference an existing ticket version') WHERE NOT EXISTS "
             "(SELECT 1 FROM ticket_versions WHERE ticket_id = NEW.id AND version = NEW.current_version);",
             "NEW.current_version IS NOT NULL")
    _trigger("trg_tickets_version_update", "BEFORE UPDATE OF current_version", "tickets",
             "SELECT RAISE(ABORT, 'current_version must reference an existing ticket version') WHERE NOT EXISTS "
             "(SELECT 1 FROM ticket_versions WHERE ticket_id = NEW.id AND version = NEW.current_version);",
             "NEW.current_version IS NOT NULL AND NEW.current_version IS NOT OLD.current_version")
    _trigger("trg_tickets_version_forward", "BEFORE UPDATE OF current_version", "tickets",
             ABORT.format("current_version can only move forward"),
             "OLD.current_version IS NOT NULL AND (NEW.current_version IS NULL OR NEW.current_version < OLD.current_version)")

    # Artifact availability: cleaned files never come back; references must be usable.
    _trigger("trg_artifacts_cleaned_final", "BEFORE UPDATE OF availability", "artifacts",
             ABORT.format("a cleaned artifact cannot become available again"),
             "OLD.unavailable_reason = 'cleaned' AND NEW.availability = 'available'")
    _artifact_guard("approvals", project="NEW.project_id", single=("target_artifact_id",), arrays=("evidence_artifact_ids",))
    _artifact_guard("releases", project="NEW.project_id",
                    single=("target_artifact_id", "build_artifact_id", "commit_artifact_id", "context_artifact_id"),
                    arrays=("evidence_artifact_ids",))
    _artifact_guard("messages", project="NEW.project_id", arrays=("attachment_ids",))
    _artifact_guard("verifications", project="(SELECT project_id FROM candidates WHERE id = NEW.candidate_id)",
                    single=("target_artifact_id", "commit_artifact_id", "build_artifact_id", "context_artifact_id"),
                    arrays=("evidence_artifact_ids",))
    # A verification covers the candidate's CURRENT target and snapshots its commit/build/context.
    # A late result for an older target, or a snapshot that omits a reference, is refused.
    _trigger("trg_verifications_snapshot", "BEFORE INSERT", "verifications",
             "SELECT RAISE(ABORT, 'verification must snapshot the candidate current target, commit, build and context') "
             "WHERE NOT EXISTS (SELECT 1 FROM candidates c WHERE c.id = NEW.candidate_id "
             "AND c.target_artifact_id IS NEW.target_artifact_id AND c.target_digest IS NEW.target_digest "
             "AND c.commit_artifact_id IS NEW.commit_artifact_id AND c.build_artifact_id IS NEW.build_artifact_id "
             "AND c.context_artifact_id IS NEW.context_artifact_id);")
    candidate_refs = dict(project="NEW.project_id", single=("commit_artifact_id", "build_artifact_id", "target_artifact_id",
                                                           "context_artifact_id"), arrays=("evidence_artifact_ids",))
    _artifact_guard("candidates", **candidate_refs)
    # Re-pointing a candidate at another artifact is checked too (not unchanged values).
    for column in candidate_refs["single"]:
        _trigger(f"trg_candidates_artifact_guard_{column}_update", "BEFORE UPDATE OF " + column, "candidates",
                 "SELECT RAISE(ABORT, 'referenced artifact is missing, unavailable or belongs to another project') "
                 f"WHERE EXISTS (SELECT 1 FROM artifacts WHERE id = NEW.{column} AND "
                 "(availability <> 'available' OR project_id <> NEW.project_id));",
                 f"NEW.{column} IS NOT NULL AND NEW.{column} IS NOT OLD.{column}")
    _trigger("trg_candidates_evidence_guard_update", "BEFORE UPDATE OF evidence_artifact_ids", "candidates",
             "SELECT RAISE(ABORT, 'referenced artifact is missing, unavailable or belongs to another project') "
             "WHERE EXISTS (SELECT 1 FROM json_each(NEW.evidence_artifact_ids) j LEFT JOIN artifacts a ON a.id = j.value "
             "WHERE a.id IS NULL OR a.availability <> 'available' OR a.project_id <> NEW.project_id);",
             "NEW.evidence_artifact_ids IS NOT OLD.evidence_artifact_ids")

    # Candidate lifecycle. 'verified' and 'accepted' need evidence/approval for the CURRENT target.
    _trigger("trg_candidates_start_submitted", "BEFORE INSERT", "candidates",
             ABORT.format("a candidate starts as submitted"), "NEW.status <> 'submitted'")
    _trigger("trg_candidates_final_status", "BEFORE UPDATE OF status", "candidates",
             ABORT.format("accepted, rejected and superseded candidates are final"),
             "OLD.status IN ('accepted', 'rejected', 'superseded') AND NEW.status <> OLD.status")
    _trigger("trg_candidates_verified_needs_pass", "BEFORE UPDATE OF status", "candidates",
             "SELECT RAISE(ABORT, 'verified requires a passed verification for the current target') WHERE "
             "NEW.target_artifact_id IS NULL OR NOT EXISTS (SELECT 1 FROM verifications v WHERE v.candidate_id = NEW.id "
             "AND v.target_artifact_id = NEW.target_artifact_id AND v.target_digest = NEW.target_digest "
             "AND v.status = 'passed');",
             "NEW.status = 'verified' AND OLD.status <> 'verified'")
    _trigger("trg_candidates_accepted_needs_uat", "BEFORE UPDATE OF status", "candidates",
             "SELECT RAISE(ABORT, 'accepted requires a verified candidate and a UAT approval for the current target') "
             "WHERE OLD.status <> 'verified' OR NEW.target_artifact_id IS NULL OR NOT EXISTS (SELECT 1 FROM approvals ap "
             "WHERE ap.type = 'uat' AND ap.candidate_id = NEW.id AND ap.target_artifact_id = NEW.target_artifact_id "
             "AND ap.target_digest = NEW.target_digest);",
             "NEW.status = 'accepted' AND OLD.status <> 'accepted'")
    _trigger("trg_candidates_target_change", "BEFORE UPDATE OF target_artifact_id, target_digest", "candidates",
             ABORT.format("a new build target needs new QA and UAT; leave verified or accepted first"),
             "(OLD.target_artifact_id IS NOT NEW.target_artifact_id OR OLD.target_digest IS NOT NEW.target_digest) "
             "AND NEW.status IN ('verified', 'accepted')")

    # Approvals must name the exact current version, verified candidate target or release target.
    _trigger("trg_approvals_scope_current", "BEFORE INSERT", "approvals",
             "SELECT RAISE(ABORT, 'scope approval must name the ticket current version') WHERE NOT EXISTS "
             "(SELECT 1 FROM tickets t WHERE t.id = NEW.ticket_id AND t.current_version = NEW.scope_version "
             "AND t.project_id = NEW.project_id);",
             "NEW.type = 'scope'")
    _trigger("trg_approvals_uat_matches", "BEFORE INSERT", "approvals",
             "SELECT RAISE(ABORT, 'uat approval requires a verified candidate with the same target') WHERE NOT EXISTS "
             "(SELECT 1 FROM candidates c WHERE c.id = NEW.candidate_id AND c.status = 'verified' "
             "AND c.target_artifact_id = NEW.target_artifact_id AND c.target_digest = NEW.target_digest "
             "AND c.project_id = NEW.project_id);",
             "NEW.type = 'uat'")
    _trigger("trg_approvals_release_matches", "BEFORE INSERT", "approvals",
             "SELECT RAISE(ABORT, 'release approval requires a draft release with the same target') WHERE NOT EXISTS "
             "(SELECT 1 FROM releases r WHERE r.id = NEW.release_id AND r.status = 'draft' "
             "AND r.target_artifact_id = NEW.target_artifact_id AND r.target_digest = NEW.target_digest "
             "AND r.project_id = NEW.project_id);",
             "NEW.type = 'release'")

    # Releases: draft first; beyond draft only with an approval for exactly this target.
    _trigger("trg_releases_start_draft", "BEFORE INSERT", "releases",
             ABORT.format("a release starts as draft"), "NEW.status <> 'draft'")
    _trigger("trg_releases_need_approval", "BEFORE UPDATE OF status", "releases",
             "SELECT RAISE(ABORT, 'release needs an approval for exactly this target') WHERE NOT EXISTS "
             "(SELECT 1 FROM approvals ap WHERE ap.type = 'release' AND ap.release_id = NEW.id "
             "AND ap.target_artifact_id = NEW.target_artifact_id AND ap.target_digest = NEW.target_digest);",
             "NEW.status IN ('approved', 'exported', 'deployed') AND OLD.status = 'draft'")

    # Optimistic concurrency in storage: every update of these entities must bump revision by
    # exactly one. A writer that loaded an old revision therefore cannot overwrite a newer one
    # (jobs are fenced by lease_generation instead).
    for table in REVISIONED_TABLES:
        _trigger(f"trg_{table}_revision_step", "BEFORE UPDATE", table,
                 ABORT.format("revision must increase by exactly one on every update"),
                 "NEW.revision IS NOT OLD.revision + 1")

    # Job leases only move forward, so an old generation can never be restored.
    _trigger("trg_jobs_generation_forward", "BEFORE UPDATE OF lease_generation", "jobs",
             ABORT.format("lease generation cannot decrease"), "NEW.lease_generation < OLD.lease_generation")


def upgrade() -> None:
    op.create_table('projects',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('mode', sa.String(), nullable=False),
    sa.Column('brief', sa.Text(), nullable=False),
    sa.Column('brief_version', sa.Integer(), nullable=False),
    sa.Column('repo_ref', sa.String(), nullable=True),
    sa.Column('runner_manifest', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(runner_manifest) AND json_type(runner_manifest) = 'object'", name=op.f('ck_projects_runner_manifest_json')),
    sa.CheckConstraint("mode = 'new' OR repo_ref IS NOT NULL", name=op.f('ck_projects_existing_has_repo')),
    sa.CheckConstraint("mode IN ('new', 'existing')", name=op.f('ck_projects_mode')),
    sa.CheckConstraint('brief_version >= 1 AND revision >= 1', name=op.f('ck_projects_versions')),
    sa.CheckConstraint('length(trim(name)) > 0', name=op.f('ck_projects_name')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_projects'))
    )
    op.create_table('artifacts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('storage', sa.String(), nullable=False),
    sa.Column('path', sa.String(), nullable=True),
    sa.Column('checksum', sa.String(), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('run_id', sa.String(), nullable=True),
    sa.Column('metadata', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('availability', sa.String(), nullable=False),
    sa.Column('unavailable_reason', sa.String(), nullable=True),
    sa.Column('verified_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(availability = 'available' AND unavailable_reason IS NULL) OR (availability = 'unavailable' AND unavailable_reason IS NOT NULL AND unavailable_reason IN ('missing', 'corrupt', 'size_mismatch', 'unreadable', 'cleaned'))", name=op.f('ck_artifacts_availability')),
    sa.CheckConstraint("(storage = 'file' AND path IS NOT NULL AND size_bytes IS NOT NULL AND size_bytes >= 0) OR (storage = 'git' AND path IS NULL AND size_bytes IS NULL)", name=op.f('ck_artifacts_storage_shape')),
    sa.CheckConstraint("json_valid(metadata) AND json_type(metadata) = 'object'", name=op.f('ck_artifacts_metadata_json')),
    sa.CheckConstraint("kind IN ('build_record', 'target_manifest', 'evidence', 'log', 'screenshot', 'trace', 'context', 'git_commit', 'report', 'other')", name=op.f('ck_artifacts_kind')),
    sa.CheckConstraint("length(checksum) IN (40, 64) AND checksum NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_artifacts_checksum')),
    sa.CheckConstraint("storage IN ('file', 'git')", name=op.f('ck_artifacts_storage')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_artifacts_project_id_projects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_artifacts')),
    sa.UniqueConstraint('id', 'checksum', name=op.f('uq_artifacts_id_checksum'))
    )
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.create_index('ix_artifacts_project_kind', ['project_id', 'kind'], unique=False)
        batch_op.create_index('ux_artifacts_path', ['path'], unique=True, sqlite_where=sa.text('path IS NOT NULL'))

    op.create_table('events',
    sa.Column('cursor', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('actor', sa.String(), nullable=False),
    sa.Column('run_id', sa.String(), nullable=True),
    sa.Column('entity_type', sa.String(), nullable=True),
    sa.Column('entity_id', sa.String(), nullable=True),
    sa.Column('payload', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(payload) AND json_type(payload) = 'object'", name=op.f('ck_events_payload_json')),
    sa.CheckConstraint('length(trim(type)) > 0 AND length(trim(actor)) > 0', name=op.f('ck_events_type_actor')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_events_project_id_projects')),
    sa.PrimaryKeyConstraint('cursor', name=op.f('pk_events')),
    sqlite_autoincrement=True
    )
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.create_index('ix_events_project_cursor', ['project_id', 'cursor'], unique=False)

    op.create_table('tickets',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('phase', sa.String(), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('current_version', sa.Integer(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('blocker', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(blocker) AND json_type(blocker) = 'object'", name=op.f('ck_tickets_blocker_json')),
    sa.CheckConstraint("phase = 'draft' OR current_version IS NOT NULL", name=op.f('ck_tickets_phase_has_version')),
    sa.CheckConstraint("phase IN ('draft', 'scope_review', 'ready', 'development', 'technical_review', 'qa', 'uat', 'integrating', 'accepted', 'cancelled')", name=op.f('ck_tickets_phase')),
    sa.CheckConstraint('current_version IS NULL OR current_version >= 1', name=op.f('ck_tickets_current_version')),
    sa.CheckConstraint('length(trim(title)) > 0', name=op.f('ck_tickets_title')),
    sa.CheckConstraint('number >= 1 AND revision >= 1', name=op.f('ck_tickets_counters')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_tickets_project_id_projects')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tickets')),
    sa.UniqueConstraint('project_id', 'number', name=op.f('uq_tickets_project_id_number'))
    )
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.create_index('ix_tickets_project_phase', ['project_id', 'phase'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('thread_id', sa.String(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=True),
    sa.Column('sender', sa.String(), nullable=False),
    sa.Column('recipient', sa.String(), nullable=True),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('reply_to', sa.String(), nullable=True),
    sa.Column('idempotency_key', sa.String(), nullable=True),
    sa.Column('metadata', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('attachment_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(attachment_ids) AND json_type(attachment_ids) = 'array'", name=op.f('ck_messages_attachment_ids_json')),
    sa.CheckConstraint("json_valid(metadata) AND json_type(metadata) = 'object'", name=op.f('ck_messages_metadata_json')),
    sa.CheckConstraint("kind <> 'input_answer' OR reply_to IS NOT NULL", name=op.f('ck_messages_answer_replies')),
    sa.CheckConstraint("kind IN ('message', 'input_request', 'input_answer', 'system')", name=op.f('ck_messages_kind')),
    sa.CheckConstraint('seq >= 1', name=op.f('ck_messages_seq')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_messages_project_id_projects')),
    sa.ForeignKeyConstraint(['reply_to'], ['messages.id'], name=op.f('fk_messages_reply_to_messages')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_messages_ticket_id_tickets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_messages')),
    sa.UniqueConstraint('project_id', 'idempotency_key', name=op.f('uq_messages_project_id_idempotency_key')),
    sa.UniqueConstraint('thread_id', 'seq', name=op.f('uq_messages_thread_id_seq'))
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index('ix_messages_thread', ['thread_id', 'seq'], unique=False)
        batch_op.create_index('ux_messages_one_answer', ['reply_to'], unique=True, sqlite_where=sa.text("kind = 'input_answer'"))

    op.create_table('releases',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('scope_snapshot', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('accepted_tip', sa.String(), nullable=False),
    sa.Column('target_artifact_id', sa.String(), nullable=False),
    sa.Column('target_digest', sa.String(), nullable=False),
    sa.Column('build_artifact_id', sa.String(), nullable=False),
    sa.Column('commit_artifact_id', sa.String(), nullable=True),
    sa.Column('context_artifact_id', sa.String(), nullable=True),
    sa.Column('evidence_artifact_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('export_result', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('deployment_result', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(deployment_result) AND json_type(deployment_result) = 'object'", name=op.f('ck_releases_deployment_result_json')),
    sa.CheckConstraint("json_valid(evidence_artifact_ids) AND json_type(evidence_artifact_ids) = 'array'", name=op.f('ck_releases_evidence_artifact_ids_json')),
    sa.CheckConstraint("json_valid(export_result) AND json_type(export_result) = 'object'", name=op.f('ck_releases_export_result_json')),
    sa.CheckConstraint("json_valid(scope_snapshot) AND json_type(scope_snapshot) = 'array'", name=op.f('ck_releases_scope_snapshot_json')),
    sa.CheckConstraint("length(accepted_tip) IN (40, 64) AND accepted_tip NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_releases_accepted_tip')),
    sa.CheckConstraint("status IN ('draft', 'approved', 'exported', 'deployed', 'failed')", name=op.f('ck_releases_status')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_releases_revision')),
    sa.ForeignKeyConstraint(['build_artifact_id'], ['artifacts.id'], name=op.f('fk_releases_build_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['commit_artifact_id'], ['artifacts.id'], name=op.f('fk_releases_commit_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['context_artifact_id'], ['artifacts.id'], name=op.f('fk_releases_context_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_releases_project_id_projects')),
    sa.ForeignKeyConstraint(['target_artifact_id', 'target_digest'], ['artifacts.id', 'artifacts.checksum'], name=op.f('fk_releases_target_artifact_id_target_digest_artifacts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_releases'))
    )
    op.create_table('ticket_versions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('uac', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('content_digest', sa.String(), nullable=False),
    sa.Column('created_by', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(uac) AND json_type(uac) = 'array'", name=op.f('ck_ticket_versions_uac_json')),
    sa.CheckConstraint("length(content_digest) IN (40, 64) AND content_digest NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_ticket_versions_content_digest')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_ticket_versions_version')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_ticket_versions_ticket_id_tickets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ticket_versions')),
    sa.UniqueConstraint('ticket_id', 'version', name=op.f('uq_ticket_versions_ticket_id_version'))
    )
    op.create_table('jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=True),
    sa.Column('scope_version', sa.Integer(), nullable=True),
    sa.Column('lane', sa.String(), nullable=False),
    sa.Column('stage', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('parent_job_id', sa.String(), nullable=True),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('lease_owner', sa.String(), nullable=True),
    sa.Column('lease_generation', sa.Integer(), nullable=False),
    sa.Column('lease_expires_at', sa.DateTime(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(), nullable=True),
    sa.Column('waiting_request_id', sa.String(), nullable=True),
    sa.Column('runtime_ref', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('context_artifact_id', sa.String(), nullable=True),
    sa.Column('limits', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('usage', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('result', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("json_valid(limits) AND json_type(limits) = 'object'", name=op.f('ck_jobs_limits_json')),
    sa.CheckConstraint("json_valid(result) AND json_type(result) = 'object'", name=op.f('ck_jobs_result_json')),
    sa.CheckConstraint("json_valid(runtime_ref) AND json_type(runtime_ref) = 'object'", name=op.f('ck_jobs_runtime_ref_json')),
    sa.CheckConstraint("json_valid(usage) AND json_type(usage) = 'object'", name=op.f('ck_jobs_usage_json')),
    sa.CheckConstraint("lane IN ('interactive', 'execution')", name=op.f('ck_jobs_lane')),
    sa.CheckConstraint("status <> 'running' OR (lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)", name=op.f('ck_jobs_running_has_lease')),
    sa.CheckConstraint("status <> 'waiting_input' OR waiting_request_id IS NOT NULL", name=op.f('ck_jobs_waiting_has_request')),
    sa.CheckConstraint("status IN ('queued', 'running', 'waiting_input', 'waiting_quota', 'stopped', 'failed', 'cancelled', 'succeeded')", name=op.f('ck_jobs_status')),
    sa.CheckConstraint('(ticket_id IS NULL) = (scope_version IS NULL)', name=op.f('ck_jobs_scope_pair')),
    sa.CheckConstraint('attempt >= 1 AND lease_generation >= 0 AND revision >= 1', name=op.f('ck_jobs_counters')),
    sa.ForeignKeyConstraint(['context_artifact_id'], ['artifacts.id'], name=op.f('fk_jobs_context_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['parent_job_id'], ['jobs.id'], name=op.f('fk_jobs_parent_job_id_jobs')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_jobs_project_id_projects')),
    sa.ForeignKeyConstraint(['ticket_id', 'scope_version'], ['ticket_versions.ticket_id', 'ticket_versions.version'], name=op.f('fk_jobs_ticket_id_scope_version_ticket_versions')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_jobs_ticket_id_tickets')),
    sa.ForeignKeyConstraint(['waiting_request_id'], ['messages.id'], name=op.f('fk_jobs_waiting_request_id_messages')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('project_id', 'idempotency_key', name=op.f('uq_jobs_project_id_idempotency_key'))
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_claim', ['status', 'lane', 'created_at'], unique=False)
        batch_op.create_index('ix_jobs_scope', ['ticket_id', 'scope_version'], unique=False)

    op.create_table('candidates',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=False),
    sa.Column('scope_version', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=True),
    sa.Column('idempotency_key', sa.String(), nullable=False),
    sa.Column('commit_sha', sa.String(), nullable=False),
    sa.Column('base_sha', sa.String(), nullable=False),
    sa.Column('attempt_ref', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('commit_artifact_id', sa.String(), nullable=True),
    sa.Column('build_artifact_id', sa.String(), nullable=True),
    sa.Column('target_artifact_id', sa.String(), nullable=True),
    sa.Column('target_digest', sa.String(), nullable=True),
    sa.Column('context_artifact_id', sa.String(), nullable=True),
    sa.Column('evidence_artifact_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('preview', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('integration', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('integrated_sha', sa.String(), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("integrated_sha IS NULL OR (length(integrated_sha) IN (40, 64) AND integrated_sha NOT GLOB '*[^0-9a-f]*')", name=op.f('ck_candidates_integrated_sha')),
    sa.CheckConstraint("json_valid(evidence_artifact_ids) AND json_type(evidence_artifact_ids) = 'array'", name=op.f('ck_candidates_evidence_artifact_ids_json')),
    sa.CheckConstraint("json_valid(integration) AND json_type(integration) = 'object'", name=op.f('ck_candidates_integration_json')),
    sa.CheckConstraint("json_valid(preview) AND json_type(preview) = 'object'", name=op.f('ck_candidates_preview_json')),
    sa.CheckConstraint("length(base_sha) IN (40, 64) AND base_sha NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_candidates_base_sha')),
    sa.CheckConstraint("length(commit_sha) IN (40, 64) AND commit_sha NOT GLOB '*[^0-9a-f]*'", name=op.f('ck_candidates_commit_sha')),
    sa.CheckConstraint("status <> 'accepted' OR integrated_sha IS NOT NULL", name=op.f('ck_candidates_accepted_integrated')),
    sa.CheckConstraint("status IN ('submitted', 'review_approved', 'verified', 'accepted', 'rejected', 'superseded')", name=op.f('ck_candidates_status')),
    sa.CheckConstraint('(target_artifact_id IS NULL) = (target_digest IS NULL)', name=op.f('ck_candidates_target_pair')),
    sa.CheckConstraint('scope_version >= 1 AND revision >= 1', name=op.f('ck_candidates_counters')),
    sa.ForeignKeyConstraint(['build_artifact_id'], ['artifacts.id'], name=op.f('fk_candidates_build_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['commit_artifact_id'], ['artifacts.id'], name=op.f('fk_candidates_commit_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['context_artifact_id'], ['artifacts.id'], name=op.f('fk_candidates_context_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_candidates_job_id_jobs')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_candidates_project_id_projects')),
    sa.ForeignKeyConstraint(['target_artifact_id', 'target_digest'], ['artifacts.id', 'artifacts.checksum'], name=op.f('fk_candidates_target_artifact_id_target_digest_artifacts')),
    sa.ForeignKeyConstraint(['ticket_id', 'scope_version'], ['ticket_versions.ticket_id', 'ticket_versions.version'], name=op.f('fk_candidates_ticket_id_scope_version_ticket_versions')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_candidates_ticket_id_tickets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_candidates')),
    sa.UniqueConstraint('project_id', 'idempotency_key', name=op.f('uq_candidates_project_id_idempotency_key'))
    )
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.create_index('ix_candidates_scope', ['ticket_id', 'scope_version'], unique=False)

    op.create_table('approvals',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('user_id', sa.String(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=True),
    sa.Column('scope_version', sa.Integer(), nullable=True),
    sa.Column('candidate_id', sa.String(), nullable=True),
    sa.Column('release_id', sa.String(), nullable=True),
    sa.Column('target_artifact_id', sa.String(), nullable=True),
    sa.Column('target_digest', sa.String(), nullable=True),
    sa.Column('evidence_artifact_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('batch_id', sa.String(), nullable=True),
    sa.Column('details', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("(type = 'scope' AND ticket_id IS NOT NULL AND scope_version IS NOT NULL) OR (type = 'uat' AND candidate_id IS NOT NULL AND target_artifact_id IS NOT NULL) OR (type = 'release' AND release_id IS NOT NULL AND target_artifact_id IS NOT NULL) OR (type = 'baseline_waiver' AND target_artifact_id IS NOT NULL)", name=op.f('ck_approvals_type_requirements')),
    sa.CheckConstraint("json_valid(details) AND json_type(details) = 'object'", name=op.f('ck_approvals_details_json')),
    sa.CheckConstraint("json_valid(evidence_artifact_ids) AND json_type(evidence_artifact_ids) = 'array'", name=op.f('ck_approvals_evidence_artifact_ids_json')),
    sa.CheckConstraint("type IN ('scope', 'uat', 'release', 'baseline_waiver')", name=op.f('ck_approvals_type')),
    sa.CheckConstraint('(target_artifact_id IS NULL) = (target_digest IS NULL)', name=op.f('ck_approvals_target_pair')),
    sa.CheckConstraint('(ticket_id IS NULL) = (scope_version IS NULL)', name=op.f('ck_approvals_scope_pair')),
    sa.CheckConstraint('length(trim(user_id)) > 0', name=op.f('ck_approvals_user_id')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_approvals_candidate_id_candidates')),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_approvals_project_id_projects')),
    sa.ForeignKeyConstraint(['release_id'], ['releases.id'], name=op.f('fk_approvals_release_id_releases')),
    sa.ForeignKeyConstraint(['target_artifact_id', 'target_digest'], ['artifacts.id', 'artifacts.checksum'], name=op.f('fk_approvals_target_artifact_id_target_digest_artifacts')),
    sa.ForeignKeyConstraint(['ticket_id', 'scope_version'], ['ticket_versions.ticket_id', 'ticket_versions.version'], name=op.f('fk_approvals_ticket_id_scope_version_ticket_versions')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_approvals_ticket_id_tickets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_approvals'))
    )
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.create_index('ix_approvals_batch', ['batch_id'], unique=False)
        batch_op.create_index('ux_approvals_release_once', ['release_id'], unique=True, sqlite_where=sa.text("type = 'release'"))
        batch_op.create_index('ux_approvals_scope_once', ['ticket_id', 'scope_version'], unique=True, sqlite_where=sa.text("type = 'scope'"))
        batch_op.create_index('ux_approvals_uat_once', ['candidate_id', 'target_artifact_id'], unique=True, sqlite_where=sa.text("type = 'uat'"))

    op.create_table('dependencies',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('ticket_id', sa.String(), nullable=False),
    sa.Column('depends_on_ticket_id', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('accepted_scope_version', sa.Integer(), nullable=True),
    sa.Column('accepted_candidate_id', sa.String(), nullable=True),
    sa.Column('integration_sha', sa.String(), nullable=True),
    sa.Column('revalidation', sa.JSON(none_as_null=True), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("integration_sha IS NULL OR (length(integration_sha) IN (40, 64) AND integration_sha NOT GLOB '*[^0-9a-f]*')", name=op.f('ck_dependencies_integration_sha')),
    sa.CheckConstraint("json_valid(revalidation) AND json_type(revalidation) = 'object'", name=op.f('ck_dependencies_revalidation_json')),
    sa.CheckConstraint("state <> 'satisfied' OR accepted_candidate_id IS NOT NULL", name=op.f('ck_dependencies_satisfied_has_pin')),
    sa.CheckConstraint("state IN ('waiting', 'satisfied', 'needs_revalidation')", name=op.f('ck_dependencies_state')),
    sa.CheckConstraint('(accepted_scope_version IS NULL AND accepted_candidate_id IS NULL AND integration_sha IS NULL) OR (accepted_scope_version IS NOT NULL AND accepted_candidate_id IS NOT NULL AND integration_sha IS NOT NULL)', name=op.f('ck_dependencies_pin_complete')),
    sa.CheckConstraint('revision >= 1', name=op.f('ck_dependencies_revision')),
    sa.CheckConstraint('ticket_id <> depends_on_ticket_id', name=op.f('ck_dependencies_not_self')),
    sa.ForeignKeyConstraint(['accepted_candidate_id'], ['candidates.id'], name=op.f('fk_dependencies_accepted_candidate_id_candidates')),
    sa.ForeignKeyConstraint(['depends_on_ticket_id', 'accepted_scope_version'], ['ticket_versions.ticket_id', 'ticket_versions.version'], name=op.f('fk_dependencies_depends_on_ticket_id_accepted_scope_version_ticket_versions')),
    sa.ForeignKeyConstraint(['depends_on_ticket_id'], ['tickets.id'], name=op.f('fk_dependencies_depends_on_ticket_id_tickets')),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], name=op.f('fk_dependencies_ticket_id_tickets')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dependencies')),
    sa.UniqueConstraint('ticket_id', 'depends_on_ticket_id', name=op.f('uq_dependencies_ticket_id_depends_on_ticket_id'))
    )
    op.create_table('verifications',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('candidate_id', sa.String(), nullable=False),
    sa.Column('target_artifact_id', sa.String(), nullable=False),
    sa.Column('target_digest', sa.String(), nullable=False),
    sa.Column('commit_artifact_id', sa.String(), nullable=True),
    sa.Column('build_artifact_id', sa.String(), nullable=True),
    sa.Column('context_artifact_id', sa.String(), nullable=True),
    sa.Column('evidence_id', sa.String(), nullable=False),
    sa.Column('suite_digest', sa.String(), nullable=False),
    sa.Column('expected_test_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('counts', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('uac_coverage', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('results', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('evidence_artifact_ids', sa.JSON(none_as_null=True), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("json_valid(counts) AND json_type(counts) = 'object'", name=op.f('ck_verifications_counts_json')),
    sa.CheckConstraint("json_valid(evidence_artifact_ids) AND json_type(evidence_artifact_ids) = 'array'", name=op.f('ck_verifications_evidence_artifact_ids_json')),
    sa.CheckConstraint("json_valid(expected_test_ids) AND json_type(expected_test_ids) = 'array'", name=op.f('ck_verifications_expected_test_ids_json')),
    sa.CheckConstraint("json_valid(results) AND json_type(results) = 'object'", name=op.f('ck_verifications_results_json')),
    sa.CheckConstraint("json_valid(uac_coverage) AND json_type(uac_coverage) = 'object'", name=op.f('ck_verifications_uac_coverage_json')),
    sa.CheckConstraint("status <> 'passed' OR (ifnull(json_extract(counts, '$.executed'), 0) > 0 AND ifnull(json_extract(counts, '$.discovered'), -1) = ifnull(json_extract(counts, '$.executed'), -2) AND ifnull(json_extract(counts, '$.passed'), -1) = ifnull(json_extract(counts, '$.executed'), -2) AND ifnull(json_extract(counts, '$.failed'), 1) = 0 AND ifnull(json_extract(counts, '$.skipped'), 1) = 0 AND json_array_length(expected_test_ids) > 0 AND json_array_length(evidence_artifact_ids) > 0)", name=op.f('ck_verifications_passed_has_execution')),
    sa.CheckConstraint("status IN ('passed', 'failed', 'incomplete')", name=op.f('ck_verifications_status')),
    sa.ForeignKeyConstraint(['build_artifact_id'], ['artifacts.id'], name=op.f('fk_verifications_build_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], name=op.f('fk_verifications_candidate_id_candidates')),
    sa.ForeignKeyConstraint(['commit_artifact_id'], ['artifacts.id'], name=op.f('fk_verifications_commit_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['context_artifact_id'], ['artifacts.id'], name=op.f('fk_verifications_context_artifact_id_artifacts')),
    sa.ForeignKeyConstraint(['target_artifact_id', 'target_digest'], ['artifacts.id', 'artifacts.checksum'], name=op.f('fk_verifications_target_artifact_id_target_digest_artifacts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_verifications')),
    sa.UniqueConstraint('evidence_id', name=op.f('uq_verifications_evidence_id'))
    )
    with op.batch_alter_table('verifications', schema=None) as batch_op:
        batch_op.create_index('ix_verifications_candidate', ['candidate_id'], unique=False)

    _create_triggers()


def downgrade() -> None:
    with op.batch_alter_table('verifications', schema=None) as batch_op:
        batch_op.drop_index('ix_verifications_candidate')

    op.drop_table('verifications')
    op.drop_table('dependencies')
    with op.batch_alter_table('approvals', schema=None) as batch_op:
        batch_op.drop_index('ux_approvals_uat_once', sqlite_where=sa.text("type = 'uat'"))
        batch_op.drop_index('ux_approvals_scope_once', sqlite_where=sa.text("type = 'scope'"))
        batch_op.drop_index('ux_approvals_release_once', sqlite_where=sa.text("type = 'release'"))
        batch_op.drop_index('ix_approvals_batch')

    op.drop_table('approvals')
    with op.batch_alter_table('candidates', schema=None) as batch_op:
        batch_op.drop_index('ix_candidates_scope')

    op.drop_table('candidates')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_scope')
        batch_op.drop_index('ix_jobs_claim')

    op.drop_table('jobs')
    op.drop_table('ticket_versions')
    op.drop_table('releases')
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index('ux_messages_one_answer', sqlite_where=sa.text("kind = 'input_answer'"))
        batch_op.drop_index('ix_messages_thread')

    op.drop_table('messages')
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_index('ix_tickets_project_phase')

    op.drop_table('tickets')
    with op.batch_alter_table('events', schema=None) as batch_op:
        batch_op.drop_index('ix_events_project_cursor')

    op.drop_table('events')
    with op.batch_alter_table('artifacts', schema=None) as batch_op:
        batch_op.drop_index('ux_artifacts_path', sqlite_where=sa.text('path IS NOT NULL'))
        batch_op.drop_index('ix_artifacts_project_kind')

    op.drop_table('artifacts')
    op.drop_table('projects')
