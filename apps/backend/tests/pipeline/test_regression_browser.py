"""Actual isolated browser on synthetic accepted journeys; no provider or product QA approval."""
import hashlib

import pytest

from app.pipeline.contracts import QaPlan
from app.pipeline.harness import DockerHarness, RUNNER_IMAGE
from app.pipeline.regression import regression_suite
from app.workspace import WorkspaceSupervisor
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401


def test_runner_executes_and_detects_failure_beyond_sixty_journeys(agent_env, tmp_path):
    supervisor = WorkspaceSupervisor(tmp_path / 'workspaces')
    if not supervisor.sandbox.available():
        pytest.skip('actual Docker engine required')
    node_image = supervisor.sandbox.image_id('node:22.20.0-alpine')
    supervisor.sandbox.image_id(RUNNER_IMAGE)
    agent_env.queue.lease_s = 150
    ctx = agent_env.ctx(agent_env.job('technical-lead', 'technical_plan',
                                    ticket=agent_env.approved_ticket(), stage='plan'))
    harness = DockerHarness(supervisor.sandbox)
    site = tmp_path / 'site'
    site.mkdir()
    html = '<button data-testid="borrow">Pinjam</button>'
    (site / 'index.html').write_text(html)
    entries = []
    for number, count in enumerate((24, 24, 13)):
        plan = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Accepted browser fixture', 'tests': [
            {'id': f'journey-{i}', 'uac': ['UAC-1'], 'steps': [
                {'action': 'assert_visible', 'selector': 'testid=borrow' if number < 2 or i < 12 else 'testid=missing'}
            ]} for i in range(count)]})
        entries.append({'ticket_id': f'ticket-{number}', 'number': number, 'legacy': False, 'tests': plan.tests})
    suite, legacy, owners = regression_suite(entries)
    try:
        proof = harness.run(ctx, site, hashlib.sha256(html.encode()).hexdigest(), suite, node_image,
                            expected_runner=harness.identity(), diagnostics=False, legacy_test_ids=legacy)
        assert proof['status'] == 'failed' and not proof['infrastructure_failure'], proof
        assert proof['counts'] == {'discovered': 61, 'executed': 61, 'passed': 60, 'failed': 1, 'skipped': 0}
        failed = [test['id'] for test in proof['report']['tests'] if test['status'] == 'failed']
        assert failed == ['reg-t2-journey-12'] and owners[failed[0]]['ticket_id'] == 'ticket-2'
    finally:
        ctx.stop_resources()
        agent_env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
