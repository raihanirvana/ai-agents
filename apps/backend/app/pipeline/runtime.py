"""Lead plan -> Hermes developer -> lead review -> trusted QA. Approval stays in Workflow."""
import json
import uuid
from pathlib import Path
from sqlalchemy import select
from app.agents.tools import ToolFacade
from app.agents.outputs import LeadPlanOutput, Clarification
from app.domain import Actor, Attempt
from app.persistence.models import Candidate, Job, Ticket, TicketVersion, Artifact, Verification, Approval
from app.workers.runtime import Outcome
from app.persistence.transactions import bind_service
from .contracts import QaPlan, Review, tool_schema
from .relay import reconcile_accounting
from .workspace import FencedWorkspace, unpack_tree, cleanup_workspace


class PipelineRuntime:
    def __init__(self, structured, workspace, driver):
        self.structured, self.workspace, self.driver = structured, workspace, driver
        self.db, self.store, self.workflow = structured.db, workspace.store, structured.workflow
        self.queue = structured.threads.queue
        self.redactor = structured.redactor
        self.fake = structured.fake
        self.name = 'pipeline:fake' if self.fake else 'pipeline'

    def stop(self, ctx):
        ctx.cancelled.set()
        ctx.stop_resources()

    def run(self, ctx):
        identity = ctx.queue.verify(ctx.lease)
        role, task = identity['role'], ctx.job['runtime_ref'].get('payload', {}).get('task')
        if identity['fake'] != self.fake or bool(self.structured.client.provider_for(role).fake) != self.fake:
            return Outcome('failed', error='provider and pipeline fake labels disagree')
        wanted = {'technical_plan': 'technical-lead', 'qa_plan': 'qa', 'implement': 'developer',
                  'review': 'technical-lead', 'verify': 'qa'}
        if wanted.get(task) != role:
            return Outcome('failed', error='pipeline task/role mismatch')
        if task in ('technical_plan', 'qa_plan'):
            with self.db.read() as s:
                t = s.get(Ticket, identity['ticket_id'])
                if not self.workflow._eligible(s, t):
                    return Outcome('failed', error='planning requires an eligible approved scope')
        return getattr(self, '_' + task)(ctx, identity)

    def _technical_plan(self, ctx, identity):
        from app.workspace import WorkspaceSupervisor
        broker = WorkspaceSupervisor(self.workspace.root).broker(identity['project_id'])
        files = broker._bare('ls-tree', '-r', '--name-only', broker.accepted_sha()).decode().splitlines()
        output, meta, snapshot = self.structured._ask(ctx, identity,
            {'name': 'technical_plan', 'ticket_id': identity['ticket_id'], 'source_files': files}, LeadPlanOutput)
        if isinstance(output, Clarification):
            ctx.request_input(output.as_text(), {}, 'pipeline-clarify:' + identity['root_job_id'] + ':' + str(identity['generation']))
        key = 'pipeline-plan:' + identity['root_job_id'] + ':' + snapshot.sha256
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            message = self.workspace._post(s, identity, key, output.summary, [snapshot.artifact_id], 'technical_plan',
                plan=output.model_dump(), authoritative=False)
        decisions = [self.structured.threads.propose_decision(identity, title=d.title, rationale=d.rationale,
                     key=key + ':decision:' + str(i)) for i, d in enumerate(output.decisions)]
        if output.needs_user:
            ctx.request_input('Technical plan still needs a user decision. Review the plan and its decision proposals.',
                {'plan_message_id': message.id}, key)
        return self.structured._result(meta, plan_message_id=message.id, decision_proposal_ids=decisions,
                                       needs_user=False, authoritative=False)

    def _qa_plan(self, ctx, identity):
        with self.db.read() as s:
            saved = s.get(Job, identity['job_id']).runtime_ref.get('pipeline_qa_plan')
        if saved:
            return Outcome('succeeded', {'suite_artifact_id': saved, 'fake': self.fake})
        baseline = self.workspace.base_build(ctx)
        sup, started, manifest = self.workspace.start(ctx)
        snapshot = self.structured.builder.build(identity, task={
            'name': 'qa_plan', 'instruction': 'Create mandatory browser assertions from approved UAC and the technical plan. '
            'Use CSS selectors. Every automated UAC must be covered. feature/bug cases must fail on the base when applicable; '
            'regression cases may pass on both. Use propose_tests with one QaPlan object. Do not edit source or claim pass.',
            'schema': QaPlan.model_json_schema(), 'baseline': {k: v for k, v in baseline.items() if k not in ('source', 'site')}},
            answer=ctx.answer, lease=ctx.lease, queue=ctx.queue)
        facade = ToolFacade(self.db, self.workflow, self.structured.threads)
        result = {}
        def propose(ctx_, current, args):
            if set(args) != {'plan'}:
                raise ValueError('propose_tests requires only plan')
            suite = QaPlan.model_validate(args['plan'])
            with self.db.write() as s:
                ctx.queue.verify_identity(s, current)
                version = s.query(TicketVersion).filter_by(ticket_id=current['ticket_id'], version=current['scope_version']).one()
                suite.check_criteria(version.uac)
                artifact = self.store.put_json(s, project_id=current['project_id'], kind='report', name='qa-suite.json',
                    document=suite.model_dump(), meta={'producer': 'qa-plan', 'fake': self.fake})
                self.workspace._post(s, current, 'qa-plan:' + current['root_job_id'], suite.summary,
                    [snapshot.artifact_id, artifact.id], 'qa_plan', suite_digest=suite.digest)
                job = s.get(Job, current['job_id'])
                job.runtime_ref = {**job.runtime_ref, 'pipeline_qa_plan': artifact.id}
                result['suite_artifact_id'] = artifact.id
            return {'submitted': True, 'suite_digest': suite.digest}
        def inspect(c, i, a):
            if set(a) != {'path'}:
                raise ValueError('inspect_app requires a relative path; use . to list source files')
            if a['path'] == '.':
                return {'files': sup.list_files(started.ref, started.credential)}
            return {'content': sup.read_file(started.ref, started.credential, a['path']).decode(errors='replace')}
        facade._handlers.update({'propose_tests': propose, 'inspect_app': inspect})
        parameters = {'propose_tests': {'plan': tool_schema(QaPlan)}, 'read_criteria': {}, 'inspect_app': {'path': {'type': 'string'}},
                      'request_input': {'question': {'type': 'string'}}}
        self.driver.run(ctx, identity, snapshot, self._tools(ctx, facade, parameters), parameters)
        if not result:
            return Outcome('failed', error='QA finished without a validated suite')
        return Outcome('succeeded', {**result, 'fake': self.fake})

    @staticmethod
    def _tools(ctx, facade, parameters):
        # The relay reserves each invocation. _execute verifies role/arguments/lease without a second reservation.
        return {name: (lambda args, tool=name: facade._execute(ctx, tool, args)) for name in parameters}

    def _implement(self, ctx, identity):
        sup, started, manifest = self.workspace.start(ctx)
        suite, suite_id = self.workspace.suite(identity)
        snapshot = self.structured.builder.build(identity, task={'name': 'implement', 'runner_manifest': manifest.to_dict(),
            'source_files': sup.list_files(started.ref, started.credential),
            'qa_suite': suite.model_dump(), 'instructions': 'Implement the approved scope. Read/patch files with relative paths. '
            'read_file path "." lists source files. run_command selects install/test/build only. '
            'Do not remove or skip repository tests to make checks pass. Completion REQUIRES submit_candidate; a prose answer fails the job. '
            'Use the locked dependencies and existing Node tests, not an uninstalled browser unit test library. '
            'Unclear requirements: use request_decision for the lead. Only the trusted harness determines QA.'},
            answer=ctx.answer, lease=ctx.lease, queue=ctx.queue)
        facade = ToolFacade(self.db, self.workflow, self.structured.threads)
        result = {}
        def read(c, i, a):
            if set(a) != {'path'}:
                raise ValueError('read_file requires path')
            if a['path'] == '.':
                return {'files': sup.list_files(started.ref, started.credential)}
            return {'content': self.redactor.redact(sup.read_file(started.ref, started.credential, a['path']).decode(errors='replace'))}
        def patch(c, i, a):
            if set(a) != {'path', 'content'} or (a['content'] is not None and not isinstance(a['content'], str)):
                raise ValueError('patch_file requires path and string content (or null to delete a file)')
            if a['content'] is None:
                sup.delete_file(started.ref, started.credential, a['path'])
                return {'deleted': a['path']}
            sup.write_file(started.ref, started.credential, a['path'], a['content'].encode())
            return {'written': a['path']}
        def command(c, i, a):
            if set(a) != {'phase'} or a['phase'] not in ('install', 'build', 'test'):
                raise ValueError('run_command requires a configured phase')
            r = sup.run_phase(started.ref, started.credential, a['phase'])
            return {'exit_code': r.exit_code, 'stdout': r.stdout.decode(errors='replace')[:16000],
                    'stderr': r.stderr.decode(errors='replace')[:16000]}
        def submit(c, i, a):
            if set(a) != {'message'}:
                raise ValueError('submit_candidate requires message')
            if result:
                return {**result, 'submitted': True}
            result.update(self.workspace.submit(ctx, sup, started, manifest, a['message']))
            return {**result, 'submitted': True}
        def decision(c, i, a):
            if set(a) != {'question'}:
                raise ValueError('request_decision requires question')
            checkpoint = self.workspace.save_checkpoint(ctx, sup, started)
            self.structured.threads.ask(ctx, 'technical-lead', a['question'],
                key='pipeline-decision:' + i['root_job_id'] + ':' + checkpoint, checkpoint={'artifact_id': checkpoint})
        facade._handlers.update({'read_file': read, 'patch_file': patch, 'run_command': command,
            'inspect_diff': lambda c, i, a: {'diff': sup.inspect_diff(started.ref, started.credential)},
            'submit_candidate': submit, 'request_decision': decision})
        parameters = {'read_file': {'path': {'type': 'string'}}, 'patch_file': {'path': {'type': 'string'},
            'content': {'type': ['string', 'null'], 'description': 'Full new file contents; null deletes this file.'}},
            'run_command': {'phase': {'type': 'string', 'enum': ['install', 'test', 'build']}}, 'inspect_diff': {},
            'submit_candidate': {'message': {'type': 'string'}}, 'request_decision': {'question': {'type': 'string'}}}
        self.driver.run(ctx, identity, snapshot, self._tools(ctx, facade, parameters), parameters)
        return Outcome('succeeded', result) if result else Outcome('failed', error='developer finished without a broker candidate')

    def _candidate(self, identity):
        with self.db.read() as s:
            t = s.get(Ticket, identity['ticket_id'])
            c = s.get(Candidate, t.workflow.get('candidate_id'))
            if not c or c.scope_version != identity['scope_version']:
                raise ValueError('no current candidate')
            return c

    def _reject(self, ctx, identity, candidate, reason, **result):
        published = {**result, 'request_changes': True, 'candidate_id': candidate.id}
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            t = s.get(Ticket, identity['ticket_id'])
            self.workspace._post(s, identity, 'rejection:' + identity['root_job_id'], reason, [], 'repair_feedback',
                                 candidate_id=candidate.id)
            bind_service(self.workflow, s).request_changes(ctx.actor(), t.id, t.revision, candidate.id, reason,
                Attempt(identity['job_id'], identity['generation'], identity['scope_version']))
            # Like approve/open-UAT: the decision and the job outcome commit together, so a crash cannot
            # leave a running job behind a ticket that is already back in development.
            bind_service(ctx.queue, s).complete(ctx.lease, {**published, 'pipeline_completion': {
                'job_id': identity['job_id'], 'generation': identity['generation']}})
        return Outcome('succeeded', published)

    def _review(self, ctx, identity):
        candidate = self._candidate(identity)
        if not candidate.target_artifact_id:
            return self._reject(ctx, identity, candidate, 'Required immutable build failed; inspect candidate handoff logs.')
        with self.db.read() as s:
            target = json.loads(self.store.read_bytes(s, candidate.target_artifact_id))
            gates = json.loads(self.store.read_bytes(s, target['gate_artifact_id']))
        current_manifest, current_base = self.workspace.configuration(identity)
        from app.workspace.manifest import parse_manifest
        if current_manifest.digest != parse_manifest(target['execution_manifest']).digest or current_base != candidate.base_sha:
            return Outcome('failed', error='Project runner/base changed: new candidate/target verification is required')
        if not self.gates_eligible(identity, candidate, gates):
            return self._reject(ctx, identity, candidate, 'Required repository gate failed without an exact baseline waiver: ' + json.dumps(gates.get('gate'))[:1800])
        sup = FencedWorkspace(self.workspace.root, ctx)
        diff = sup.broker(identity['project_id']).diff_commits(candidate.base_sha, candidate.commit_sha)
        output, meta, _ = self.structured._ask(ctx, identity, {'name': 'technical_review', 'candidate_id': candidate.id,
            'diff': self.redactor.redact(diff), 'repo_gates': gates, 'schema': Review.model_json_schema(),
            'instructions': 'Review the diff against approved scope, including all changes to repo tests and skip/removal. '
            'Return a Review JSON. Technical acceptance cannot approve user scope/UAT/release.'}, Review)
        if not output.accept:
            return self._reject(ctx, identity, candidate, output.summary + '\n' + '\n'.join(output.findings))
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            t = s.get(Ticket, identity['ticket_id'])
            self.workspace._post(s, identity, 'review:' + identity['root_job_id'], output.summary,
                [meta['context_artifact_id'], target['gate_artifact_id']], 'technical_review', candidate_id=candidate.id)
            bind_service(self.workflow, s).approve_review(ctx.actor(), t.id, t.revision,
                Attempt(identity['job_id'], identity['generation'], identity['scope_version']), candidate.id)
            bind_service(ctx.queue, s).complete(ctx.lease, {'candidate_id': candidate.id, 'review': 'accepted',
                'pipeline_completion': {'job_id': identity['job_id'], 'generation': identity['generation']}})
        return Outcome('succeeded', {'candidate_id': candidate.id, 'review': 'accepted', 'fake': self.fake})

    def gates_eligible(self, identity, candidate, gates):
        return self.gate_admission(identity, candidate, gates)['status'] in ('passed', 'waived')

    def gate_admission(self, identity, candidate, gates):
        gate = gates.get('gate')
        if gates.get('build_error') or not gate or gate.get('infrastructure_failure'):
            return {'status': 'failed', 'waiver_ids': []}
        if gate['status'] == 'passed':
            return {'status': 'passed', 'waiver_ids': []}
        if gate['status'] != 'failed':
            return {'status': 'incomplete', 'waiver_ids': []}
        with self.db.read() as s:
            approvals = list(s.scalars(select(Approval).where(Approval.ticket_id == identity['ticket_id'], Approval.type == 'baseline_waiver')))
        actor = Actor('service:verification', 'verification', identity['project_id'])
        matches = [ap.id for ap in approvals if self.workflow.waiver_matches(actor, ap.id, ticket_id=identity['ticket_id'],
            scope_version=identity['scope_version'], base_sha=candidate.base_sha,
            environment_digest=gate['environment_digest'], test_id=gate['test_id'], signature=gate['signature'])]
        return {'status': 'waived' if matches else 'failed', 'waiver_ids': matches}

    def _verify(self, ctx, identity):
        candidate = self._candidate(identity)
        self.structured.builder.build(identity, task={'name': 'trusted_verification', 'candidate_id': candidate.id}, lease=ctx.lease, queue=ctx.queue)
        with self.db.read() as s:
            target = json.loads(self.store.read_bytes(s, candidate.target_artifact_id))
            build = json.loads(self.store.read_bytes(s, candidate.build_artifact_id))
            files = json.loads(self.store.read_bytes(s, build['bundle_artifact_id']))
            suite = QaPlan.model_validate(json.loads(self.store.read_bytes(s, target['suite_artifact_id'])))
            version = s.query(TicketVersion).filter_by(ticket_id=candidate.ticket_id, version=candidate.scope_version).one()
            suite.check_criteria(version.uac)
            gates = json.loads(self.store.read_bytes(s, target['gate_artifact_id']))
        current_manifest, current_base = self.workspace.configuration(identity)
        from app.workspace.manifest import parse_manifest
        if current_manifest.digest != parse_manifest(target['execution_manifest']).digest or current_base != candidate.base_sha:
            return Outcome('failed', error='Project runner/base changed: new candidate/target verification is required')
        if not self.gates_eligible(identity, candidate, gates):
            return self._reject(ctx, identity, candidate, 'Required gates/waiver no longer eligible')
        directory = self.workspace.root / '.verification' / identity['job_id'] / str(identity['generation'])
        site = directory / 'site'
        unpack_tree(files, site)
        from app.workspace import fsutil
        if fsutil.sha256_tree(site, fsutil.scan_tree(site)) != target['build_digest']:
            raise ValueError('stored build bytes do not match immutable target')
        proof = self.workspace.harness.run(ctx, site, candidate.target_digest, suite, target['node_image_id'], expected_runner=target['runner'])
        proof['required_checks'] = self.gate_admission(identity, candidate, gates)
        baseline = self.workspace.base_build(ctx)
        proof['baseline'] = {k: v for k, v in baseline.items() if k not in ('source', 'site')}
        if baseline['applicable']:
            if baseline['status'] == 'built':
                from app.workspace import fsutil
                from .contracts import digest_of
                base_target = digest_of({'base_sha': candidate.base_sha,
                    'build_digest': fsutil.sha256_tree(baseline['site'], fsutil.scan_tree(baseline['site'])),
                    'runner': target['runner'], 'config_digest': target['config_digest'], 'suite_digest': suite.digest})
                base_proof = self.workspace.harness.run(ctx, baseline['site'], base_target, suite,
                    target['node_image_id'], expected_runner=target['runner'])
                base_proof.pop('diagnostics', None)
                proof['baseline']['execution'] = base_proof
                statuses = {t['id']: t['status'] for t in (base_proof.get('report') or {}).get('tests', [])}
                discriminates = all(statuses.get(t.id) == 'failed' for t in suite.tests if t.purpose in ('feature', 'bug'))
                if base_proof['status'] == 'incomplete' or not discriminates:
                    proof.update(status='incomplete', infrastructure_failure=base_proof['status'] == 'incomplete',
                        error='Baseline execution incomplete or feature/bug assertion also passed on base')
            else:
                proof.update(status='incomplete', infrastructure_failure=True, error='Accepted base could not be built for comparison')
        if self.fake and proof['status'] == 'passed':
            proof['status'], proof['infrastructure_failure'] = 'incomplete', True
            proof['error'] = 'FAKE provider: harness result cannot count as product QA pass'
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            attachments = [target['gate_artifact_id']]
            attachments += [baseline[k] for k in ('artifact_id', 'fingerprint_artifact_id') if k in baseline]
            for item in proof.pop('diagnostics', []):
                a = self.store.put_bytes(s, project_id=identity['project_id'], kind=item['kind'],
                    data=item['data'], name=item['kind'] + ('.png' if item['kind'] == 'screenshot' else '.zip'),
                    meta={'producer': 'verification', 'test_id': item['test_id']})
                attachments.append(a.id)
            evidence = self.store.put_json(s, project_id=identity['project_id'], kind='report', name='acceptance.json',
                document=self.redactor.redact_value(proof), meta={'producer': 'verification'})
            attachments.append(evidence.id)
            v = Verification(candidate_id=candidate.id, target_artifact_id=candidate.target_artifact_id,
                target_digest=candidate.target_digest, commit_artifact_id=candidate.commit_artifact_id,
                build_artifact_id=candidate.build_artifact_id, context_artifact_id=candidate.context_artifact_id,
                evidence_id=proof['invocation_id'], suite_digest=suite.digest, expected_test_ids=[t.id for t in suite.tests],
                counts=proof['counts'], uac_coverage=proof['coverage'], status=proof['status'], evidence_artifact_ids=attachments,
                results={**{k: proof[k] for k in ('commands', 'infrastructure_failure')}, 'fake_provider': self.fake,
                    'job_id': identity['job_id'], 'generation': identity['generation'], 'executed_test_ids': proof['executed']})
            s.add(v)
            s.flush()
            t = s.get(Ticket, identity['ticket_id'])
            if proof['status'] == 'passed':
                smoke = self.store.put_json(s, project_id=identity['project_id'], kind='report', name='smoke.json',
                    document={'target_artifact_id': candidate.target_artifact_id, 'target_digest': candidate.target_digest,
                        'kind': 'preview_smoke', 'status': 'passed'}, meta={'producer': 'verification'})
                attachments.append(smoke.id)
                bind_service(self.workflow, s).open_uat(Actor('service:verification', 'verification', identity['project_id']), t.id, t.revision,
                    Attempt(identity['job_id'], identity['generation'], identity['scope_version']), candidate.id, v.id, smoke.id)
            self.workspace._post(s, identity, 'verification:' + proof['invocation_id'],
                'QA ' + proof['status'] + '. Manual UAC still require user confirmation.', attachments, 'qa_evidence',
                candidate_id=candidate.id, verification_id=v.id, counts=proof['counts'], coverage=proof['coverage'])
            if proof['status'] == 'passed':
                bind_service(ctx.queue, s).complete(ctx.lease, {'verification_id': v.id, 'evidence_artifact_ids': attachments,
                    'qa_status': 'passed', 'pipeline_completion': {'job_id': identity['job_id'], 'generation': identity['generation']}})
        if proof['status'] == 'failed':
            return self._reject(ctx, identity, candidate, 'Browser acceptance failed: ' + json.dumps(proof['report'])[:3000],
                                verification_id=v.id, evidence_artifact_ids=attachments, qa_status='failed')
        return Outcome('succeeded' if proof['status'] == 'passed' else 'failed',
            {'verification_id': v.id, 'evidence_artifact_ids': attachments, 'qa_status': proof['status']},
            error=proof.get('error') or ('Incomplete harness execution' if proof['status'] == 'incomplete' else ''))

    def reconcile(self, snapshot):
        with self.db.read() as s:
            job = s.get(Job, snapshot['id'])
            if job is None or (job.status == 'running' and job.lease_expires_at > self.queue.clock()):
                return False
        generation = snapshot['runtime_ref']['cleanup']['generation']
        for resource in snapshot['runtime_ref'].get('resources', []):
            if resource.get('generation') != generation or resource.get('owner', snapshot['id'] + ':' + str(generation)) != snapshot['id'] + ':' + str(generation):
                return False
        reconcile_accounting(self.queue, snapshot)
        # Called only after Supervisor proved the old owner/process group is gone.
        from app.workspace import WorkspaceSupervisor
        from app.workspace.runspec import RunRef
        sup = WorkspaceSupervisor(self.workspace.root)
        for resource in snapshot['runtime_ref'].get('resources', []):
            if resource.get('kind') == 'pipeline_workspace':
                ref = RunRef(resource['project_id'], resource['run_id'])
                cleanup_workspace(sup, ref, snapshot['id'], generation)
            elif resource.get('kind') == 'pipeline_containers':
                from .harness import remove_owned
                for name in resource['names']:
                    remove_owned(sup.sandbox, name, resource['owner'])
            elif resource.get('kind') != 'process_groups':
                return False
        return True
