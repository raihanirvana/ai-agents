"""Validate persisted identities/receipts, never turn model prose into QA evidence."""
import json
import re
from app.persistence import ArtifactUnavailable
from app.persistence.models import Artifact, Verification, TicketVersion
from .types import Invalid, Conflict


def digest(value, lengths=(64,)):
    if not isinstance(value, str) or len(value) not in lengths or not re.fullmatch("[0-9a-f]+", value):
        raise Invalid("invalid digest")
    return value


def artifact(session, store, project_id, artifact_id, kind=None):
    if not isinstance(artifact_id, str) or not artifact_id:
        raise Invalid("artifact ID is required")
    row = session.get(Artifact, artifact_id)
    if row is None or row.project_id != project_id or (kind and row.kind != kind):
        raise Invalid("artifact identity/kind/project mismatch")
    if row.availability != "available":
        raise ArtifactUnavailable(row.id, row.unavailable_reason)
    if row.storage == "file":
        store.read_bytes(session, row.id)  # flag alone is not sufficient
    return row


def document(session, store, artifact_id):
    try:
        value = json.loads(store.read_bytes(session, artifact_id))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Invalid("receipt must be a JSON object") from exc
    if not isinstance(value, dict):
        raise Invalid("receipt must be a JSON object")
    return value


def successful_execution(counts, mandatory, executed, commands):
    def ids(value):
        return (isinstance(value, list) and bool(value) and
                all(isinstance(x, str) and x.strip() for x in value) and len(set(value)) == len(value))
    return (isinstance(counts, dict) and ids(mandatory) and ids(executed) and
        set(mandatory).issubset(executed) and
        all(type(counts.get(k)) is int for k in ("executed", "discovered", "passed", "failed", "skipped")) and
        counts["executed"] == counts["discovered"] == counts["passed"] == len(executed) and
        counts["failed"] == counts["skipped"] == 0 and successful_commands(commands))


def successful_commands(commands):
    return (isinstance(commands, list) and bool(commands) and all(isinstance(c, dict) and
        type(c.get("exit_code")) is int and c["exit_code"] == 0 and isinstance(c.get("argv"), list) and
        c["argv"] and all(isinstance(x, str) and x for x in c["argv"]) for c in commands))


def target(session, store, candidate, target_id, target_digest):
    row = artifact(session, store, candidate.project_id, target_id, "target_manifest")
    if row.checksum != target_digest:
        raise Conflict("target digest changed")
    manifest = document(session, store, row.id)
    identity = {"project_id": candidate.project_id, "ticket_id": candidate.ticket_id,
                "candidate_id": candidate.id, "scope_version": candidate.scope_version,
                "source_sha": candidate.commit_sha, "base_sha": candidate.base_sha,
                "build_artifact_id": candidate.build_artifact_id}
    if any(manifest.get(k) != v for k, v in identity.items()):
        raise Conflict("manifest does not identify this candidate/build/scope")
    for key in ("build_digest", "runner_manifest_digest", "toolchain_digest", "config_digest",
                "fixture_digest", "migration_digest"):
        digest(manifest.get(key))
    build = artifact(session, store, candidate.project_id, candidate.build_artifact_id, "build_record")
    if document(session, store, build.id).get("build_digest") != manifest["build_digest"]:
        raise Conflict("build record does not match target")
    return row


def verification(session, store, candidate, verification_id):
    v = session.get(Verification, verification_id)
    if v is None or v.candidate_id != candidate.id or v.target_artifact_id != candidate.target_artifact_id or v.target_digest != candidate.target_digest:
        raise Conflict("verification belongs to another candidate/target")
    target(session, store, candidate, v.target_artifact_id, v.target_digest)
    for aid in v.evidence_artifact_ids:
        proof = artifact(session, store, candidate.project_id, aid)
        if proof.meta.get("producer") != "verification":
            raise Invalid("evidence must be published by verification service")
    manifest = document(session, store, v.target_artifact_id)
    if v.suite_digest != manifest["runner_manifest_digest"]:
        raise Conflict("verification suite differs from pinned runner manifest")
    if v.status != 'passed':
        from .qa_resolution import validate_persisted
        return validate_persisted(session, store, candidate, v.id)
    if v.results.get("fake_provider") is not False or v.results.get("infrastructure_failure") is not False:
        raise Invalid("QA requires non-fake successful harness execution")
    counts = v.counts
    mandatory = v.expected_test_ids
    executed = v.results.get("executed_test_ids", [])
    commands = v.results.get("commands", [])
    if not successful_execution(counts, mandatory, executed, commands) or not v.evidence_artifact_ids:
        raise Invalid("mandatory execution/counts/commands incomplete")
    scope = session.query(TicketVersion).filter_by(ticket_id=candidate.ticket_id, version=candidate.scope_version).one()
    for criterion in scope.uac:
        if criterion.get("mode", "automated") == "automated":
            coverage = v.uac_coverage.get(criterion["id"], [])
            if (not isinstance(coverage, list) or not coverage or
                any(not isinstance(x, str) for x in coverage) or not set(coverage).issubset(executed)):
                raise Invalid("automated UAC coverage incomplete")
    return v
