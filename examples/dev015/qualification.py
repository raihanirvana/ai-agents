"""Real, finite DEV-015 coffee pilot; approvals are explicit qualification test-user actions.

Run in POSIX/WSL with Docker, the pinned Hermes interpreter and OPENROUTER_API_KEY.
No fake provider, production payments, external push/deploy or manual UAT claim.
Private DB/build/context/evidence stay under --root; print sanitized progress only.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid
from types import SimpleNamespace

from sqlalchemy import select

from app import config  # load local key configuration without printing it
from app.agents.wiring import build_structured_runtime
from app.domain import Actor, ApprovalItem, Workflow
from app.integration.integrator import Integrator
from app.http.service import bind
from app.onboarding.requests import request
from app.onboarding.runtime import OnboardingRuntime
from app.persistence import ArtifactStore, Database, append_message, migrate, cleanup_unpinned
from app.persistence.models import Artifact, Candidate, Job, Message, Preview, Project, Release, Ticket, Verification, TicketVersion
from app.persistence.transactions import bind_service
from app.pipeline.harness import DockerHarness
from app.pipeline.hermes import HermesDriver
from app.pipeline.runtime import PipelineRuntime
from app.pipeline.scheduler import PipelineScheduler
from app.pipeline.workspace import ProductWorkspace
from app.preview.requests import request_preview, request_stop
from app.preview.service import PreviewService
from app.recovery.offline import backup, restore, digest
from app.release.runtime import ReleaseRuntime
from app.release.requests import request_freeze
from app.workspace import WorkspaceSupervisor, fsutil
from app.workers import JobQueue, ProviderLimiter, Supervisor, WorkerConfig
from app.workers.queue import Lease

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / 'apps/backend/tests/workspace/fixtures/reference-react-vite'
LIMITS = {'model_calls': 48, 'tool_calls': 160, 'active_s': 1800, 'output_tokens': 4096, 'total_tokens': 250000}
BRIEF = '''Qualification coffee shop on an existing locked React/Vite reference fixture. Exactly THREE small tickets,
keys profile, menu, transaction. Do not propose bootstrap, deployment, login, backend, payments or dependencies.
Existing src/main.jsx displays MENU (Latte and Espresso) and two-latte total 8.00; src/cart.js exports MENU/cartTotalCents.
All tickets preserve repository Node tests and existing behavior, use cents and deterministic fixtures, no new packages.
profile: independent feature. Add a link data-testid="coffee-contact" with exact text "Contact coffee shop",
href="mailto:hello@example.test". Automated UAC checks text AND href. Do not depend on any ticket.
menu: independent feature. Add a heading data-testid="menu-heading" with exact text "Our coffee menu" above the existing
menu list. Preserve Latte/Espresso entries and cart total. Automated UAC checks the heading and both items. No dependencies.
transaction: depends_on_keys=["menu"] ONLY; do not start until menu is Accepted. Add a button
data-testid="discount-toggle", text "10% off", that toggles the two-latte fixture discount. Separate cart-total element
data-testid="cart-total" has exact text 8.00 initially, 7.20 after one click, 8.00 after two. Two automated UACs.
No manual UACs in this unattended fixture. PO proposes only; scope/UAT/release are test-user commands, never agents.
All coding choices within this brief are settled: simple React state, current files, no clarification required.
'''


def fingerprint(root):
    return {p.relative_to(root).as_posix(): digest(p) for p in sorted(root.rglob('*')) if p.is_file()}


class Pilot:
    def __init__(self, root, args, pid):
        self.root, self.args, self.pid = root, args, pid
        self.db, self.store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
        self.workflow = Workflow(self.db, self.store)
        self.queue = JobQueue(self.db, startable=self.workflow.startable, retry_backoff_s=.2)
        self.structured, self.threads, _ = build_structured_runtime(self.db, self.store, self.workflow, self.queue,
            config_path=REPO / 'examples/dev010/models.openrouter.example.json')
        workspace = ProductWorkspace(self.db, self.store, self.workflow, root / 'workspaces',
            DockerHarness(WorkspaceSupervisor(root / 'workspaces').sandbox), self.structured.redactor)
        driver = HermesDriver(args.hermes_python, root / 'hermes-homes', self.structured.client)
        driver.validate_install()
        pipeline = PipelineRuntime(self.structured, workspace, driver)
        self.preview = PreviewService(self.db, self.store, root / 'workspaces', owner='preview:dev015-' + pid)
        self.integrator = Integrator(self.db, self.store, self.workflow, root / 'workspaces')
        self.worker = Supervisor(self.db, self.store,
            {'structured': self.structured, 'pipeline': pipeline,
             'onboarding': OnboardingRuntime(self.db, self.store, root / 'workspaces', self.structured.redactor),
             'release': ReleaseRuntime(self.db, self.store, self.workflow, root / 'workspaces', self.structured.redactor)},
            queue=self.queue, workflow=self.workflow, limiter=ProviderLimiter(),
            config=WorkerConfig(worker_id='worker:dev015-' + uuid.uuid4().hex, heartbeat_s=.5))
        self.scheduler = PipelineScheduler(self.db, self.queue, self.workflow, limits=LIMITS)
        self.worker.maintenance.extend([self.threads.ensure_reply_jobs, self.scheduler.tick, self.preview.tick, self.integrator.tick])
        self.user = Actor('user:dev015-qualification', 'user', pid)
        self.deadline = time.monotonic() + 3600
        self.last = None

    def run_until(self, predicate, *, seconds=1200, hook=None, ignored_terminal=()):
        limit = min(self.deadline, time.monotonic() + seconds)
        while time.monotonic() < limit:
            self.worker.tick()
            with self.db.read() as s:
                jobs = list(s.scalars(select(Job)))
                cost = sum(j.usage.get('cost_usd', 0) for j in jobs)
                if cost > 1:
                    raise RuntimeError('pilot USD 1 supervision limit reached')
                state = [(t.number, t.phase, t.blocker) for t in s.scalars(select(Ticket).order_by(Ticket.number))]
                if state != self.last:
                    print(json.dumps({'tickets': state, 'cost_usd': cost}), flush=True)
                    self.last = state
                if predicate(s):
                    return
                parents = {j.parent_job_id for j in jobs if j.parent_job_id}
                fatal = [j for j in jobs if j.id not in parents and j.id not in ignored_terminal and
                    (not j.ticket_id or s.get(Ticket, j.ticket_id).current_version == j.scope_version) and
                    (j.stage != 'development' or '@' not in j.idempotency_key or
                     j.idempotency_key.split('@', 1)[1].split('#', 1)[0] == s.get(Project, j.project_id).workflow['accepted_tip'][:12]) and (j.status == 'stopped' or
                    (j.status == 'failed' and (j.result or {}).get('needs_human')))]
                if fatal:
                    raise RuntimeError('terminal job needs attention: ' + repr([(j.stage, j.result) for j in fatal]))
            if hook:
                hook(self)
            time.sleep(.1)
        raise RuntimeError('finite pilot deadline reached')

    def close(self):
        self.worker.shutdown(timeout_s=30)
        self.preview.shutdown()
        self.integrator.shutdown()
        self.db.dispose()

    def checkpoint(self, facts):
        result = self.summary(facts)
        temporary = self.root / 'result.json.partial'
        temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
        os.replace(temporary, self.root / 'result.json')
        return result

    def chat(self, task, text, tid=None):
        with self.db.write() as s:
            m, _ = append_message(s, project_id=self.pid, thread_id='chat:' + self.pid,
                sender=self.user.id, recipient='role:po', ticket_id=tid, body=text)
            j = self.queue.enqueue(session=s, project_id=self.pid, ticket_id=tid, role='po', stage='chat',
                lane='interactive', runtime='structured', limits=LIMITS, idempotency_key='pilot-chat:' + m.id,
                payload={'task': task, 'request': text, 'message_id': m.id})
        return j.id

    def preview_target(self, tid):
        with self.db.write() as s:
            t = s.get(Ticket, tid)
            c = s.get(Candidate, t.workflow['candidate_id'])
            p = request_preview(s, self.store, ticket_id=t.id, candidate_id=c.id, user_id=self.user.id, port=5193)
        self.run_until(lambda s: s.get(Preview, p.id).status in ('ready', 'failed'), seconds=60)
        with self.db.read() as s:
            p = s.get(Preview, p.id)
            if p.status != 'ready':
                raise RuntimeError('preview failed: ' + str(p.error))
        # Browser execution is external to the generated app; fetch checks serving the pinned build.
        from urllib.request import urlopen
        with urlopen('http://localhost:5193/', timeout=10) as response:
            if response.status != 200 or b'<div id="root"' not in response.read():
                raise RuntimeError('preview HTTP check failed')
        with self.db.write() as s:
            request_stop(s, p.id, self.user.id)
        self.run_until(lambda s: s.get(Preview, p.id).status == 'stopped', seconds=60)
        return {'id': p.id, 'candidate_id': c.id, 'target_digest': c.target_digest, 'http_status': 200}

    def accept(self, tid):
        with self.db.read() as s:
            t = s.get(Ticket, tid)
            c = s.get(Candidate, t.workflow['candidate_id'])
            v = s.get(Verification, c.preview['verification_id'])
        self.workflow.accept_uat(self.user, tid, t.revision, c.id, t.current_version,
            c.target_artifact_id, c.target_digest, v.id, c.evidence_artifact_ids)
        self.run_until(lambda s: s.get(Ticket, tid).phase == 'accepted', seconds=60)

    def summary(self, facts):
        with self.db.read() as s:
            jobs = list(s.scalars(select(Job).order_by(Job.created_at)))
            candidates = list(s.scalars(select(Candidate)))
            artifacts = list(s.scalars(select(Artifact)))
            return self.structured.redactor.redact_value({'ticket': 'DEV-015', 'provider': 'openrouter',
                'model': 'openai/gpt-4.1-mini', 'project_id': self.pid, 'facts': facts,
                'manual_user_uat': False, 'approvals_actor': self.user.id, 'independent_review': 'NOT_REVIEWED',
                'usage': {k: sum(j.usage.get(k, 0) for j in jobs) for k in ('model_calls','tool_calls','input_tokens','output_tokens','total_tokens','cost_usd')},
                'unknown_usage_fields': sorted({f for j in jobs for f in j.usage.get('_unknown', [])}),
                'tickets': [{'id': t.id, 'number': t.number, 'phase': t.phase, 'scope_version': t.current_version} for t in s.scalars(select(Ticket))],
                'jobs': [{'id': j.id, 'stage': j.stage, 'status': j.status, 'generation': j.lease_generation,
                          'context_artifact_id': j.context_artifact_id, 'usage': j.usage} for j in jobs],
                'candidates': [{'id': c.id, 'ticket_id': c.ticket_id, 'status': c.status, 'sha': c.commit_sha,
                    'base_sha': c.base_sha, 'target_digest': c.target_digest} for c in candidates],
                'releases': [{'id': r.id, 'status': r.status, 'accepted_tip': r.accepted_tip, 'target_digest': r.target_digest,
                    'target_artifact_id': r.target_artifact_id, 'evidence_ids': r.evidence_artifact_ids} for r in s.scalars(select(Release))],
                'artifacts': len(artifacts), 'messages': [{'id': m.id, 'sender': m.sender, 'recipient': m.recipient,
                    'intent': m.meta.get('intent'), 'reply_to': m.reply_to, 'kind': m.kind,
                    'fake': m.meta.get('fake', False)}
                    for m in s.scalars(select(Message)) if m.meta.get('intent') or m.reply_to or m.kind == 'input_request']})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='continue the same isolated DB/usage after a stopped worker')
    parser.add_argument('--revise-transaction', action='store_true',
                        help='explicit test-user decision: propose/approve clarified transaction scope after auditing v1')
    parser.add_argument('--authorize-transaction-repair', action='store_true',
                        help='explicit test-user decision: one bounded repair to restore the accepted Node tests')
    parser.add_argument('--extend-transaction-budget', action='store_true',
                        help='explicit test-user decision: one +100000 total-token extension, preserving all usage')
    args = parser.parse_args()
    if not os.environ.get('OPENROUTER_API_KEY'):
        raise RuntimeError('OpenRouter key is required; no fake substitution')
    root = args.root.resolve()
    from app.workspace.manifest import parse_manifest, reference_manifest_dict
    manifest = parse_manifest(reference_manifest_dict())
    if args.resume:
        saved = json.loads((root / 'result.json').read_text())
        pid, facts = saved['project_id'], saved['facts']
        if facts.get('completed'):
            raise RuntimeError('completed pilot is immutable; use a new isolated root')
        pilot = Pilot(root, args, pid)
        with pilot.db.read() as s:
            source = Path(s.get(Project, pid).repo_ref)
        source_before = json.loads((root / 'source-fingerprint.json').read_text())
        facts['resumed_preserving_usage'] = True
    else:
        root.mkdir(parents=True, exist_ok=False, mode=0o700)
        pid = uuid.uuid4().hex
        # The source is a trusted fixture we create, and is thereafter read only.
        seed = WorkspaceSupervisor(root / 'seed')
        initial = seed.create_project(pid)
        run = seed.start_attempt(pid, ticket_id='fixture', scope_version=1, role='developer', attempt=1,
            generation=1, lease_id='fixture-bootstrap', manifest=manifest)
        for entry in fsutil.scan_tree(FIXTURE):
            if entry.kind == 'file':
                seed.write_file(run.ref, run.credential, entry.rel, (FIXTURE / entry.rel).read_bytes())
        record = seed.submit_candidate(run.ref, run.credential, 'Trusted coffee reference fixture (not model output)')
        broker = seed.broker(pid)
        broker._bare('update-ref', 'refs/heads/accepted', record['sha'], initial)
        seed.stop_run(run.ref, 'fixture prepared')
        source = root.with_name(root.name + '-source')
        broker.run(['-c','protocol.file.allow=always','clone','--no-hardlinks',str(broker.repo),str(source)])
        (source / 'local-notes.txt').write_text('Untracked user fixture; explicit exclusion from onboarding.\n')
        source_before = fingerprint(source)
        migrate.upgrade(root / 'app.sqlite3')
        pilot = Pilot(root, args, pid)
        facts = {'fixture_bootstrap': True, 'source_sha': record['sha']}
        pilot.workflow.create_project(pilot.user, name='DEV-015 coffee fixture pilot', mode='existing', repo_ref=str(source), brief=BRIEF)
    (root / 'source-fingerprint.json').write_text(json.dumps(source_before), encoding='utf-8')
    pilot.checkpoint(facts)
    try:
        if not facts.get('backup_restore'):
            with pilot.db.write() as s:
                project = s.get(Project, pid)
                if project.workflow.get('onboarding') == 'ready':
                    job = None
                elif project.workflow.get('onboarding_detail', {}).get('job_id'):
                    job = s.get(Job, project.workflow['onboarding_detail']['job_id'])
                else:
                    _, job = request(s, bind(s, pilot.store, pilot.structured.redactor), pilot.store, pilot.user,
                        project.revision, manifest.to_dict(), 'pilot-baseline')
            if job:
                pilot.run_until(lambda s: s.get(Job, job.id).status in ('succeeded','failed','stopped'), seconds=500)
            with pilot.db.read() as s:
                if s.get(Project, pid).workflow.get('onboarding') != 'ready':
                    raise RuntimeError('baseline was blocked')
                proposal_job = facts.get('po_proposal_job')
            if not proposal_job:
                proposal_job = pilot.chat('breakdown', BRIEF)
                facts['po_proposal_job'] = proposal_job
            pilot.run_until(lambda s: s.get(Job, proposal_job).status == 'succeeded', seconds=120)
            with pilot.db.read() as s:
                ids = s.get(Job, proposal_job).result['key_map']
                tickets = [s.get(Ticket, tid) for tid in ids.values()]
                assert set(ids) == {'profile','menu','transaction'} and len(tickets) == 3
            unapproved = [t for t in tickets if t.phase == 'scope_review']
            if unapproved:
                pilot.workflow.approve_scope(pilot.user, [ApprovalItem(t.id, t.current_version, t.revision) for t in unapproved])
            def overlap(p):
                with p.db.read() as s:
                    menu = s.get(Ticket, ids['menu'])
                if (menu.blocker or {}).get('reason') == 'needs_human':
                    if facts.get('menu_scope_correction'):
                        raise RuntimeError('corrected menu scope exhausted its bounded repairs')
                    correction = p.chat('revise', 'Revise the menu scope to resolve a QA ambiguity. Add the heading '
                        '[data-testid="menu-heading"] exact text "Our coffee menu" above the existing list. '
                        'Preserve existing list item text exactly "Espresso: 2.50" and "Latte: 4.00", and preserve '
                        'the existing paragraph text "Sample cart total: 8.00". Prices must stay visible; do not remove '
                        'prices or change MENU/cart math or Node tests/dependencies. Automated UAC: new heading; '
                        'both original name-price strings; original paragraph. No dependencies or manual UAC. '
                        'This explicit revision replaces ambiguous display criteria; do not keep exact plain-name '
                        'list item assertions or require a new cart-total test id. No scope is approved by PO.', menu.id)
                    p.run_until(lambda s: s.get(Job, correction).status == 'succeeded', seconds=120)
                    with p.db.read() as s:
                        proposal_id = s.get(Job, correction).result['proposal_id']
                        document = s.get(Message, proposal_id).meta['document']
                        current = s.get(Ticket, menu.id)
                    assert document['dependencies'] == [] and all(u.get('mode') == 'automated' for u in document['uac'])
                    assert all(v in json.dumps(document['uac']) for v in ('4.00','2.50','8.00','menu-heading'))
                    revised = p.workflow.decide_proposal(p.user, menu.id, current.revision, proposal_id, True)
                    p.workflow.approve_scope(p.user, [ApprovalItem(revised.id, revised.current_version, revised.revision)])
                    facts['menu_scope_correction'] = {'job_id': correction, 'proposal_id': proposal_id,
                        'old_version': menu.current_version, 'new_version': revised.current_version,
                        'reason': 'explicit preservation of original name-price display and paragraph; old QA expected plain names'}
                if facts.get('po_chat_job'):
                    return
                with p.db.read() as s:
                    j = s.scalar(select(Job).where(Job.stage == 'development', Job.status == 'running'))
                if j:
                    identity = p.queue.verify(Lease(j.id, j.lease_owner, j.lease_generation))
                    directed = p.threads.send(identity, to_role='technical-lead', thread_id='pilot-handoff:' + pid,
                        category='clarification', body='Confirm the approved coffee fixture stays stateless and uses cents; no payment or backend is allowed.',
                        needs_reply=True, idempotency_key='pilot-lead-question')
                    facts['directed_lead_message'] = directed.message_id
                    facts['directed_lead_reply_job'] = directed.reply_job_id
                    facts['po_chat_while_developer_job'] = j.id
                    facts['po_chat_job'] = p.chat('revise', 'Propose the SAME current scope/UAC with a clearer description: fixture only, existing locked dependencies, no backend/payments. Do not add dependencies or criteria. This is a proposal only.', j.ticket_id)
            pilot.run_until(lambda s: all(s.get(Ticket, ids[k]).phase == 'uat' for k in ('profile','menu')), hook=overlap)
            with pilot.db.read() as s:
                assert s.get(Ticket, ids['transaction']).phase == 'ready'
                old_profile = s.get(Candidate, s.get(Ticket, ids['profile']).workflow['candidate_id'])
            facts['independent_menu_completed_before_profile_accepted'] = True
            facts['transaction_waited_for_menu_acceptance'] = True
            facts['preview_before_restore'] = pilot.preview_target(ids['profile'])
            pilot.run_until(lambda s: all(s.get(Job, facts[k]).status == 'succeeded' for k in ('po_chat_job','directed_lead_reply_job')), seconds=120)
            facts['preview_before_restore']['old_candidate'] = old_profile.id
            pilot.close()
            snapshot, recovered = root.with_name(root.name + '-backup'), root.with_name(root.name + '-restored')
            inv = backup(db_path=root/'app.sqlite3', artifact_root=root/'artifacts', workspace_root=root/'workspaces',
                destination=snapshot, offline=True)
            restored = restore(snapshot=snapshot, destination=recovered, offline=True)
            facts['backup_restore'] = {'files': len(inv['files']), 'pinned_artifacts': len(inv['pins']), **restored}
            pilot = Pilot(recovered, args, pid)
            (recovered / 'source-fingerprint.json').write_text(json.dumps(source_before), encoding='utf-8')
            pilot.checkpoint(facts)
            facts['preview_after_restore'] = pilot.preview_target(ids['profile'])
            assert facts['preview_after_restore']['target_digest'] == facts['preview_before_restore']['target_digest']
            # Same-scope feedback: recheck the already approved mailto criterion, no silent scope change.
            with pilot.db.read() as s:
                t = s.get(Ticket, ids['profile'])
            pilot.workflow.request_changes(pilot.user, t.id, t.revision, t.workflow['candidate_id'],
                'Recheck the approved mailto href and contact label, preserve all menu/cart behavior, and submit a fresh tested candidate. No scope change.')
            facts['feedback_superseded_candidate'] = old_profile.id
            pilot.accept(ids['menu'])  # rebases any old-base candidate and unblocks dependent transaction
        else:
            # Resume the restored root without repeating approvals, feedback, backup or resetting usage.
            with pilot.db.read() as s:
                ids = s.get(Job, facts['po_proposal_job']).result['key_map']
                old_profile = s.get(Candidate, facts['feedback_superseded_candidate'])
            facts['resumed_after_restore'] = True
            if args.revise_transaction and not facts.get('transaction_scope_correction'):
                with pilot.db.read() as s:
                    prior = [j for j in s.scalars(select(Job).where(Job.ticket_id == ids['transaction'], Job.stage == 'chat')
                             .order_by(Job.created_at, Job.id))
                             if j.runtime_ref.get('payload', {}).get('request', '').startswith('Revise transaction to resolve incompatible markup')]
                    halted = tuple(j.id for j in s.scalars(select(Job).where(Job.ticket_id == ids['transaction']))
                                   if j.status == 'stopped' and (j.result or {}).get('reason') == 'budget_exhausted')
                correction = prior[-1].id if prior else pilot.chat('revise', 'Revise transaction to resolve incompatible markup and an incorrect '
                    'QA interaction sequence. Preserve the existing original paragraph EXACT text "Sample cart total: 8.00" '
                    'and menu items/prices, heading and mailto contact. Keep exactly that original paragraph; put the new '
                    'separate dynamic total in a span data-testid="cart-total" outside that paragraph. Keep the button '
                    'data-testid="discount-toggle", exact text "10% off". Initial span text is 8.00. Within ONE fresh-page '
                    'test, click once and assert 7.20, click AGAIN and assert 8.00. Every test starts fresh, so two-click '
                    'coverage cannot rely on an earlier test. UAC must explicitly require this complete two-click sequence '
                    'and preservation of the original paragraph/contact. Keep cents, stateless React state, existing Node '
                    'tests and locked dependencies, no backend/payment. Dependency is ONLY menu ticket ' + ids['menu'] +
                    '. All UAC automated. This is a proposal; user must approve.', ids['transaction'])
                facts['transaction_scope_correction_job'] = correction
                pilot.run_until(lambda s: s.get(Job, correction).status == 'succeeded', seconds=120, ignored_terminal=halted)
                with pilot.db.read() as s:
                    proposal_id = s.get(Job, correction).result['proposal_id']
                    document = s.get(Message, proposal_id).meta['document']
                    current = s.get(Ticket, ids['transaction'])
                    decision = s.scalar(select(Message).where(Message.reply_to == proposal_id,
                        Message.sender == pilot.user.id, Message.body == 'accepted'))
                    base_version = s.get(Message, proposal_id).meta['base_version']
                assert document['dependencies'] == [ids['menu']]
                assert all(u.get('mode') == 'automated' for u in document['uac'])
                assert all(v in json.dumps(document) for v in ('Sample cart total: 8.00', 'span', '7.20', '8.00'))
                if decision:
                    assert current.current_version == base_version + 1
                    with pilot.db.read() as s:
                        installed = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == current.id,
                            TicketVersion.version == current.current_version))
                        assert installed.uac == document['uac'] and installed.description == document['description']
                    revised = current
                    if revised.phase == 'scope_review':
                        pilot.workflow.approve_scope(pilot.user, [ApprovalItem(revised.id, revised.current_version, revised.revision)])
                else:
                    revised = pilot.workflow.decide_proposal(pilot.user, current.id, current.revision, proposal_id, True)
                    pilot.workflow.approve_scope(pilot.user, [ApprovalItem(revised.id, revised.current_version, revised.revision)])
                facts['transaction_scope_correction'] = {'job_id': correction, 'proposal_id': proposal_id,
                    'old_version': base_version, 'new_version': revised.current_version,
                    'reason': 'preserve accepted paragraph; separate span; complete two-click sequence in one fresh-page test; v1 budget exhaustion retained'}
                pilot.checkpoint(facts)
            if args.extend_transaction_budget and not facts.get('transaction_budget_extension'):
                with pilot.db.read() as s:
                    current = s.get(Ticket, ids['transaction'])
                    halted_job = s.scalar(select(Job).where(Job.ticket_id == current.id,
                        Job.scope_version == current.current_version, Job.status == 'stopped')
                        .order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
                    assert halted_job and halted_job.result.get('reason') == 'budget_exhausted'
                    assert halted_job.result.get('limit') == 'total_tokens'
                    old_cap = halted_job.limits['total_tokens']
                    assert old_cap == LIMITS['total_tokens'], 'only one finite token extension is authorized'
                    old_usage = pilot.queue.budget_usage(s, halted_job)
                retry_id = pilot.queue.extend_budget(halted_job.id, user=pilot.user.id,
                    additions={'total_tokens': 100000}, authorization_id='dev015-v2-unit-test-recovery')
                facts['transaction_budget_extension'] = {'job_id': retry_id, 'parent_job_id': halted_job.id,
                    'scope_version': current.current_version, 'old_cap': old_cap, 'new_cap': old_cap + 100000,
                    'usage_before': old_usage, 'reason': 'one explicit bounded continuation after preserving mandatory Node tests'}
                pilot.checkpoint(facts)
            if args.authorize_transaction_repair and not facts.get('transaction_repair_authorization'):
                broker = WorkspaceSupervisor(pilot.root / 'workspaces').broker(pid)
                baseline_tests = broker._bare('show', broker.accepted_sha() + ':test/cart.test.js').decode()
                with pilot.db.write() as s:
                    guide, _ = append_message(s, project_id=pid, ticket_id=ids['transaction'],
                        thread_id='chat:' + pid, sender=pilot.user.id, recipient='role:developer',
                        idempotency_key='dev015-restore-pure-node-tests',
                        body='Concrete recovery guidance: test/cart.test.js was replaced with a Playwright test, '
                        'but Playwright is not a locked dependency and the required two Node tests disappeared. '
                        'Restore this exact accepted repository unit test file; do not change its assertions or add '
                        'dependencies. Leave the approved span/button implementation; QA separately runs browser tests. '
                        'Run npm test/build, then submit a fresh candidate. Accepted test file:\n' + baseline_tests)
                    current = s.get(Ticket, ids['transaction'])
                if current.workflow.get('repair_limit', 3) == 3:
                    pilot.workflow.authorize_repair(pilot.user, current.id, current.revision, additional_cycles=1)
                else:
                    assert current.workflow['repair_limit'] == 4, 'only one extra repair is authorized for this pilot'
                facts['transaction_repair_authorization'] = {'message_id': guide.id, 'additional_cycles': 1,
                    'old_limit': 3, 'new_limit': 4, 'scope_version': current.current_version,
                    'caps_unchanged': True, 'guidance': 'restore exact accepted pure Node tests; no direct code or QA suite edit'}
                pilot.checkpoint(facts)
        with pilot.db.read() as s:
            profile_accepted = s.get(Ticket, ids['profile']).phase == 'accepted'
        if not profile_accepted:
            pilot.run_until(lambda s: s.get(Ticket, ids['profile']).phase == 'uat')
        with pilot.db.read() as s:
            t = s.get(Ticket, ids['profile'])
            new = s.get(Candidate, t.workflow['candidate_id'])
            assert new.id != old_profile.id and new.target_digest != old_profile.target_digest and new.base_sha != old_profile.base_sha
        facts['rebased_profile'] = {'old_candidate_id': old_profile.id, 'new_candidate_id': new.id,
                                   'new_target_digest': new.target_digest, 'new_base_sha': new.base_sha}
        if not profile_accepted:
            facts['preview_profile_after_feedback'] = pilot.preview_target(ids['profile'])
            pilot.accept(ids['profile'])
        with pilot.db.read() as s:
            transaction_accepted = s.get(Ticket, ids['transaction']).phase == 'accepted'
        if not transaction_accepted:
            pilot.run_until(lambda s: s.get(Ticket, ids['transaction']).phase == 'uat')
            facts['preview_transaction'] = pilot.preview_target(ids['transaction'])
            pilot.accept(ids['transaction'])
        with pilot.db.read() as s:
            release = s.scalar(select(Release).where(Release.project_id == pid))
        if release is None:
            with pilot.db.write() as s:
                services = SimpleNamespace(queue=pilot.queue, workflow=bind_service(pilot.workflow, s))
                job, _, _ = request_freeze(s, services, pilot.user, s.get(Project, pid).revision, 'pilot-release')
            pilot.run_until(lambda s: s.get(Job, job.id).status in ('succeeded','failed','stopped'), seconds=300)
            with pilot.db.read() as s:
                release = s.scalar(select(Release).where(Release.project_id == pid))
        assert release and release.status in ('draft', 'approved'), 'release regression did not pass'
        if release.status == 'draft':
            pilot.workflow.approve_release(pilot.user, release.id, release.revision, release.target_artifact_id,
                release.target_digest, release.evidence_artifact_ids)
        from datetime import timedelta
        cleanup = cleanup_unpinned(pilot.db, pilot.store, project_id=pid, min_age=timedelta(0), dry_run=False)
        facts['cleanup'] = {'kept_pinned': len(cleanup.kept_pinned), 'removed': len(cleanup.removed)}
        facts['source_unchanged'] = source_before == fingerprint(source)
        assert facts['source_unchanged']
        facts['completed'] = True
        return 0
    finally:
        result = pilot.checkpoint(facts)
        print('pilot completed=' + str(facts.get('completed', False)) + ' usage=' + json.dumps(result['usage']), flush=True)
        pilot.close()


if __name__ == '__main__':
    raise SystemExit(main())
