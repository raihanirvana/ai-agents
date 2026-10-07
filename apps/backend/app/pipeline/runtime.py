"""Lead plan -> Hermes developer -> lead review -> trusted QA. Approval stays in Workflow."""
import json
import uuid
from dataclasses import replace
from pathlib import Path
from sqlalchemy import select
from app.agents.tools import ToolFacade
from app.agents.context import ContextRefused, ContextTooLarge
from app.agents.models import ModelError
from app.agents.outputs import LeadPlanOutput, Clarification, InvalidOutput
from app.domain import Actor, Attempt
from app.persistence.models import Candidate, Job, Ticket, TicketVersion, Artifact, Verification, Approval
from app.workers.runtime import Outcome
from app.persistence.transactions import bind_service
from .contracts import QaPlan, QaDiagnosis, QaSetupRepair, QaOptionRepair, Review, tool_schema, browser_capabilities
from .qa_policy import policy_context, preflight
from .relay import reconcile_accounting
from .hermes import RelayContextError
from .workspace import FencedWorkspace, unpack_tree, cleanup_workspace
from .files import allocate_directory, cleanup_directory


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
                  'review': 'technical-lead', 'verify': 'qa', 'diagnose': 'qa'}
        if wanted.get(task) != role:
            return Outcome('failed', error='pipeline task/role mismatch')
        if task in ('technical_plan', 'qa_plan'):
            with self.db.read() as s:
                t = s.get(Ticket, identity['ticket_id'])
                if not self.workflow._eligible(s, t):
                    return Outcome('failed', error='planning requires an eligible approved scope')
        try:
            return getattr(self, '_' + task)(ctx, identity)
        except (ContextRefused, ContextTooLarge, InvalidOutput) as exc:
            error = self.redactor.redact('pipeline context refused: ' + str(exc))[:500]
            ctx.log(error)
            return Outcome('failed', error=error, retryable=False)
        except RelayContextError as exc:
            return Outcome('failed', {'failure_kind': 'relay_context', 'relay_detail': exc.detail},
                error='Konteks ditolak relay lokal sebelum panggilan provider. Periksa ukuran request/proyeksi sebelum retry.',
                retryable=False)
        except ModelError as exc:
            error = self.redactor.redact(str(exc))[:500]
            return Outcome('failed', {'failure_kind': 'provider', 'http_status': getattr(exc, 'http_status', None)},
                error=error, retryable=exc.retryable)

    def _technical_plan(self, ctx, identity):
        from app.workspace import WorkspaceSupervisor
        from .bootstrap import bootstrap_contract
        manifest, base = self.workspace.configuration(identity)
        broker = WorkspaceSupervisor(self.workspace.root).broker(identity['project_id'])
        files = broker._bare('ls-tree', '-r', '--name-only', base).decode().splitlines()
        output, meta, snapshot = self.structured._ask(ctx, identity,
            {'name': 'technical_plan', 'ticket_id': identity['ticket_id'], 'source_files': files,
             'runner_manifest': manifest.to_dict(),
             'browser_capabilities': browser_capabilities(),
             'verification_policy': policy_context(),
             'reference_bootstrap': bootstrap_contract() if self.workspace.bootstrap_available(identity) else None,
             'onboarding': self._onboarding_context(identity['project_id'])}, LeadPlanOutput)
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

    def _onboarding_context(self, project_id):
        from app.persistence.models import Project
        with self.db.read() as s:
            p = s.get(Project, project_id)
            return {'status': p.workflow.get('onboarding'), 'baseline': p.workflow.get('onboarding_detail'),
                    'plan': p.workflow.get('onboarding_plan')}

    def _qa_plan(self, ctx, identity):
        with self.db.read() as s:
            saved = s.get(Job, identity['job_id']).runtime_ref.get('pipeline_qa_plan')
        if saved:
            return Outcome('succeeded', {'suite_artifact_id': saved, 'fake': self.fake})
        baseline = self.workspace.base_build(ctx)
        sup, started, manifest = self.workspace.start(ctx)
        source_files = sup.list_files(started.ref, started.credential)
        from app.agents.context import ContextBuilder
        builder = self.structured.builder
        planning_builder = ContextBuilder(builder.db, builder.store, builder.agents, builder.redactor,
            limits=replace(builder.limits, total_tokens=max(16384, builder.limits.total_tokens)))
        snapshot = planning_builder.build(identity, task={
            'name': 'qa_plan', 'instruction': 'Create mandatory browser assertions from approved UAC and the technical plan. '
            'Use CSS selectors. Every automated UAC must be covered. feature/bug cases must fail on the base when applicable; '
            'regression cases may pass on both. Use propose_tests with one QaPlan object. Do not edit source or claim pass.',
            'keyboard_guidance': 'fill changes field text and never simulates a key. To test Enter, fill the input '
                'then use a separate step {"action":"press","selector":"input selector","value":"Enter"}. '
                'Do not append newline or the literal characters \\n to simulate Enter.',
            'persistence_guidance': 'To test state restoration, create/change state, assert it, use '
                '{"action":"reload"}, then assert the restored state on the same page/context. '
                'Adding an item and asserting it before reload does not test persistence. '
                'reload takes no selector or value. Every test starts with a fresh browser context.',
            'schema': QaPlan.model_json_schema(), 'source_files': source_files,
            'browser_capabilities': browser_capabilities(),
            'verification_policy': policy_context(),
            'empty_source_guidance': ('The accepted base is empty; this is expected for a new project. '
                'There is no DOM or other folder to inspect. Plan feature tests from approved UAC and technical plan, '
                'and declare stable selectors/text as a contract for the developer. Do not request user input '
                'only because files are absent. Do not invent new requirements or regression cases for absent features.'
                if not source_files else None),
            'baseline': {k: v for k, v in baseline.items() if k not in ('source', 'site')}},
            answer=ctx.answer, lease=ctx.lease, queue=ctx.queue)
        facade = ToolFacade(self.db, self.workflow, self.structured.threads)
        result = {}
        def propose(ctx_, current, args):
            if set(args) != {'plan'}:
                raise ValueError('propose_tests requires only plan')
            authored = QaPlan.model_validate(args['plan'])
            suite = authored.materialize_fixtures()
            suite.check_csv_expectations()
            with self.db.write() as s:
                ctx.queue.verify_identity(s, current)
                version = s.query(TicketVersion).filter_by(ticket_id=current['ticket_id'], version=current['scope_version']).one()
                readiness = preflight(suite, version.uac, current['scope_version'], new_plan=True)
                receipt = self.store.put_json(s, project_id=current['project_id'], kind='report', name='qa-preflight.json',
                    document={**readiness, 'authored_suite_digest': authored.digest,
                              'fixtures': {fixture.id: len(fixture.steps) for fixture in authored.fixtures}},
                    meta={'producer': 'qa-preflight', 'fake': self.fake})
                authored_row = self.store.put_json(s, project_id=current['project_id'], kind='report',
                    name='qa-authored-plan.json', document=authored.model_dump(),
                    meta={'producer': 'qa-plan-authored', 'fake': self.fake})
                artifact = self.store.put_json(s, project_id=current['project_id'], kind='report', name='qa-suite.json',
                    document=suite.model_dump(), meta={'producer': 'qa-plan', 'fake': self.fake})
                self.workspace._post(s, current, 'qa-plan:' + current['root_job_id'], suite.summary,
                    [snapshot.artifact_id, receipt.id, authored_row.id, artifact.id], 'qa_plan', suite_digest=suite.digest,
                    verification_plan=readiness, preflight_artifact_id=receipt.id)
                job = s.get(Job, current['job_id'])
                job.runtime_ref = {**job.runtime_ref, 'pipeline_qa_plan': artifact.id}
                result['suite_artifact_id'] = artifact.id
                result['preflight_artifact_id'] = receipt.id
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
        from .bootstrap import bootstrap_contract
        sup, started, manifest = self.workspace.start(ctx)
        suite, suite_id = self.workspace.suite(identity)
        with self.db.read() as s:
            feedback = self.workspace.repair_feedback(s, identity)
            repair = ({'message_id': feedback.id, 'candidate_id': feedback.meta.get('candidate_id'),
                       'reason': feedback.body} if feedback is not None else None)
            # Recover diagnostics from pinned evidence even for historical feedback
            # whose prose was truncated before the actual gate failure.
            if repair:
                previous = s.get(Candidate, repair['candidate_id']) if repair['candidate_id'] else None
                if (previous and previous.project_id == identity['project_id']
                        and previous.ticket_id == identity['ticket_id']
                        and previous.scope_version == identity['scope_version']
                        and previous.target_artifact_id):
                    target = json.loads(self.store.read_bytes(s, previous.target_artifact_id))
                    gates = json.loads(self.store.read_bytes(s, target['gate_artifact_id']))
                    if (gates.get('gate') or {}).get('status') != 'passed':
                        from .review_context import gate_failure_summary
                        repair['repository_gate_failure'] = gate_failure_summary(gates)
                        repair['gate_artifact_id'] = target['gate_artifact_id']
                        repair['guidance'] = ('Preserve baseline tests and their original test names/IDs. '
                            'Restore missing baseline tests; add new feature tests separately. '
                            'A passing run_command test alone does not prove baseline coverage at submission.')
        source_files = sup.list_files(started.ref, started.credential)
        bootstrap = bootstrap_contract() if self.workspace.bootstrap_available(identity) else None
        snapshot = self.structured.builder.build(identity, task={'name': 'implement', 'runner_manifest': manifest.to_dict(),
            'repair_feedback': repair,
            'browser_capabilities': browser_capabilities(),
            'source_files': source_files, 'reference_bootstrap': bootstrap,
            'empty_source_guidance': ('The source snapshot is empty by design. Create the application and Node tests '
                'for this approved scope here. There is no existing application in another directory; '
                'never search host paths or /work. write_file creates directories automatically.' if not source_files else None),
            'qa_suite': suite.model_dump(), 'instructions': 'Implement the approved scope. Read/patch files with relative paths. '
            'read_file is paged and never writes. write_file creates/replaces entire files; edit_file replaces one exact match. '
            'Use current expected_digest from read_file for edits/replacements; empty digest creates only missing files. '
            'Follow next_offset with expected_digest to read more. Unchanged repeated pages return a reuse receipt, '
            'not another content copy: use the earlier page and move to implementation/checks. If that page was '
            'archived from active context, request refresh=true once. inspect_diff defaults to stat; pass path for file diff. '
            'read_file path "." lists source files. run_command selects bootstrap/install/test/build. '
            'For repository tests use node:test and node:assert/strict, with npm test running node --test. '
            'Create real .test.js/.test.cjs files that exercise application code; echo success and empty tests fail the gate. '
            'Do not remove or skip repository tests to make checks pass. Completion REQUIRES submit_candidate; a prose answer fails the job. '
            'Use the locked dependencies and existing Node tests, not an uninstalled browser unit test library. '
            'Unclear requirements: use request_decision for the lead. Only the trusted harness determines QA.'},
            answer=ctx.answer, lease=ctx.lease, queue=ctx.queue)
        facade = ToolFacade(self.db, self.workflow, self.structured.threads)
        result = {}
        def read(c, i, a):
            if set(a) != {'path'}:
                raise ValueError('read_file is read-only and accepts only {"path":"relative/file"}. '
                    'To write a file, call patch_file with {"path":"relative/file","content":"full file text"}.')
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
            if set(a) != {'phase'} or a['phase'] not in ('bootstrap', 'install', 'build', 'test'):
                raise ValueError('run_command requires a configured phase')
            if a['phase'] == 'bootstrap':
                return self.workspace.bootstrap(ctx, sup, started)
            if a['phase'] == 'install' and bootstrap and 'package-lock.json' not in sup.list_files(started.ref, started.credential):
                return {'exit_code': 1, 'error': 'package-lock.json is missing. Create package.json with the '
                        'exact reference_bootstrap versions, then call run_command phase bootstrap before install.'}
            r = sup.run_phase(started.ref, started.credential, a['phase'])
            response = {'exit_code': r.exit_code, 'stdout': r.stdout.decode(errors='replace')[:16000],
                        'stderr': r.stderr.decode(errors='replace')[:16000]}
            if a['phase'] == 'test':
                from .gates import node_gate
                response['repository_gate'] = node_gate(r.stdout, r.stderr, r.exit_code)
                if response['repository_gate']['status'] == 'incomplete':
                    response['next'] = ('Repository test evidence is incomplete. Create and run actual Node tests; '
                        'npm exit code 0 alone is insufficient. Read repository_gate counts before submitting.')
            return response
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
            'run_command': {'phase': {'type': 'string', 'enum': ['bootstrap', 'install', 'test', 'build']}}, 'inspect_diff': {},
            'submit_candidate': {'message': {'type': 'string', 'minLength': 1, 'maxLength': 2000}}, 'request_decision': {'question': {'type': 'string'}}}
        if not self.fake:
            from .source_tools import SourceTools
            source = SourceTools(sup, started, self.redactor,
                after_write=lambda: self.workspace.save_source_checkpoint(ctx, sup, started))
            facade._handlers.update(source.handlers)
            parameters.pop('patch_file')
            parameters.update(source.parameters)
        # Fake foundation drivers retain their legacy tool contract; real Hermes
        # exposes only the explicit write/edit surface above.
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
        reason = self.redactor.redact(reason)
        published = {**result, 'request_changes': True, 'candidate_id': candidate.id}
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            t = s.get(Ticket, identity['ticket_id'])
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
            suite = QaPlan.model_validate(json.loads(self.store.read_bytes(s, target['suite_artifact_id'])))
        current_manifest, current_base = self.workspace.configuration(identity)
        from app.workspace.manifest import parse_manifest
        if current_manifest.digest != parse_manifest(target['execution_manifest']).digest or current_base != candidate.base_sha:
            return self._reject(ctx, identity, candidate,
                'Project runner/base changed; rebuild the candidate with the current configuration. '
                'New technical review, QA and UAT are required.', runner_changed=True)
        if not self.gates_eligible(identity, candidate, gates):
            from .review_context import gate_failure_summary
            failure = gate_failure_summary(gates)
            return self._reject(ctx, identity, candidate,
                'Required repository gate failed without an exact baseline waiver. '
                'Preserve original baseline test names/IDs; restore missing tests rather than rename or remove them. '
                'Failure: ' + json.dumps(failure) + '\nFull evidence artifact: ' + target['gate_artifact_id'],
                repository_gate_failure=failure, gate_artifact_id=target['gate_artifact_id'])
        sup = FencedWorkspace(self.workspace.root, ctx)
        broker = sup.broker(identity['project_id'])
        diff = broker.diff_commits(candidate.base_sha, candidate.commit_sha)
        from .review_context import review_diff, gate_summary, dependency_manifest_summary
        from app.workspace.errors import WorkspaceError
        try:
            projected = review_diff(broker, candidate.base_sha, candidate.commit_sha, diff)
            dependency_manifest = dependency_manifest_summary(broker, candidate.commit_sha)
        except (ValueError, WorkspaceError) as exc:
            return self._reject(ctx, identity, candidate, 'Dependency lock inspection failed: ' + self.redactor.redact(str(exc)))
        ctx.log('review.context ' + json.dumps({'full_diff_chars': len(diff),
            'projected_source_chars': len(json.dumps(projected)), 'dependency_summary': projected['dependency_changes'] is not None}))
        from app.persistence import append_message
        from app.persistence.models import Message
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            evidence_key = 'review-full-diff:' + identity['job_id'] + ':' + candidate.id
            saved = s.scalar(select(Message).where(Message.project_id == identity['project_id'],
                                                    Message.idempotency_key == evidence_key))
            if saved is not None:
                self.store.read_bytes(s, saved.attachment_ids[0])
                evidence = s.get(Artifact, saved.attachment_ids[0])
            else:
                evidence = self.store.put_bytes(s, project_id=identity['project_id'], kind='report',
                    name='technical-review-full.diff', data=self.redactor.redact(diff).encode(), run_id=identity['job_id'],
                    meta={'producer': 'review-diff', 'candidate_id': candidate.id, 'base_sha': candidate.base_sha,
                          'commit_sha': candidate.commit_sha, 'generation': identity['generation']})
                append_message(s, project_id=identity['project_id'], ticket_id=identity['ticket_id'],
                    thread_id=f"job:{identity['job_id']}:g{identity['generation']}", sender='service:review',
                    body='Full candidate diff archived before dependency/context projection.',
                    idempotency_key=evidence_key, attachment_ids=[evidence.id],
                    meta={'runtime_log': True, 'intent': 'review_diff'})
        output, meta, _ = self.structured._ask(ctx, identity, {'name': 'technical_review', 'candidate_id': candidate.id,
            **self.redactor.redact_value(projected), 'repo_gates': gate_summary(gates),
            'dependency_manifest': self.redactor.redact_value(dependency_manifest),
            'runner_manifest': current_manifest.to_dict(), 'qa_selector_contract': suite.model_dump(),
            'browser_capabilities': browser_capabilities(),
            'full_diff_artifact_id': evidence.id, 'gate_artifact_id': target['gate_artifact_id'],
            'instructions': 'Review the diff against approved scope, including all changes to repo tests and skip/removal. '
            'Return a Review JSON. Technical acceptance cannot approve user scope/UAT/release.'}, Review,
            context_limits=replace(self.structured.builder.limits, total_tokens=32768))
        if not output.accept:
            return self._reject(ctx, identity, candidate, output.summary + '\n' + '\n'.join(output.findings))
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            t = s.get(Ticket, identity['ticket_id'])
            self.workspace._post(s, identity, 'review:' + identity['root_job_id'], output.summary,
                [meta['context_artifact_id'], target['gate_artifact_id'], evidence.id], 'technical_review', candidate_id=candidate.id)
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
        failures = gate.get('failures', [{'test_id': gate['test_id'], 'signature': gate['signature']}])
        matches = []
        for failure in failures:
            exact = [ap.id for ap in approvals if self.workflow.waiver_matches(actor, ap.id, ticket_id=identity['ticket_id'],
                scope_version=identity['scope_version'], base_sha=candidate.base_sha,
                environment_digest=gate['environment_digest'], test_id=failure['test_id'], signature=failure['signature'])]
            if not exact:
                return {'status': 'failed', 'waiver_ids': matches}
            matches.extend(exact)
        return {'status': 'waived' if failures else 'failed', 'waiver_ids': list(dict.fromkeys(matches))}

    def _verify(self, ctx, identity):
        finalizers = []
        try:
            return self._verify_attempt(ctx, identity, finalizers)
        finally:
            for finish in reversed(finalizers):
                finish()

    def _verify_attempt(self, ctx, identity, finalizers):
        from .qa_repair import classify_failure, repair_contract_errors, MAX_SUITE_REPAIRS
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
            return self._reject(ctx, identity, candidate,
                'Project runner/base changed; rebuild the candidate with the current configuration. '
                'New technical review, QA and UAT are required.', runner_changed=True)
        if not self.gates_eligible(identity, candidate, gates):
            return self._reject(ctx, identity, candidate, 'Required gates/waiver no longer eligible')
        runner = self.workspace.harness.identity()
        if runner != target['runner']:
            return self._repair_qa_target(ctx, identity, candidate, target, suite, None, [],
                repair_kind='runner_contract_upgrade', new_runner=runner)
        directory = allocate_directory(ctx, self.workspace.root / '.verification', 'verification',
                                       store=self.store, redactor=self.redactor, finalizers=finalizers)
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
        failure_kind = classify_failure(proof) if proof['status'] != 'passed' else None
        proof['failure_kind'] = failure_kind
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            attachments = [target['gate_artifact_id']]
            attachments += [baseline[k] for k in ('artifact_id', 'fingerprint_artifact_id') if k in baseline]
            attachments += baseline.get('fingerprint_artifact_ids', [])
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
                    'failure_kind': failure_kind,
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
            if failure_kind == 'test_contract':
                repaired = repair_contract_errors(suite, proof)
                repair_count = target.get('suite_repair_count', 0)
                if repaired is not None and type(repair_count) is int and 0 <= repair_count < MAX_SUITE_REPAIRS:
                    return self._repair_qa_target(ctx, identity, candidate, target, repaired, v.id, attachments)
            pending = {'verification_id': v.id, 'evidence_artifact_ids': attachments,
                'qa_status': 'failed', 'failure_kind': failure_kind, 'diagnosis_required': True,
                'candidate_id': candidate.id, 'target_digest': candidate.target_digest}
            with self.db.write() as s:
                ctx.queue.verify_identity(s, identity)
                bind_service(ctx.queue, s).complete(ctx.lease, {**pending, 'pipeline_completion': {
                    'job_id': identity['job_id'], 'generation': identity['generation']}})
            return Outcome('succeeded', pending)
        return Outcome('succeeded' if proof['status'] == 'passed' else 'failed',
            {'verification_id': v.id, 'evidence_artifact_ids': attachments, 'qa_status': proof['status'],
             'failure_kind': failure_kind},
            error=proof.get('error') or ('Incomplete harness execution' if proof['status'] == 'incomplete' else ''),
            retryable=failure_kind == 'infrastructure')

    def _diagnose(self, ctx, identity):
        """One durable diagnosis of failed evidence, with no model authority to pass QA."""
        candidate = self._candidate(identity)
        with self.db.read() as s:
            job = s.get(Job, identity['job_id'])
            v = s.get(Verification, job.runtime_ref['payload'].get('verification_id'))
            if (v is None or v.status != 'failed' or v.candidate_id != candidate.id
                    or v.target_digest != candidate.target_digest or v.target_artifact_id != candidate.target_artifact_id):
                raise ValueError('diagnosis belongs to an obsolete verification target')
            target = json.loads(self.store.read_bytes(s, candidate.target_artifact_id))
            suite = QaPlan.model_validate(json.loads(self.store.read_bytes(s, target['suite_artifact_id'])))
            version = s.query(TicketVersion).filter_by(ticket_id=candidate.ticket_id, version=candidate.scope_version).one()
            criteria = version.uac
            suite.check_criteria(criteria)
            rows = [s.get(Artifact, aid) for aid in v.evidence_artifact_ids]
            report_row = next((row for row in rows if row is not None
                and row.kind == 'report' and str(row.path).endswith('/acceptance.json')
                and row.meta.get('producer') == 'verification'), None)
            if report_row is None:
                raise ValueError('authoritative acceptance report unavailable')
            proof = json.loads(self.store.read_bytes(s, report_row.id))
            attachments = list(v.evidence_artifact_ids)
            verification_id = v.id
            from app.persistence.models import Message
            previous = s.scalar(select(Message).where(Message.project_id == identity['project_id'],
                Message.idempotency_key == 'qa-diagnosis:' + identity['root_job_id']))
            persisted_output = None
            if previous is not None:
                diagnosis_row = s.get(Artifact, previous.attachment_ids[-1]) if previous.attachment_ids else None
                if (previous.meta.get('candidate_id') != candidate.id
                        or previous.meta.get('verification_id') != verification_id or diagnosis_row is None
                        or diagnosis_row.meta.get('producer') != 'qa-diagnosis'
                        or diagnosis_row.meta.get('target_digest') != candidate.target_digest
                        or diagnosis_row.meta.get('verification_id') != verification_id
                        or diagnosis_row.meta.get('fake') != self.fake):
                    raise ValueError('persisted QA diagnosis belongs to different candidate/evidence')
                persisted_output = QaDiagnosis.model_validate(json.loads(self.store.read_bytes(s, diagnosis_row.id)))
        manifest, base = self.workspace.configuration(identity)
        from app.workspace.manifest import parse_manifest
        if base != candidate.base_sha or manifest.digest != parse_manifest(target['execution_manifest']).digest:
            return Outcome('failed', {'failure_kind': 'infrastructure', 'verification_id': verification_id},
                           error='Runner/base changed after failed QA; rebuild/revalidate before diagnosis.')
        runner = self.workspace.harness.identity()
        if runner != target['runner']:
            return self._repair_qa_target(ctx, identity, candidate, target, suite, verification_id, attachments,
                repair_kind='runner_contract_upgrade', new_runner=runner)
        failed = [t for t in proof['report']['tests'] if t['status'] == 'failed']
        if not failed:
            raise ValueError('failed verification contains no failed test')
        # Read source as data through the broker, never execute it. The evidence
        # and test inputs remain the basis; an incomplete view requires unknown.
        broker = FencedWorkspace(self.workspace.root, ctx).broker(candidate.project_id)
        paths = broker._bare('ls-tree', '-r', '--name-only', candidate.commit_sha).decode().splitlines()
        source, remaining, unavailable = {}, 16000, []
        eligible = [p for p in paths if p.endswith(('.html', '.css', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx'))
                    and not set(Path(p).parts).intersection(('test', 'tests', '__tests__', 'node_modules', 'home'))
                    and not Path(p).stem.endswith(('.test', '.spec'))]
        from app.workspace.errors import WorkspaceError
        for path in eligible[:12]:
            try:
                raw = broker.read_committed_file(candidate.commit_sha, path).decode(errors='replace')
            except (WorkspaceError, ValueError):
                unavailable.append(path)
                continue
            source[path] = raw[:remaining]
            remaining -= len(source[path])
            if remaining <= 0:
                break
        diagnosis_task = {
            'name': 'qa_diagnosis', 'candidate_id': candidate.id, 'verification_id': verification_id,
            'verification_policy': policy_context(), 'approved_criteria': criteria,
            'suite': suite.model_dump(), 'failed_tests': failed, 'source': source,
            'source_complete': not unavailable and len(source) == len(eligible) and remaining > 0,
            'source_unavailable': unavailable,
            'instructions': 'Diagnose every failed test against approved criteria, original input, runner evidence and source. '
                'Return QaDiagnosis. Application fault requires concrete evidence that the assertion is valid and shipped '
                'behaviour violates a criterion. Each application finding MUST include criterion_id, source_path and '
                'source_excerpt copied exactly from supplied shipped source (12..400 characters), explaining the causal bug. '
                'An unresolved selector/action contract or guessed generated record ID cannot authorize application repair. Test fault means wrong selector/action/expected value; never ask for a valid '
                'control or CSV escaping to be broken to fit a test. Mixed or insufficient evidence is unknown. '
                'You cannot pass QA, drop tests, change approved UAC or waive a gate. Include every failed test exactly once.'
        }
        if persisted_output is not None:
            output, meta = persisted_output, {}
            ctx.log('Reusing persisted QA diagnosis for this exact failed target/evidence.')
        else:
            output, meta, _ = self.structured._ask(ctx, identity, diagnosis_task, QaDiagnosis,
                context_limits=replace(self.structured.builder.limits, total_tokens=32768))
        if {f.test_id for f in output.findings} != {t['id'] for t in failed}:
            return Outcome('failed', {'failure_kind': 'test_contract', 'verification_id': verification_id},
                           error='QA diagnosis must account for every failed test exactly once.')
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            from app.persistence.models import Message
            key = 'qa-diagnosis:' + identity['root_job_id']
            previous = s.scalar(select(Message).where(Message.project_id == identity['project_id'],
                                                      Message.idempotency_key == key))
            if previous is not None:
                diagnosis = s.get(Artifact, previous.attachment_ids[-1])
                if (previous.meta.get('candidate_id') != candidate.id or previous.meta.get('verification_id') != verification_id
                        or json.loads(self.store.read_bytes(s, diagnosis.id)) != self.redactor.redact_value(output.model_dump())):
                    raise ValueError('persisted QA diagnosis differs from this candidate/evidence')
                attachments = list(previous.attachment_ids)
            else:
                diagnosis = self.store.put_json(s, project_id=identity['project_id'], kind='report', name='qa-diagnosis.json',
                    document=self.redactor.redact_value(output.model_dump()), meta={'producer': 'qa-diagnosis',
                        'verification_id': verification_id, 'target_digest': candidate.target_digest, 'fake': self.fake})
                attachments += [meta['context_artifact_id'], diagnosis.id]
                self.workspace._post(s, identity, key, output.summary,
                    attachments, 'qa_diagnosis', candidate_id=candidate.id, verification_id=verification_id,
                    fault=output.fault, findings=output.model_dump()['findings'])
        if output.fault == 'application':
            from .qa_repair import application_repair_issues
            issues = application_repair_issues(suite, proof, output, criteria, source)
            if issues:
                ctx.log('Application attribution withheld: ' + '; '.join(issues))
                return Outcome('failed', {'verification_id': verification_id, 'evidence_artifact_ids': attachments,
                    'qa_status': 'failed', 'failure_kind': 'test_contract', 'diagnosis_fault': 'unknown',
                    'diagnosis_artifact_id': diagnosis.id, 'application_repair_issues': issues},
                    error='QA application diagnosis lacks qualified evidence: ' + '; '.join(issues))
            detail = '\n'.join(f"{f.test_id}: expected {f.expected}; observed {f.observed}. {f.reason}" for f in output.findings)
            return self._reject(ctx, identity, candidate, 'QA diagnosed an application defect: ' + output.summary + '\n' + detail,
                verification_id=verification_id, evidence_artifact_ids=attachments, qa_status='failed',
                failure_kind='application', diagnosis_artifact_id=diagnosis.id)
        if output.fault == 'test':
            from .qa_repair import (repair_csv_inputs, repair_unsupported_text_selectors,
                                    passed_setup_prefixes, repair_test_setup, MAX_SUITE_REPAIRS,
                                    option_binding_candidates, repair_option_bindings)
            count = target.get('suite_repair_count', 0)
            if type(count) is int and 0 <= count < MAX_SUITE_REPAIRS:
                for repair_kind, repair in (('csv_input_encoding', repair_csv_inputs),
                                            ('text_selector_contract', repair_unsupported_text_selectors)):
                    repaired = repair(suite, proof)
                    if repaired is not None:
                        return self._repair_qa_target(ctx, identity, candidate, target, repaired,
                                                     verification_id, attachments, repair_kind=repair_kind)
                bindings = option_binding_candidates(suite, proof)
                if bindings and set(bindings) == {test['id'] for test in failed}:
                    ctx.log('QA dropdown repair: binding original fixture names to observed option labels.')
                    proposal, binding_meta, _ = self.structured._ask(ctx, identity, {
                        'name': 'qa_option_repair', 'candidate_id': candidate.id,
                        'verification_id': verification_id, 'approved_criteria': criteria,
                        'suite': suite.model_dump(), 'failed_tests': failed, 'source': source,
                        'option_binding_candidates': bindings,
                        'instructions': 'Return QaOptionRepair with bindings of test_id, step_index, label_input_step. '
                            'Correct the failed select and subsequent guessed values of the SAME select using the '
                            'corresponding original earlier fill input index. Supplied candidates prove these exact '
                            'fixture names are unique enabled labels in the DOM. Do not guess generated IDs or provide '
                            'new labels/expected values. Preserve all tests, UAC, actions, inputs and assertions. '
                            'Cover every failed selection; bind later selections consistently in the same proposal.'
                    }, QaOptionRepair, context_limits=replace(self.structured.builder.limits, total_tokens=32768))
                    repaired = repair_option_bindings(suite, proof, proposal)
                    if repaired is not None:
                        with self.db.write() as s:
                            ctx.queue.verify_identity(s, identity)
                            binding_row = self.store.put_json(s, project_id=identity['project_id'], kind='report',
                                name='qa-option-repair.json', document=self.redactor.redact_value(proposal.model_dump()),
                                meta={'producer': 'qa-option-repair', 'verification_id': verification_id,
                                      'target_digest': candidate.target_digest, 'fake': self.fake})
                        return self._repair_qa_target(ctx, identity, candidate, target, repaired,
                            verification_id, [*attachments, binding_meta['context_artifact_id'], binding_row.id],
                            repair_kind='fixture_option_binding')
                    return Outcome('failed', {'failure_kind': 'test_contract', 'verification_id': verification_id,
                        'evidence_artifact_ids': attachments, 'diagnosis_artifact_id': diagnosis.id},
                        error='QA dropdown binding proposal is not supported by original inputs and DOM evidence.')
                prefixes = passed_setup_prefixes(suite, proof)
                if prefixes:
                    ctx.log('QA test fault: selecting setup from passed tests; original assertions remain fixed.')
                    proposal, setup_meta, _ = self.structured._ask(ctx, identity, {
                        'name': 'qa_setup_repair', 'candidate_id': candidate.id,
                        'verification_id': verification_id, 'approved_criteria': criteria,
                        'diagnosis': output.model_dump(), 'suite': suite.model_dump(),
                        'failed_tests': failed, 'source': source, 'passed_setup_prefixes': prefixes,
                        'instructions': 'Return QaSetupRepair, selecting only step indexes from one supplied passed '
                            'test prefix per failed test. Every browser test has empty isolated storage. Before_step=0 '
                            'prepends missing prerequisite customer/task creation. Select only necessary existing steps '
                            'in original order, preserving their exact input values. For an assertion immediately after '
                            'reload, before_step may point to that assertion and select only a prior proven click '
                            'to reopen a transient detail panel: before_step MUST equal failed_step, never zero. '
                            'Do not prepend actions already performed by the failed test before its failed_step. '
                            'Never change or remove any original step/assertion, '
                            'expected value, test ID, UAC or purpose. Cover every failed test once. No code or new inputs.'
                    }, QaSetupRepair, context_limits=replace(self.structured.builder.limits, total_tokens=32768))
                    repaired = repair_test_setup(suite, proof, proposal)
                    if repaired is not None:
                        with self.db.write() as s:
                            ctx.queue.verify_identity(s, identity)
                            setup_row = self.store.put_json(s, project_id=identity['project_id'], kind='report',
                                name='qa-setup-repair.json', document=self.redactor.redact_value(proposal.model_dump()),
                                meta={'producer': 'qa-setup-repair', 'verification_id': verification_id,
                                      'target_digest': candidate.target_digest, 'fake': self.fake})
                        return self._repair_qa_target(ctx, identity, candidate, target, repaired,
                            verification_id, [*attachments, setup_meta['context_artifact_id'], setup_row.id],
                            repair_kind='isolated_test_setup')
        return Outcome('failed', {'verification_id': verification_id, 'evidence_artifact_ids': attachments,
            'qa_status': 'failed', 'failure_kind': 'infrastructure' if output.fault == 'infrastructure' else 'test_contract',
            'diagnosis_artifact_id': diagnosis.id, 'diagnosis_fault': output.fault},
            error='QA diagnosis: ' + output.summary + '. A corrected suite/runner and fresh evidence are required; '
                  'application repair was not requested.')

    def _repair_qa_target(self, ctx, identity, candidate, target, repaired, verification_id, attachments,
                          repair_kind='dom_contract', new_runner=None):
        """Repin an unaccepted target to corrected QA contracts; old proof stays immutable."""
        suite_changed = repaired.digest != target['runner_manifest_digest']
        if new_runner is not None and (repair_kind != 'runner_contract_upgrade' or suite_changed
                or new_runner != self.workspace.harness.identity()):
            raise ValueError('runner refresh must preserve the suite and pin the trusted current runner')
        with self.db.write() as s:
            ctx.queue.verify_identity(s, identity)
            t = s.get(Ticket, identity['ticket_id'])
            workflow = bind_service(self.workflow, s)
            workflow._attempt(s, ctx.actor(), t,
                Attempt(identity['job_id'], identity['generation'], identity['scope_version']), 'qa')
            current = s.get(Candidate, candidate.id)
            if (t.phase != 'qa' or t.workflow.get('candidate_id') != candidate.id or
                    current.target_artifact_id != candidate.target_artifact_id or
                    current.target_digest != candidate.target_digest or current.status != 'review_approved'):
                raise ValueError('QA repair belongs to an obsolete candidate/target')
            version = s.query(TicketVersion).filter_by(ticket_id=t.id, version=t.current_version).one()
            repaired.check_criteria(version.uac)
            readiness = preflight(repaired, version.uac, t.current_version)
            suite_row = self.store.put_json(s, project_id=t.project_id, kind='report', name='qa-suite.json',
                document=repaired.model_dump(), meta={'producer': 'qa-plan', 'fake': self.fake,
                    'repair_verification_id': verification_id, 'previous_suite_artifact_id': target['suite_artifact_id'],
                    'repair_kind': repair_kind})
            previous = s.get(Artifact, candidate.target_artifact_id)
            new_target = self.store.put_json(s, project_id=t.project_id, kind='target_manifest', name='target.json',
                document={**target, 'suite_artifact_id': suite_row.id, 'runner_manifest_digest': repaired.digest,
                    'runner': new_runner if new_runner is not None else target['runner'],
                    'suite_repair_count': target.get('suite_repair_count', 0) + int(suite_changed),
                    'previous_target_artifact_id': previous.id, 'repair_verification_id': verification_id},
                meta={**previous.meta, 'qa_repair_job_id': identity['job_id']})
            # Reviewed source/build/config stay pinned. A changed suite or trusted
            # runner gets a new target and full baseline/candidate execution.
            workflow.attach_target(Actor('service:builder', 'builder', t.project_id), t.id, t.revision,
                candidate.id, build_artifact_id=current.build_artifact_id,
                target_artifact_id=new_target.id, target_digest=new_target.checksum)
            self.workspace._post(s, identity, 'qa-selector-repair:' + identity['job_id'],
                'QA corrected a test contract (' + repair_kind + '). New suite/target require fresh baseline and '
                'candidate execution; source/build unchanged.', [*attachments, suite_row.id], 'qa_plan',
                candidate_id=candidate.id, suite_digest=repaired.digest,
                previous_target_artifact_id=previous.id, target_artifact_id=new_target.id,
                verification_plan=readiness, repair_kind=repair_kind)
            result = {'qa_status': 'suite_repaired' if suite_changed else 'target_refreshed',
                'failure_kind': 'test_contract' if suite_changed else 'infrastructure',
                'repair_kind': repair_kind,
                'verification_id': verification_id, 'target_artifact_id': new_target.id,
                'evidence_artifact_ids': [*attachments, suite_row.id, new_target.id]}
            bind_service(ctx.queue, s).complete(ctx.lease, {**result, 'pipeline_completion': {
                'job_id': identity['job_id'], 'generation': identity['generation']}})
        return Outcome('succeeded', result)

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
        # Remove owned processes/containers/workspaces before their temporary files.
        resources = sorted(snapshot['runtime_ref'].get('resources', []),
                           key=lambda r: r.get('kind') == 'pipeline_directory')
        for resource in resources:
            if resource.get('kind') == 'pipeline_workspace':
                ref = RunRef(resource['project_id'], resource['run_id'])
                cleanup_workspace(sup, ref, snapshot['id'], generation)
            elif resource.get('kind') == 'pipeline_containers':
                from .harness import remove_owned
                for name in resource['names']:
                    remove_owned(sup.sandbox, name, resource['owner'])
            elif resource.get('kind') == 'pipeline_directory':
                if resource.get('job_id') != snapshot['id'] or resource.get('project_id') != snapshot['project_id']:
                    return False
                if resource['purpose'] == 'verification':
                    root = self.workspace.root / '.verification'
                elif resource['purpose'] == 'hermes':
                    root = self.driver.root
                else:
                    return False
                cleanup_directory(root, resource, db=self.db, store=self.store, redactor=self.redactor)
            elif resource.get('kind') != 'process_groups':
                return False
        return True
