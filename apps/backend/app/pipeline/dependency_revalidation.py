"""Recheck an integrated upstream contract before unblocking downstream work.

This service executes the accepted upstream suite on the current accepted
tree. It cannot approve new scope, UAT, release, or waive failed checks.
"""
import json

from sqlalchemy import select

from app.domain import Actor
from app.persistence.models import Approval, Artifact, Candidate, Dependency, Job, Project, Ticket, TicketVersion
from app.persistence.transactions import bind_service
from app.persistence.artifacts import ArtifactError
from app.workers.runtime import Outcome
from app.workspace import fsutil
from .contracts import QaPlan, digest_of


def pending_contract(s, ticket, project):
    if (ticket.phase not in ('ready', 'development', 'technical_review', 'qa')
            or (ticket.blocker or {}).get('reason') != 'dependency_revalidation'
            or not s.scalar(select(Approval.id).where(Approval.ticket_id == ticket.id,
                Approval.type == 'scope', Approval.scope_version == ticket.current_version))):
        return None
    edges = s.scalars(select(Dependency).where(Dependency.ticket_id == ticket.id,
        Dependency.state == 'needs_revalidation').order_by(Dependency.id))
    for edge in edges:
        upstream = s.get(Ticket, edge.depends_on_ticket_id)
        candidate = s.get(Candidate, upstream.workflow.get('accepted_candidate_id')) if upstream else None
        if (not upstream or upstream.project_id != project.id or upstream.phase != 'accepted'
                or not candidate or candidate.ticket_id != upstream.id or candidate.status != 'accepted'
                or not candidate.integrated_sha or not candidate.target_artifact_id
                or not (edge.revalidation or {}).get('request_id')):
            continue
        if s.scalar(select(Dependency.id).where(Dependency.ticket_id == upstream.id,
                                              Dependency.state != 'satisfied')):
            continue  # Recover transitive contracts in dependency order.
        return {'task': 'revalidate_dependency', 'dependency_id': edge.id,
                'revalidation_id': edge.revalidation['request_id'], 'upstream_id': upstream.id,
                'upstream_candidate_id': candidate.id, 'upstream_target_id': candidate.target_artifact_id,
                'upstream_target_digest': candidate.target_digest, 'base_sha': project.workflow['accepted_tip'],
                'manifest_digest': digest_of(project.workflow['pipeline']['manifest'])}
    return None


def run(runtime, ctx, identity):
    try:
        return _run(runtime, ctx, identity)
    except ArtifactError as exc:
        return Outcome('failed', {'failure_kind': 'evidence_unavailable'},
            error='Bukti dependency tidak tersedia: ' + runtime.redactor.redact(str(exc))[:400], retryable=False)


def _run(runtime, ctx, identity):
    payload = ctx.job['runtime_ref']['payload']
    with runtime.db.read() as s:
        ctx.queue.verify_identity(s, identity)
        saved = s.get(Job, identity['job_id']).runtime_ref.get('dependency_revalidation_result')
        if saved:
            return Outcome('succeeded', saved)
        ticket = s.get(Ticket, identity['ticket_id'])
        project = s.get(Project, identity['project_id'])
        if pending_contract(s, ticket, project) != payload:
            return Outcome('failed', error='Dependency/scope/base berubah; job revalidasi ini sudah usang.', retryable=False)
        revision = ticket.revision
        target_row = s.get(Artifact, payload['upstream_target_id'])
        if (not target_row or target_row.project_id != project.id
                or target_row.checksum != payload['upstream_target_digest']):
            return Outcome('failed', error='Target upstream yang disetujui tidak tersedia.', retryable=False)
        target = json.loads(runtime.store.read_bytes(s, target_row.id))
        suite = QaPlan.model_validate(json.loads(runtime.store.read_bytes(s, target['suite_artifact_id'])))
        if suite.digest != target['runner_manifest_digest']:
            raise ValueError('suite dependency tidak cocok dengan target upstream yang disetujui')
        candidate = s.get(Candidate, payload['upstream_candidate_id'])
        version = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == candidate.ticket_id,
                                                      TicketVersion.version == candidate.scope_version))
        suite.check_criteria(version.uac)
    if runtime.fake:
        return Outcome('failed', error='Revalidasi dependency memerlukan harness nyata; hasil fake tidak berlaku.', retryable=False)
    manifest, base = runtime.workspace.configuration(identity)
    baseline = runtime.workspace.base_build(ctx)
    gate = baseline.get('gate', {})
    if (base != payload['base_sha'] or baseline.get('base_sha') != base
            or baseline.get('status') != 'built' or gate.get('status') != 'passed'
            or gate.get('infrastructure_failure') is not False):
        return Outcome('failed', {'baseline_artifact_id': baseline.get('artifact_id')},
            error='Accepted base belum lulus install/build/tes; dependency tetap diblokir.', retryable=False)
    runner = runtime.workspace.harness.identity()
    target_digest = digest_of({'base_sha': base, 'execution_manifest': manifest.to_dict(),
        'build_digest': fsutil.sha256_tree(baseline['site'], fsutil.scan_tree(baseline['site'])),
        'runner': runner, 'suite_digest': suite.digest, 'dependency': payload})
    from .harness import legacy_locators
    proof = runtime.workspace.harness.run(ctx, baseline['site'], target_digest, suite,
        runtime.workspace.harness.sandbox.image_id(manifest.image), expected_runner=runner,
        legacy_locators=legacy_locators(target))
    proof.pop('diagnostics', None)
    receipt = {**proof, 'fake_provider': False, 'uac_changed': False,
        'revalidation_id': payload['revalidation_id'], 'ticket_id': identity['ticket_id'],
        'scope_version': identity['scope_version'], 'upstream_candidate_id': payload['upstream_candidate_id'],
        'upstream_target_digest': payload['upstream_target_digest'], 'base_sha': base,
        'execution_manifest': manifest.to_dict(), 'expected_test_ids': [test.id for test in suite.tests],
        'executed_test_ids': proof.get('executed', []), 'baseline_artifact_id': baseline['artifact_id'],
        'repository_gate': gate}
    with runtime.db.write() as s:
        ctx.queue.verify_identity(s, identity)
        ticket = s.get(Ticket, identity['ticket_id'])
        project = s.get(Project, identity['project_id'])
        if ticket.revision != revision or pending_contract(s, ticket, project) != payload:
            return Outcome('failed', error='Dependency/scope/base berubah selama pengujian; bukti lama tidak diterapkan.', retryable=False)
        artifact = runtime.store.put_json(s, project_id=project.id, kind='report',
            name='dependency-revalidation.json', document=receipt, meta={'producer': 'verification'})
        runtime.workspace._post(s, identity,
            'dependency-revalidation:' + identity['job_id'] + ':' + str(identity['generation']),
            'Dependency contract checks: ' + proof['status'], [baseline['artifact_id'], artifact.id],
            'dependency_revalidation', upstream_ticket_id=payload['upstream_id'])
        if proof['status'] != 'passed':
            return Outcome('failed', {'report_artifact_id': artifact.id},
                error='Kontrak dependency gagal atau belum lengkap. Periksa laporan; dependency tetap diblokir.', retryable=False)
        bind_service(runtime.workflow, s).revalidate_dependency(
            Actor('service:dependency-revalidation', 'verification', project.id),
            ticket.id, ticket.revision, payload['upstream_id'], artifact.id)
        result = {'report_artifact_id': artifact.id, 'dependency_id': payload['dependency_id'], 'fake': False}
        job = s.get(Job, identity['job_id'])
        job.runtime_ref = {**job.runtime_ref, 'dependency_revalidation_result': result}
        return Outcome('succeeded', result)
