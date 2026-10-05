"""Durable onboarding job: independent import, sandbox baseline, fenced atomic publication."""
import uuid
from pathlib import Path
from app.persistence import apply_change, EventSpec, append_message
from app.persistence.models import Project, Job
from app.persistence.columns import utcnow
from app.persistence.transactions import bind_service
from app.workers.runtime import Outcome
from app.workspace.errors import WorkspaceError
from app.workspace.manifest import parse_manifest, digest_of
from app.workspace.runspec import RunRef
from app.pipeline.workspace import FencedWorkspace, cleanup_workspace, ProductWorkspace
from app.pipeline.gates import node_gate
from .source import import_source, inspect_source


class OnboardingRuntime:
    name = 'onboarding'

    def __init__(self, db, store, root, redactor):
        self.db, self.store, self.root, self.redactor = db, store, Path(root).resolve(), redactor

    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()

    def reconcile(self, snapshot):
        from app.workspace import WorkspaceSupervisor
        with self.db.read() as s:
            job = s.get(Job, snapshot['id'])
            if job is None or job.status == 'running' and job.lease_expires_at and job.lease_expires_at > utcnow():
                return False
        generation = snapshot['runtime_ref']['cleanup']['generation']
        sup = WorkspaceSupervisor(self.root)
        for resource in snapshot['runtime_ref'].get('resources', []):
            if (resource.get('kind') != 'pipeline_workspace' or resource.get('generation') != generation or
                resource.get('owner') != snapshot['id'] + ':' + str(generation) or
                resource.get('project_id') != snapshot['project_id']):
                return False
            cleanup_workspace(sup, RunRef(resource['project_id'], resource['run_id']),
                              snapshot['id'], resource['generation'])
        return True

    def run(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        if identity['role'] != 'technical-lead' or identity['ticket_id'] is not None or ctx.job['stage'] != 'onboarding':
            return Outcome('failed', error='invalid onboarding job identity')
        payload = ctx.job['runtime_ref']['payload']
        manifest = parse_manifest(payload['manifest'])
        sup = FencedWorkspace(self.root, ctx)
        report = {'status': 'blocked', 'baseline': {}, 'commands': [], 'policy':
                  'No source commands on host; source is never mounted. Repository instructions cannot grant permissions.'}
        local = None
        try:
            with self.db.read() as s:
                p = s.get(Project, identity['project_id'])
                if p.mode != 'existing' or p.workflow.get('accepted_tip'):
                    raise ValueError('existing project is already initialized')
                source = p.repo_ref
                patch = self.store.read_bytes(s, payload['patch_artifact_id']) if payload.get('patch_artifact_id') else None
            inspected, _ = ctx.tool_call('inspect_source', lambda: inspect_source(sup.broker(p.id), source))
            report.update(inspected)
            # This MVP deliberately rejects stateful/server runners until migration/service support is verified.
            if manifest.migrations != {'id': 'none'}:
                raise ValueError('unsupported migrations/services: existing runner supports static projects with migrations none')
            imported = ctx.tool_call('independent_git_import', lambda: import_source(sup, p.id, source,
                identity['root_job_id'], payload.get('source_sha'), patch, check=lambda: ctx.queue.verify(ctx.lease),
                replace_uninitialized=True))  # the project has no accepted base yet (checked above)
            report.update(imported)
            ctx.queue.verify(ctx.lease)
            run_id = 'run-' + uuid.uuid4().hex[:12]
            ref = RunRef(p.id, run_id)
            descriptor = {'kind': 'pipeline_workspace', 'project_id': p.id, 'run_id': run_id,
                          'generation': identity['generation'], 'owner': ctx.tag}
            ctx.queue.register_resource(ctx.lease, descriptor)
            ctx.add_stopper(lambda: cleanup_workspace(sup, ref, ctx.lease.job_id, ctx.lease.generation), resource=descriptor)
            started = sup.start_attempt(p.id, ticket_id='onboarding', scope_version=1, role='developer',
                attempt=identity['attempt'], generation=identity['generation'], lease_id=identity['lease_owner'],
                manifest=manifest, run_id=run_id, allow_install_egress=True,
                provenance={'created_by': 'product-onboarding', 'job_id': identity['job_id']})
            local = sup._store(ref)
            actual_toolchain = {}
            for tool, argv in (('node', ['node', '--version']), ('npm', ['npm', '--version'])):
                probe = sup.run_command(ref, started.credential, argv, timeout_s=20)
                actual = probe.stdout.decode(errors='replace').strip().removeprefix('v')
                if probe.exit_code != 0 or (tool in manifest.toolchain and actual != manifest.toolchain[tool]):
                    raise ValueError('declared toolchain.' + tool + ' differs from sandbox version: ' + actual)
                actual_toolchain[tool] = actual
            report['toolchain'] = actual_toolchain
            for phase in ('install', 'build', 'test'):
                result = sup.run_phase(ref, started.credential, phase)
                infra = result.timed_out or result.cancelled or result.oom_killed or result.truncated
                report['baseline'][phase] = {'exit_code': result.exit_code, 'infrastructure_failure': infra}
                if phase == 'test':
                    image = sup.sandbox.image_id(manifest.image)
                    gate = {**node_gate(result.stdout, result.stderr, result.exit_code),
                        'infrastructure_failure': infra,
                        'environment_digest': digest_of({'image': image, 'manifest': manifest.effective_config()})}
                    report['baseline']['gate'] = gate
                    if gate['status'] == 'incomplete' or infra:
                        raise ValueError('baseline test evidence incomplete/infrastructure failure; flat Node TAP required')
                elif result.exit_code != 0 or infra:
                    raise ValueError('baseline ' + phase + ' failed; fix runner/source before onboarding')
            # Baseline build is supervisor-only evidence, never a candidate/QA/UAT submission.
            built = sup.build_baseline(ref)
            smoke = sup.smoke_target(ref, built['build_id'])
            report['baseline']['start'] = smoke
            if not smoke['healthy']:
                raise ValueError('baseline start/health contract failed')
            report['status'] = 'ready' if gate['status'] == 'passed' else 'ready_with_baseline_failures'
            report['lead_plan'] = ('Use the validated static React/Vite runner. Preserve baseline regression test IDs; '
                'each feature requires approved scope, lead plan/review, independent QA and user UAT. ' +
                ('Required repo checks passed.' if gate['status'] == 'passed' else
                 'Baseline tests failed; fix them or request an exact per-scope user waiver. No waiver granted by onboarding.'))
        except (ValueError, OSError, WorkspaceError) as exc:
            report['blocker'] = str(exc)
        # Stale/cancelled jobs cannot publish evidence/configuration or initialize the accepted DB tip.
        with self.db.write() as s:
            current = ctx.queue.identity(s, ctx.lease)
            p = s.get(Project, current['project_id'])
            if p.workflow.get('accepted_tip') or p.workflow.get('onboarding_detail', {}).get('job_id') != current['root_job_id']:
                raise ValueError('onboarding request is no longer current')
            helper = ProductWorkspace(self.db, self.store, None, self.root, None, self.redactor)
            if local:
                report['commands'] = helper.command_reports(s, current, local.dir / 'evidence')
            report = self.redactor.redact_value(report)
            a = self.store.put_json(s, project_id=p.id, kind='report', name='existing-baseline.json',
                document=report, meta={'producer': 'verification', 'job_id': current['job_id'], 'generation': current['generation']})
            state = {**p.workflow, 'onboarding': report['status'], 'onboarding_detail': {
                'job_id': current['root_job_id'], 'report_artifact_id': a.id,
                'source_sha': report.get('source_sha'), 'baseline_sha': report.get('baseline_sha'),
                'dirty': report.get('dirty', False), 'dirty_status': report.get('source_status', []),
                'dirty_total': report.get('source_status_total', 0),
                'patch_applied': report.get('patch_applied', False), 'blocker': report.get('blocker'),
                'required_checks': report.get('baseline', {}).get('gate', {}).get('status', 'incomplete')}}
            if report['status'] != 'blocked':
                state.update(accepted_tip=report['baseline_sha'], pipeline={'manifest': manifest.to_dict()},
                             repository_instructions=report['detected']['instructions'], onboarding_plan=report['lead_plan'])
            apply_change(s, Project, p.id, expected_revision=p.revision, values={'workflow': state},
                event=EventSpec('project.onboarding_completed', 'service:onboarding', {'status': report['status'], 'report_artifact_id': a.id}))
            attachments = [a.id] + [r[k] for r in report['commands'] for k in ('stdout_file_artifact_id', 'stderr_file_artifact_id')]
            append_message(s, project_id=p.id, thread_id='onboarding:' + p.id, sender='service:onboarding',
                recipient='technical-lead', kind='message', body=report.get('lead_plan', report.get('blocker', 'Onboarding blocked')),
                attachment_ids=attachments, meta={'intent': 'onboarding_baseline', 'report_artifact_id': a.id})
            result = {'onboarding': report['status'], 'report_artifact_id': a.id,
                      'pipeline_completion': {'job_id': current['job_id'], 'generation': current['generation']}}
            if report['status'] == 'blocked':
                bind_service(ctx.queue, s).fail(ctx.lease, error=report['blocker'], retryable=False)
            else:
                bind_service(ctx.queue, s).complete(ctx.lease, result)
        return Outcome('succeeded' if report['status'] != 'blocked' else 'failed', result, report.get('blocker', ''))
