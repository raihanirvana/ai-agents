"""DEV-013 qualification: real onboarding + provider pipeline on a LOCAL TEST SOURCE exported from DEV-010.

Creates a dedicated fixture source from the prior pilot candidate, including dirty user files. Never changes the
pilot managed repo. --accept-fixture-uat explicitly exercises a TEST USER approval; it is not manual user UAT.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import time
import uuid
from sqlalchemy import select
from app import config  # load local provider config without printing secrets
from app.agents import Redactor
from app.domain import Actor, Workflow, ApprovalItem
from app.http.service import bind
from app.onboarding.requests import request
from app.onboarding.runtime import OnboardingRuntime
from app.onboarding.source import inspect_source
from app.persistence import Database, ArtifactStore, migrate
from app.persistence.models import Project, Ticket, Candidate, Verification, Job
from app.workers import JobQueue, ProviderLimiter, Supervisor, WorkerConfig
from app.workspace import WorkspaceSupervisor
from app.workspace.gitbroker import GitBroker
from app.workspace.manifest import reference_manifest_dict
from app.integration.integrator import Integrator

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-root', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    parser.add_argument('--model', default='openai/gpt-4.1-mini')
    parser.add_argument('--accept-fixture-uat', action='store_true')
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    prior = json.loads((args.pilot_root / 'result.json').read_text())
    if prior['phase'] != 'uat':
        raise ValueError('source pilot must have a verified UAT candidate')
    candidate_sha = prior['candidates'][-1]['sha']
    pilot_broker = WorkspaceSupervisor(args.pilot_root / 'workspaces').broker(prior['project_id'])
    source = root / 'source-fixture'
    pilot_broker.export_commit(candidate_sha, source)
    git = GitBroker(root / 'unused.git', root / 'source-git-home')
    git.run(['init', '--template=', str(source)])
    git.run(['add', '.'], cwd=source)
    git.run(['commit', '-m', 'DEV-010 verified pilot source fixture: ' + candidate_sha], cwd=source)
    (source / 'README.md').write_text('Local dirty notes; preserve this file. Do not push or deploy.\n')
    (source / 'src/main.jsx').write_text((source / 'src/main.jsx').read_text() + '\n// Uncommitted user notes, not selected for import.\n')
    before, _ = inspect_source(git, source)
    import hashlib
    def fingerprint():
        return {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in source.rglob('*') if p.is_file()}
    bytes_before = fingerprint()
    migrate.upgrade(root / 'app.sqlite3')
    db, store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
    workflow = Workflow(db, store)
    pid = uuid.uuid4().hex
    user = Actor('user:qualification-fixture', 'user', pid)
    project = workflow.create_project(user, name='DEV-013 existing pilot fixture', mode='existing', repo_ref=str(source),
        brief='Add one feature to a verified existing coffee app. Preserve repo tests and user source. Qualification fixture only.')
    queue = JobQueue(db, lease_s=30, startable=workflow.startable)
    with db.write() as s:
        _, job = request(s, bind(s, store, Redactor()), store, user, project.revision, reference_manifest_dict(), 'qualification')
    onboarding = OnboardingRuntime(db, store, root / 'workspaces', Redactor())
    supervisor = Supervisor(db, store, {'onboarding': onboarding}, queue=queue, limiter=ProviderLimiter(),
        config=WorkerConfig(worker_id='worker:dev013-baseline', heartbeat_s=.5))
    try:
        deadline = time.monotonic() + 500
        while time.monotonic() < deadline:
            supervisor.tick()
            with db.read() as s:
                current = s.get(Job, job.id)
                if current.status in ('succeeded', 'failed', 'stopped'):
                    break
            time.sleep(.1)
        supervisor.wait_idle(10)
    finally:
        supervisor.shutdown()
    with db.read() as s:
        p = s.get(Project, pid)
    if p.workflow.get('onboarding') != 'ready':
        raise ValueError('baseline not ready: ' + str(p.workflow))
    document = {'title': 'Show a receipt confirmation on the existing coffee app',
        'description': 'Keep all existing menu/cart/discount behavior. Add a button with data-testid="receipt-button" '
            'labelled "Show receipt". Clicking it shows a separate element data-testid="receipt-status" whose exact '
            'text is "Receipt ready". Do not change existing tests or dependencies.',
        'uac': [{'id': 'UAC-receipt', 'mode': 'automated', 'text': 'Click [data-testid="receipt-button"] and '
                 'assert [data-testid="receipt-status"] has exact text "Receipt ready".'}], 'dependencies': []}
    ticket = workflow.create_ticket(user, document)
    workflow.approve_scope(user, [ApprovalItem(ticket.id, ticket.current_version, ticket.revision)])
    # Reuse the already pinned provider/Hermes pipeline qualification harness; this does not bootstrap a NEW repo.
    spec = importlib.util.spec_from_file_location('dev010_qualification', REPO / 'examples/dev010/qualification.py')
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    args.case = 'existing-receipt'
    rc = previous.execute(args, root, db, store, workflow, ticket, pid, {'sha': p.workflow['accepted_tip']},
                          WorkspaceSupervisor(root / 'workspaces'))
    # execute closes its DB; reopen and reconcile using production UAT + integrator commands.
    db = Database(root / 'app.sqlite3')
    workflow = Workflow(db, store)
    accepted = False
    try:
        if rc == 0 and args.accept_fixture_uat:
            with db.read() as s:
                t = s.get(Ticket, ticket.id)
                c = s.get(Candidate, t.workflow['candidate_id'])
                v = s.scalar(select(Verification).where(Verification.candidate_id == c.id, Verification.status == 'passed'))
                target_id, target_digest = c.target_artifact_id, c.target_digest
                ids = list(c.evidence_artifact_ids)
            workflow.accept_uat(user, t.id, t.revision, c.id, t.current_version, target_id, target_digest, v.id, ids)
            integrator = Integrator(db, store, workflow, root / 'workspaces')
            try:
                integrator.tick()
            finally:
                integrator.shutdown()
            with db.read() as s:
                accepted = s.get(Ticket, ticket.id).phase == 'accepted'
        after, _ = inspect_source(git, source)
        unchanged = before == after and bytes_before == fingerprint()
        summary = json.loads((root / 'result.json').read_text())
        summary.update(ticket='DEV-013', source_pilot_candidate=candidate_sha, source_fixture_sha=before['source_sha'],
            source_dirty=before['dirty'], source_unchanged=unchanged, fixture_uat_approval=bool(args.accept_fixture_uat),
            user_uat_approval=False, accepted_by_production_integrator=accepted,
            phase='accepted' if accepted else summary['phase'], onboarding_report=p.workflow['onboarding_detail']['report_artifact_id'])
        (root / 'result.json').write_text(json.dumps(summary, indent=2))
        print('source_unchanged=' + str(unchanged) + ' accepted_by_fixture_user=' + str(accepted), flush=True)
        return 0 if unchanged and rc == 0 and (accepted or not args.accept_fixture_uat) else 2
    finally:
        db.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
