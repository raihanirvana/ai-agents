"""Reproducible isolated cold/warm infrastructure benchmark, no model/provider.

All DB, Git, artifacts, cache and containers belong to a temporary directory.
Synthetic transcript replay reports estimates, never billable token usage.
"""
import argparse
import copy
import hashlib
import json
import platform
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import select
from app.agents import Redactor
from app.agents.context import estimate_tokens
from app.domain import Actor, Workflow, ApprovalItem
from app.persistence import Database, ArtifactStore, migrate
from app.persistence.models import Project, Job
from app.workers import JobQueue, ProviderLimiter
from app.workers.runtime import RunContext
from app.workspace import WorkspaceSupervisor, reference_manifest_dict, parse_manifest
from .contracts import QaPlan, digest_of
from .harness import DockerHarness
from .workspace import ProductWorkspace
from .checks import DeveloperChecks
from .transcript import TranscriptProjection


def replay(calls=80):
    """Fixed source/check workload, identical inputs for each projection strategy."""
    initial = [{'role': 'system', 'content': 'Implement the approved scope; preserve decisions.'},
               {'role': 'user', 'content': 'Add a coffee menu with storage and real tests.'}]
    strategies = {'unprojected': 0, 'stable_projection': 0, 'projection_rollover_10_calls': 0}
    transcripts = {key: copy.deepcopy(initial) for key in strategies}
    segments = []
    project = TranscriptProjection(lambda _: None)
    for index in range(calls):
        identifier = 'read-' + str(index)
        content = 'export function menu() { return "Coffee"; }\n' * 360
        exchange = [
            {'role': 'assistant', 'content': 'Read before editing.', 'tool_calls': [
                {'id': identifier, 'type': 'function', 'function': {'name': 'pipeline_read_file',
                  'arguments': json.dumps({'path': 'src/menu.js'})}}]},
            {'role': 'tool', 'tool_call_id': identifier, 'content': json.dumps({
                'path': 'src/menu.js', 'content': content, 'digest': hashlib.sha256(content.encode()).hexdigest()})}]
        for name, messages in transcripts.items():
            messages.extend(copy.deepcopy(exchange))
            body = {'messages': messages}
            if name != 'unprojected':
                body = project(body)
            strategies[name] += estimate_tokens(json.dumps(body['messages'], ensure_ascii=False))
            if name == 'projection_rollover_10_calls' and (index + 1) % 10 == 0:
                transcripts[name] = project({'messages': messages}, rollover=True)['messages']
                segments.append(len(transcripts[name]))
    return {'label': 'synthetic replay; estimates, not provider usage or prompt-cache hits',
            'calls': calls, 'cumulative_estimated_input_tokens': strategies,
            'rollover_message_counts': segments, 'actual_provider_calls': 0, 'actual_cost_usd': 0}


def run(fixture, *, rounds=2):
    fixture = Path(fixture).resolve()
    if not (fixture / 'package-lock.json').is_file():
        raise ValueError('Benchmark requires the committed reference fixture with generated lockfile')
    with tempfile.TemporaryDirectory(prefix='aiagent-benchmark-') as temporary:
        root = Path(temporary)
        migrate.upgrade(root / 'control.sqlite3')
        db, store = Database(root / 'control.sqlite3'), ArtifactStore(root / 'artifacts')
        workflow = Workflow(db, store)
        sup = WorkspaceSupervisor(root / 'workspaces')
        pid = 'isolated-benchmark'
        manifest = parse_manifest(reference_manifest_dict(image='node:22.20.0-alpine'))
        try:
            if not sup.sandbox.available():
                raise RuntimeError('Docker daemon unavailable')
            empty = sup.create_project(pid)
            seeded = sup.start_attempt(pid, ticket_id='benchmark-seed', scope_version=1, role='developer',
                attempt=1, generation=1, lease_id='isolated-seed', manifest=manifest)
            for path in sorted(fixture.rglob('*')):
                if path.is_file():
                    sup.write_file(seeded.ref, seeded.credential, path.relative_to(fixture).as_posix(), path.read_bytes())
            sup.write_file(seeded.ref, seeded.credential, 'test.cjs',
                b"const {test}=require('node:test');test('seed test remains present',()=>{});\n")
            commit = sup.submit_candidate(seeded.ref, seeded.credential, 'Isolated reference benchmark fixture')
            # Trusted initialization of this temporary benchmark ONLY. No user
            # application, candidate approval, or release exists in this DB.
            sup.broker(pid)._bare('update-ref', 'refs/heads/accepted', commit['sha'], empty)
            sup.stop_run(seeded.ref, 'benchmark_seed_done')
            user = Actor('user:benchmark', 'user', pid)
            p = workflow.create_project(user, name='Isolated benchmark', mode='new')
            workflow.initialize_base(Actor('benchmark:init', 'integrator', pid), p.revision, commit['sha'])
            with db.write() as s:
                p = s.get(Project, pid)
                p.workflow = {**p.workflow, 'pipeline': {'manifest': manifest.to_dict()}}
            ticket = workflow.create_ticket(user, {'title': 'Reference smoke', 'uac': [
                {'id': 'UAC-1', 'text': 'The app opens.', 'mode': 'automated'}]})
            workflow.approve_scope(user, [ApprovalItem(ticket.id, 1, ticket.revision)])
            queue = JobQueue(db, lease_s=3600)
            harness = DockerHarness(sup.sandbox)
            workspace = ProductWorkspace(db, store, workflow, sup.root, harness, Redactor([]))
            suite = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Reference browser workload', 'tests': [
                {'id': 'open-app', 'purpose': 'regression', 'uac': ['UAC-1'],
                 'steps': [{'action': 'assert_visible', 'selector': 'body'}]}]})
            samples = []
            for iteration in range(rounds):
                started = time.monotonic()
                job = queue.enqueue(project_id=pid, ticket_id=ticket.id, expected_scope=1, lane='interactive',
                    role='qa', stage='qa_plan', runtime='isolated-benchmark', idempotency_key='round-' + str(iteration),
                    limits={'model_calls': 1, 'tool_calls': 100, 'active_s': 3600}, payload={})
                lease = queue.claim('benchmark:worker', 'interactive', capacity=2, runtimes=('isolated-benchmark',))
                with db.read() as s:
                    j = s.get(Job, job.id)
                    ctx = RunContext(queue=queue, limiter=ProviderLimiter(), lease=lease,
                        job={key: getattr(j, key) for key in ('id', 'project_id', 'ticket_id', 'lane', 'runtime_ref')})
                try:
                    before = set(sup.root.rglob('cmd-*.json'))
                    phase = time.monotonic(); baseline = workspace.base_build(ctx)
                    build_s = time.monotonic() - phase
                    if baseline['status'] != 'built' or baseline['gate']['status'] != 'passed':
                        raise RuntimeError('Reference baseline checks failed')
                    target = digest_of({'base': baseline['base_sha'], 'fixture': 'reference-smoke'})
                    phase = time.monotonic()
                    browser = workspace.execution_cache.baseline_browser(ctx, baseline['site'], target, suite,
                        sup.sandbox.image_id(manifest.image), harness.identity(), fake=False)
                    browser_s = time.monotonic() - phase
                    if browser['status'] != 'passed':
                        raise RuntimeError('Reference browser check failed')
                    candidate_sup, candidate, _ = workspace.start(ctx)
                    checks_workspace = SimpleNamespace(ui_contract=lambda _: None, db=db, store=store,
                                                       configuration=workspace.configuration, redactor=workspace.redactor)
                    phase = time.monotonic()
                    checks = DeveloperChecks(ctx, candidate_sup, candidate, checks_workspace).run()
                    checks_s = time.monotonic() - phase
                    if checks['status'] != 'passed':
                        raise RuntimeError('Reference developer checks failed: ' + json.dumps(checks))
                    records = [json.loads(path.read_text()) for path in set(sup.root.rglob('cmd-*.json')) - before]
                    peak = [r['peak_memory_bytes'] for r in records if r.get('peak_memory_bytes') is not None]
                    ctx.flush_metrics()
                    samples.append({'round': iteration, 'cache': 'cold' if iteration == 0 else 'warm',
                        'duration_s': round(time.monotonic() - started, 3),
                        'baseline_build_s': round(build_s, 3), 'baseline_browser_s': round(browser_s, 3),
                        'developer_checks_s': round(checks_s, 3), 'baseline_build_cache_hit': baseline.get('cache_hit', False),
                        'baseline_browser_cache_hit': browser.get('cache_hit', False),
                        'developer_phases': checks['phases'], 'commands': [{k: r.get(k) for k in
                            ('label', 'duration_s', 'peak_memory_bytes', 'memory_observation', 'execution_kind', 'exit_code', 'oom_killed')} for r in records],
                        'peak_memory_bytes': max(peak) if peak else None})
                    queue.complete(lease, {'benchmark_only': True})
                finally:
                    ctx.stop_resources()
                    queue.finish_cleanup(lease.job_id, lease.generation)
            return {'schema': 1, 'label': 'isolated real Docker/Git/browser; no provider, no product approvals',
                'host': platform.platform(), 'base_sha': commit['sha'], 'manifest_digest': manifest.digest,
                'lock_digest': hashlib.sha256((fixture / 'package-lock.json').read_bytes()).hexdigest(),
                'node_image_id': sup.sandbox.image_id(manifest.image), 'runner': harness.identity(),
                'workload': 'base install/build/repo test + browser + independent developer run_checks',
                'samples': samples, 'transcript_replay': replay(), 'billable_token_usage': None}
        finally:
            for container in sup.sandbox.owned():
                sup.sandbox.kill_and_remove(container['name'])
            db.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parents[2] /
        'tests/workspace/fixtures/reference-react-vite')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.rounds <= 10:
        parser.error('--rounds must be 2..10')
    previous = os.environ.get('SANDBOX_MEASURE_MEMORY')
    os.environ['SANDBOX_MEASURE_MEMORY'] = '1'
    try:
        report = run(args.fixture, rounds=args.rounds)
    finally:
        if previous is None:
            os.environ.pop('SANDBOX_MEASURE_MEMORY', None)
        else:
            os.environ['SANDBOX_MEASURE_MEMORY'] = previous
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    for sample in report['samples']:
        print(sample['cache'], sample['duration_s'], 'seconds; peak bytes', sample['peak_memory_bytes'])
    print('Provider calls: 0. Transcript estimates are labeled separately.')


if __name__ == '__main__':
    main()
