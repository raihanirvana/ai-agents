"""Actual builds and product DB, FAKE model; no QA/UAT authority."""
from sqlalchemy import select

from app.persistence.models import Artifact, Candidate
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.pipeline.test_product_loop import setup, FILES, plan
from tests.pipeline.test_product_regressions import start_stage


def test_missing_testid_is_repaired_before_publication_in_the_same_job(agent_env, tmp_path):
    env = agent_env
    class Driver:
        def run(self, ctx, identity, snapshot, tools, parameters):
            if identity['role'] == 'qa':
                tools['propose_tests']({'plan': {'kind': 'qa_plan', 'summary': 'Original expected count',
                    'tests': [{'id': 'total', 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': [
                        {'action': 'click', 'selector': 'testid=add'},
                        {'action': 'assert_text', 'selector': 'testid=total', 'value': '4'}]}]}})
                return {}
            html = FILES['index.html'].replace('id="add"', 'id="add" data-testid="add"')
            for name, data in {**FILES, 'index.html': html}.items():
                tools['patch_file']({'path': name, 'content': data})
            rejected = tools['submit_candidate']({'message': 'Incomplete instrumentation'})
            assert rejected['submitted'] is False
            assert rejected['ui_check']['missing_testids'] == ['total']
            with env.db.read() as s:
                assert s.scalar(select(Candidate)) is None
            assert env.get(ctx.lease.job_id).status == 'running'
            tools['patch_file']({'path': 'index.html', 'content': html.replace(
                'id="total"', 'id="total" data-testid="total"')})
            published = tools['submit_candidate']({'message': 'Complete instrumentation'})
            assert published['submitted'] is True
            return {}
    runtime, _, scheduler = setup(env, tmp_path, Driver())
    technical = plan()
    technical['ui_contract'] = {'controls': [
        {'testid': 'add', 'role': 'button', 'name': 'Add', 'purpose': 'Add record'},
        {'testid': 'total', 'role': 'status', 'purpose': 'Result'}]}
    env.script(technical)
    ticket = env.approved_ticket()
    for stage in ('technical_plan', 'qa_plan', 'development'):
        ctx = start_stage(env, runtime, scheduler, ticket, stage)
        try:
            outcome = runtime.run(ctx)
            assert outcome.status == 'succeeded', outcome.error
            if env.get(ctx.lease.job_id).status == 'running':
                env.queue.complete(ctx.lease, outcome.result)
        finally:
            ctx.stop_resources()
            env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    with env.db.read() as s:
        candidates = list(s.scalars(select(Candidate)))
        reports = [a for a in s.scalars(select(Artifact))
                   if a.meta.get('producer') == 'ui-contract-check']
        assert len(candidates) == 1 and len(reports) == 2
        assert all(a.meta['fake'] is True for a in reports)
    assert env.world.ticket(ticket.id).phase == 'technical_review'
    assert env.world.ticket(ticket.id).workflow['repair_cycles'] == 0
