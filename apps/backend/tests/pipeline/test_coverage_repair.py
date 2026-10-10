"""Coverage witnesses are proposals; actual separate harness execution remains authoritative."""
from copy import deepcopy
import json
import pytest
from app.pipeline.contracts import QaCoverageRepair, QaPlan
from app.pipeline.qa_repair import baseline_coverage_gaps, classify_failure, repair_coverage

SOURCE = {'index.html': '<button id="pay">Pay</button><div id="paid-total">0</div>'}
CRITERIA = [{'id': 'UAC-1', 'text': 'Pay the cart and display the paid total'}]


def original():
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Surrogate', 'tests': [
        {'id': 'payment', 'purpose': 'feature', 'uac': ['UAC-1'], 'steps': [
            {'action': 'click', 'selector': '#add'},
            {'action': 'assert_visible', 'selector': '#product'}]},
        {'id': 'catalog', 'purpose': 'regression', 'uac': [], 'steps': [
            {'action': 'assert_visible', 'selector': '#product'}]}]})


def evidence():
    return {'status': 'incomplete', 'infrastructure_failure': False,
        'coverage_gap_test_ids': ['payment'], 'baseline': {'execution': {
            'status': 'passed', 'infrastructure_failure': False, 'report': {
                'discovered': 2, 'executed': 2, 'skipped': 0, 'tests': [
                    {'id': 'payment', 'status': 'passed'}, {'id': 'catalog', 'status': 'passed'}]}}}}


def proposal():
    suite = original().model_dump()
    suite['tests'][0]['steps'] = [{'action': 'click', 'selector': '#pay'},
        {'action': 'assert_text', 'selector': '#paid-total', 'value': '2000'}]
    return {'kind': 'qa_coverage_repair', 'summary': 'Exercise payment', 'suite': suite,
        'witnesses': [{'test_id': 'payment', 'criterion_id': 'UAC-1', 'action_step': 0,
            'assertion_step': 1, 'source_path': 'index.html', 'source_excerpt': SOURCE['index.html'],
            'reason': 'Use the payment control and assert fixture-computed total.'}]}


def test_complete_baseline_gap_is_test_contract_not_infrastructure():
    proof = evidence()
    assert baseline_coverage_gaps(original(), proof) == ['payment']
    assert classify_failure(proof) == 'test_contract'
    proof['infrastructure_failure'] = True
    assert not baseline_coverage_gaps(original(), proof)
    assert classify_failure(proof) == 'infrastructure'


@pytest.mark.parametrize('mutation', ['incomplete', 'missing', 'skipped', 'counts', 'duplicate', 'all_failed'])
def test_incomplete_or_non_gap_baseline_cannot_authorize_coverage_rewrite(mutation):
    proof = evidence()
    base = proof['baseline']['execution']
    if mutation == 'incomplete':
        base['status'] = 'incomplete'
    elif mutation == 'missing':
        base['report']['tests'].pop()
    elif mutation == 'skipped':
        base['report']['tests'][0]['status'] = 'skipped'
    elif mutation == 'counts':
        base['report']['executed'] = 0
    elif mutation == 'duplicate':
        base['report']['tests'][1]['id'] = 'payment'
    else:
        base['report']['tests'][0]['status'] = 'failed'
        base['status'] = 'failed'
    assert not baseline_coverage_gaps(original(), proof)
    assert classify_failure(proof) == 'infrastructure'
    with pytest.raises(ValueError, match='completed baseline evidence'):
        repair_coverage(original(), proof, QaCoverageRepair.model_validate(proposal()), CRITERIA, SOURCE)


def test_repaired_coverage_keeps_unaffected_case_and_requires_shipped_source_witness():
    before = original()
    repaired = repair_coverage(before, evidence(), QaCoverageRepair.model_validate(proposal()), CRITERIA, SOURCE)
    assert repaired.digest != before.digest
    assert repaired.tests[1] == before.tests[1]
    assert before.tests[0].steps[0].selector == '#add'


@pytest.mark.parametrize('mutation', ['drop_test', 'purpose', 'mapping', 'unaffected', 'source',
                                    'criterion', 'action', 'assertion', 'unchanged', 'duplicate_witness'])
def test_coverage_repair_cannot_drop_weaken_scope_or_change_other_cases(mutation):
    data = proposal()
    if mutation == 'drop_test':
        data['suite']['tests'].pop()
    elif mutation == 'purpose':
        data['suite']['tests'][0]['purpose'] = 'regression'
    elif mutation == 'mapping':
        data['suite']['tests'][0]['uac'] = []
    elif mutation == 'unaffected':
        data['suite']['tests'][1]['steps'][0]['selector'] = '#different'
    elif mutation == 'source':
        data['witnesses'][0]['source_excerpt'] = 'fabricated shipped source'
    elif mutation == 'criterion':
        data['witnesses'][0]['criterion_id'] = 'UAC-2'
    elif mutation == 'action':
        data['suite']['tests'][0]['steps'][0] = {'action': 'assert_visible', 'selector': '#pay'}
    elif mutation == 'assertion':
        data['witnesses'][0]['assertion_step'] = 0
    elif mutation == 'unchanged':
        data['suite'] = original().model_dump()
    else:
        data['witnesses'].append(deepcopy(data['witnesses'][0]))
    with pytest.raises(ValueError):
        repair_coverage(original(), evidence(), QaCoverageRepair.model_validate(data), CRITERIA, SOURCE)


from sqlalchemy import select
from app.persistence.models import Candidate, Artifact, Verification
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401,E402
from tests.pipeline.test_product_loop import setup, plan, ScriptedDriver, FILES
from tests.pipeline.test_product_regressions import seed_baseline, prepare, start_stage


@pytest.mark.parametrize('invalid_first', [False, True])
def test_surrogate_suite_schedules_qa_repair_and_fresh_execution_without_developer(agent_env, tmp_path, invalid_first):
    env = agent_env
    html = FILES['index.html'] + '<button id="pay" onclick="document.querySelector(\'#total\').textContent=\'8\'">Pay</button>'
    class Driver(ScriptedDriver):
        def run(self, ctx, identity, snapshot, tools, parameters):
            if identity['role'] == 'developer':
                for name, content in {**FILES, 'index.html': html}.items():
                    ctx.tool_call('patch_file', lambda n=name, c=content: tools['patch_file']({'path': n, 'content': c}))
                ctx.tool_call('submit_candidate', lambda: tools['submit_candidate']({'message': 'Payment fixture'}))
                return {}
            return super().run(ctx, identity, snapshot, tools, parameters)
    runtime, _, scheduler = setup(env, tmp_path, Driver())
    seed_baseline(env, runtime, FILES)
    from tests.pipeline.test_contracts import PLAN
    fixed = deepcopy(PLAN)
    fixed['tests'][0]['steps'] = [{'action': 'click', 'selector': '#pay'},
                                {'action': 'assert_text', 'selector': '#total', 'value': '8'}]
    reply = {'kind': 'qa_coverage_repair', 'summary': 'Real payment journey', 'suite': fixed,
        'witnesses': [{'test_id': 'total', 'criterion_id': 'UAC-1', 'action_step': 0,
                      'assertion_step': 1, 'source_path': 'index.html', 'source_excerpt': '<button id="pay" onclick=',
                      'reason': 'New payment action exercises the approved feature.'}]}
    invalid = deepcopy(reply)
    invalid['witnesses'][0]['assertion_step'] = 0
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'Correct'},
               *([invalid] if invalid_first else []), reply)
    t = env.approved_ticket()
    prepare(env, runtime, scheduler, t)
    with env.db.read() as s:
        candidate = s.scalar(select(Candidate))
        old_target, commit, build = candidate.target_digest, candidate.commit_sha, candidate.build_artifact_id
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    try:
        result = runtime.run(ctx)
        assert result.status == 'succeeded' and result.result['diagnosis_required']
        assert result.result['failure_kind'] == 'test_contract'
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    assert ctx.job['runtime_ref']['payload']['task'] == 'diagnose'
    original_repair = runtime._repair_qa_target
    def interrupted_repin(*args, **kwargs):
        raise RuntimeError('injected interruption before repin')
    runtime._repair_qa_target = interrupted_repin
    try:
        with pytest.raises(RuntimeError, match='interruption before repin'):
            runtime.run(ctx)
        runtime._repair_qa_target = original_repair
        def no_new_model(*args, **kwargs):
            raise AssertionError('persisted proposal must be reused without a model call')
        runtime.structured._ask = no_new_model
        result = runtime.run(ctx)
        assert result.result['repair_kind'] == 'feature_coverage'
        with env.db.read() as s:
            current = s.get(Candidate, candidate.id)
            assert current.target_digest != old_target
            assert (current.commit_sha, current.build_artifact_id) == (commit, build)
            old = s.scalar(select(Verification))
            assert old.status == 'incomplete'
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    ctx = start_stage(env, runtime, scheduler, t, 'qa')
    assert ctx.job['runtime_ref']['payload']['task'] == 'verify'
    try:
        result = runtime.run(ctx)
        assert result.status == 'failed'  # FAKE provider still cannot authorize UAT
        with env.db.read() as s:
            rows = list(s.scalars(select(Verification).order_by(Verification.created_at)))
            assert len(rows) == 2 and rows[-1].counts['passed'] == 1
            proof = json.loads(env.store.read_bytes(s, rows[-1].evidence_artifact_ids[-1]))
            assert proof['baseline']['execution']['counts']['failed'] == 1
        assert env.world.ticket(t.id).phase == 'qa'
        assert env.world.ticket(t.id).workflow['repair_cycles'] == 0
    finally:
        ctx.stop_resources()
