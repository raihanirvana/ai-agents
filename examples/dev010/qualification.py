"""Opt-in paid qualification in an isolated product DB. Never reads or imports a user's target repository.

PYTHONPATH=apps/backend python examples/dev010/qualification.py --root /linux/data/dev010-run --case feature --hermes-python ...
The bundled fixture is a TRUSTED TEST BASE, not an onboarding/integration implementation or user UAT approval.
"""
import argparse
import json
import os
from pathlib import Path
import time
import uuid
from sqlalchemy import select
from app import config  # load local provider configuration; never print its values
from app.domain import Actor, Workflow, ApprovalItem
from app.persistence import ArtifactStore, Database, migrate, scope_usage
from app.persistence.models import Project, Ticket, Job, Candidate, Verification
from app.workspace import WorkspaceSupervisor
from app.workspace.manifest import reference_manifest_dict, parse_manifest
from app.workspace import fsutil
from app.agents.wiring import build_structured_runtime
from app.pipeline.workspace import ProductWorkspace
from app.pipeline.harness import DockerHarness
from app.pipeline.hermes import HermesDriver
from app.pipeline.runtime import PipelineRuntime
from app.pipeline.scheduler import PipelineScheduler
from app.workers import JobQueue, ProviderLimiter, Supervisor, WorkerConfig

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / 'apps/backend/tests/workspace/fixtures/reference-react-vite'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--case', choices=('feature', 'bug'), required=True)
    parser.add_argument('--hermes-python', type=Path, required=True)
    parser.add_argument('--model', help='Explicit model; resume preserves the prior model unless this is supplied')
    parser.add_argument('--resume', action='store_true', help='Restart the same isolated run, preserving DB, usage and checkpoints')
    parser.add_argument('--extend-model-calls', type=int, default=0, help='Explicit fixture-user budget extension, 1..16 additional calls')
    args = parser.parse_args()
    root = args.root.resolve()
    if args.resume:
        return resume(args, root)
    if args.extend_model_calls:
        raise ValueError('budget extension requires --resume of a stopped qualification job')
    args.model = args.model or 'qwen/qwen3-coder-30b-a3b-instruct'
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    database_path = root / 'app.sqlite3'
    migrate.upgrade(database_path)
    db, store = Database(database_path), ArtifactStore(root / 'artifacts')
    workflow = Workflow(db, store)
    pid = uuid.uuid4().hex
    user = Actor('user:qualification', 'user', pid)
    project = workflow.create_project(user, name='DEV-010 ' + args.case, mode='new',
        brief='Isolated DEV-010 qualification against the bundled React/Vite coffee fixture. '
              'Use existing locked dependencies. User scope/UAT/release approval cannot come from agents.')
    supervisor_workspace = WorkspaceSupervisor(root / 'workspaces')
    initial = supervisor_workspace.create_project(pid)
    manifest = parse_manifest(reference_manifest_dict())
    # Fixture bootstrap only: no scheduler, model, or approval state lives in this standalone run.
    bootstrap = supervisor_workspace.start_attempt(pid, ticket_id='qualification-fixture', scope_version=1,
        role='developer', attempt=1, generation=1, lease_id='fixture-bootstrap', manifest=manifest)
    source = supervisor_workspace.src_dir(bootstrap.ref)
    for entry in fsutil.scan_tree(FIXTURE):
        if entry.kind == 'file':
            raw = (FIXTURE / entry.rel).read_bytes()
            if args.case == 'bug' and entry.rel == 'src/cart.js':
                raw = raw.replace(b'item.priceCents * line.qty', b'(item.priceCents + 100) * line.qty')
            supervisor_workspace.write_file(bootstrap.ref, bootstrap.credential, entry.rel, raw)
    record = supervisor_workspace.submit_candidate(bootstrap.ref, bootstrap.credential, 'Trusted qualification fixture base')
    broker = supervisor_workspace.broker(pid)
    broker._bare('update-ref', 'refs/heads/accepted', record['sha'], initial)
    supervisor_workspace.stop_run(bootstrap.ref, 'fixture ready')
    workflow.initialize_base(Actor('fixture:qualification', 'integrator', pid), project.revision, record['sha'])
    with db.write() as s:
        p = s.get(Project, pid)
        p.workflow = {**p.workflow, 'pipeline': {'manifest': manifest.to_dict(), 'fixture_only': True}}
    if args.case == 'feature':
        document = {'title': 'Toggle a ten percent discount on the sample cart',
            'description': 'Keep the sample cart of two lattes (8.00) and the existing coffee menu. Add a button '
            'with data-testid="discount-toggle" labelled "10% off". Clicking it toggles a 10 percent discount; '
            'show just the formatted amount in a separate element data-testid="cart-total" (8.00 normally, '
            '7.20 discounted). Clicking again restores 8.00. Preserve existing unit tests; add a focused unit test if useful.',
            'uac': [{'id': 'UAC-discount', 'text': 'Click [data-testid="discount-toggle"] once: [data-testid="cart-total"] is exactly 7.20.', 'mode': 'automated'},
                    {'id': 'UAC-restore', 'text': 'Click the discount button twice: cart-total is exactly 8.00.', 'mode': 'automated'}]}
    else:
        document = {'title': 'Fix the seeded coffee price calculation bug',
            'description': 'src/cart.js has a seeded +100 cents error per item. Fix cartTotalCents to multiply the actual MENU '
            'priceCents by qty, without altering MENU or deleting/weakening existing tests. The existing main > p must '
            'read exactly "Sample cart total: 8.00" for two lattes. Keep unknown-item rejection.',
            'uac': [{'id': 'UAC-price', 'text': 'On page load, main > p reads exactly "Sample cart total: 8.00".', 'mode': 'automated'}]}
    document['dependencies'] = []
    ticket = workflow.create_ticket(user, document)
    workflow.approve_scope(user, [ApprovalItem(ticket.id, ticket.current_version, ticket.revision)])
    return execute(args, root, db, store, workflow, ticket, pid, record, supervisor_workspace)


def execute(args, root, db, store, workflow, ticket, pid, record, supervisor_workspace):
    model_path = root / 'models.json'
    model_path.write_text(json.dumps({'providers': {'openrouter': {'base_url': 'https://openrouter.ai/api/v1',
        'api_key_env': 'OPENROUTER_API_KEY'}}, 'default': {'provider': 'openrouter', 'model': args.model,
        'timeout_s': 60, 'max_output_tokens': 4096, 'temperature': 0.1}}))
    queue = JobQueue(db, lease_s=30, retry_backoff_s=1, startable=workflow.startable)
    structured, threads, notes = build_structured_runtime(db, store, workflow, queue, config_path=model_path)
    harness = DockerHarness(supervisor_workspace.sandbox)
    workspace = ProductWorkspace(db, store, workflow, root / 'workspaces', harness, structured.redactor)
    driver = HermesDriver(args.hermes_python, root / 'hermes', structured.client)
    runtime = PipelineRuntime(structured, workspace, driver)
    limits = {'model_calls': 32, 'tool_calls': 120, 'active_s': 900, 'output_tokens': 4096, 'total_tokens': 200000}
    scheduler = PipelineScheduler(db, queue, workflow, limits=limits)
    supervisor = Supervisor(db, store, {'structured': structured, 'pipeline': runtime}, queue=queue,
        limiter=ProviderLimiter(), workflow=workflow, config=WorkerConfig(heartbeat_s=0.5, worker_id='worker:dev010-qualification'))
    supervisor.maintenance.extend([threads.ensure_reply_jobs, scheduler.tick])
    deadline, last = time.monotonic() + 1000, None
    try:
        while time.monotonic() < deadline:
            supervisor.tick()
            with db.read() as s:
                current = s.get(Ticket, ticket.id)
                jobs = list(s.scalars(select(Job).where(Job.ticket_id == ticket.id).order_by(Job.created_at)))
                state = (current.phase, tuple((j.stage, j.status) for j in jobs))
                if state != last:
                    print(state, flush=True)
                    last = state
                parents = {j.parent_job_id for j in jobs if j.parent_job_id}
                fatal = any(j.id not in parents and (j.status == 'stopped' or (j.result or {}).get('needs_human')) for j in jobs)
                from app.persistence.models import Message
                waiting = any(j.status == 'waiting_input' and s.get(Message, j.waiting_request_id).recipient == 'user' for j in jobs)
                if current.phase == 'uat' or current.blocker or fatal or waiting:
                    break
            time.sleep(0.1)
        supervisor.wait_idle(10)
    finally:
        supervisor.shutdown(timeout_s=30)
    with db.read() as s:
        current = s.get(Ticket, ticket.id)
        jobs = list(s.scalars(select(Job).where(Job.ticket_id == ticket.id).order_by(Job.created_at)))
        candidates = list(s.scalars(select(Candidate).where(Candidate.ticket_id == ticket.id)))
        verifications = list(s.scalars(select(Verification).where(Verification.candidate_id.in_([c.id for c in candidates]))))
        result = {'ticket': 'DEV-010', 'case': args.case, 'model': args.model, 'provider': 'openrouter',
            'project_id': pid, 'ticket_id': ticket.id, 'phase': current.phase, 'blocker': current.blocker,
            'fixture_base_sha': record['sha'], 'usage': scope_usage(s, ticket.id, current.current_version),
            'jobs': [{'id': j.id, 'stage': j.stage, 'status': j.status, 'generation': j.lease_generation,
                'usage': j.usage, 'result': j.result} for j in jobs],
            'candidates': [{'id': c.id, 'sha': c.commit_sha, 'target_artifact_id': c.target_artifact_id,
                'target_digest': c.target_digest} for c in candidates],
            'verifications': [{'id': v.id, 'status': v.status, 'counts': v.counts,
                'coverage': v.uac_coverage, 'evidence_artifact_ids': v.evidence_artifact_ids} for v in verifications],
            'independent_review': 'NOT_REVIEWED', 'user_uat_approval': False}
    (root / 'result.json').write_text(json.dumps(structured.redactor.redact_value(result), indent=2))
    print('phase=' + current.phase + ' usage=' + json.dumps(result['usage']), flush=True)
    db.dispose()
    return 0 if current.phase == 'uat' else 2


def resume(args, root):
    saved = json.loads((root / 'result.json').read_text())
    if saved['case'] != args.case:
        raise ValueError('resume case differs from the recorded scope')
    args.model = args.model or saved['model']
    db, store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
    workflow = Workflow(db, store)
    with db.read() as s:
        ticket = s.get(Ticket, saved['ticket_id'])
        stopped = list(s.scalars(select(Job).where(Job.ticket_id == ticket.id, Job.status == 'stopped').order_by(Job.created_at)))
    if args.extend_model_calls:
        if not 1 <= args.extend_model_calls <= 16 or not stopped:
            raise ValueError('extension must be 1..16 calls for a budget-stopped fixture job')
        queue = JobQueue(db, startable=workflow.startable)
        queue.extend_budget(stopped[-1].id, user='user:qualification',
            additions={'model_calls': args.extend_model_calls, 'total_tokens': args.extend_model_calls * 12000},
            authorization_id='qualification-budget-' + uuid.uuid4().hex)
    workspace = WorkspaceSupervisor(root / 'workspaces')
    if workspace.broker(saved['project_id']).accepted_sha() != saved['fixture_base_sha']:
        raise ValueError('resume base differs; no automatic reset is allowed')
    return execute(args, root, db, store, workflow, ticket, saved['project_id'],
                   {'sha': saved['fixture_base_sha']}, workspace)


if __name__ == '__main__':
    raise SystemExit(main())
