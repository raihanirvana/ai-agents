"""Selector correction preserves assertions; fixture bugs must still fail in the real browser."""
import pytest

from app.pipeline.contracts import QaPlan, QaSelectorRepair
from app.pipeline.qa_repair import fill_selector_candidates, repair_fill_selectors


SOURCE = {'src/app.js': 'input.className = "borrower-input";'}


def suite():
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Loan fixture', 'tests': [{
        'id': 'loan', 'purpose': 'feature', 'uac': ['UAC-1'], 'steps': [
            {'action': 'fill', 'selector': '#guessed-borrower', 'value': 'Budi'},
            {'action': 'click', 'selector': '#borrow'},
            {'action': 'assert_text', 'selector': '#result', 'value': 'Budi'}]}]})


def proof():
    return {'status': 'failed', 'infrastructure_failure': False, 'report': {'tests': [{
        'id': 'loan', 'status': 'failed', 'failed_step': 0, 'selector_diagnosis': {
            'contract': 'missing_fill_selector', 'selector': '#guessed-borrower', 'matched_count': 0,
            'candidates': [{'selector': '.borrower-input', 'tag': 'input', 'type': 'text',
                            'matched_count': 1, 'visible': True, 'enabled': True, 'editable': True}]}}]}}


def proposal(index=0):
    return QaSelectorRepair(kind='qa_selector_repair', summary='Reuse the borrower control',
        bindings=[{'test_id': 'loan', 'candidate_index': index, 'reason': 'Borrower input in source and DOM'}])


def test_only_failed_fill_selector_changes():
    original = suite()
    fixed = repair_fill_selectors(original, proof(), SOURCE, proposal())
    wanted = original.model_dump()
    wanted['tests'][0]['steps'][0]['selector'] = '.borrower-input'
    assert fixed.model_dump() == wanted
    assert original.tests[0].steps[0].selector == '#guessed-borrower'
    assert original.digest != fixed.digest


@pytest.mark.parametrize('fault', ['infrastructure', 'missing_source', 'hidden', 'disabled', 'readonly',
                                  'ambiguous', 'password', 'unobserved', 'wrong_original', 'other_failure',
                                  'assertion', 'missing_report', 'out_of_range'])
def test_insufficient_evidence_cannot_change_suite(fault):
    current, report, source, selected = suite(), proof(), SOURCE, proposal()
    result = report['report']['tests'][0]
    facts = result['selector_diagnosis']
    row = facts['candidates'][0]
    if fault == 'infrastructure': report['infrastructure_failure'] = True
    elif fault == 'missing_source': source = {}
    elif fault == 'hidden': row['visible'] = False
    elif fault == 'disabled': row['enabled'] = False
    elif fault == 'readonly': row['editable'] = False
    elif fault == 'ambiguous': row['matched_count'] = 2
    elif fault == 'password': row['type'] = 'password'
    elif fault == 'unobserved': selected = proposal(1)
    elif fault == 'wrong_original': facts['selector'] = '#different'
    elif fault == 'other_failure': report['report']['tests'].append({'id': 'other', 'status': 'failed'})
    elif fault == 'assertion': result['failed_step'] = 2
    elif fault == 'missing_report': report['report'] = {}
    elif fault == 'out_of_range': result['failed_step'] = 99
    assert repair_fill_selectors(current, report, source, selected) is None


def test_model_can_abstain_and_cannot_supply_a_replacement_assertion():
    empty = QaSelectorRepair(kind='qa_selector_repair', summary='Uncertain', bindings=[])
    assert repair_fill_selectors(suite(), proof(), SOURCE, empty) is None
    data = proposal().model_dump()
    data['bindings'][0]['selector'] = '#anything'
    with pytest.raises(ValueError):
        QaSelectorRepair.model_validate(data)


from app.pipeline.harness import DockerHarness, RUNNER_IMAGE
from app.workspace import WorkspaceSupervisor
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401,E402


def test_real_browser_repair_does_not_hide_a_seeded_application_bug(agent_env, tmp_path):
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
    html = '''<input class="borrower-input" aria-label="Borrower">
        <input class="hidden-input" hidden><input class="disabled-input" disabled>
        <input class="ambiguous"><input class="ambiguous">
        <button id="borrow">Borrow</button><p id="result"></p><script>
        document.querySelector('#borrow').onclick = () => {
          document.querySelector('#result').textContent = document.querySelector('.borrower-input').value;
        };</script>'''
    (site / 'index.html').write_text(html)
    try:
        original = suite()
        failed = harness.run(ctx, site, 'a' * 64, original, node_image, expected_runner=harness.identity())
        assert failed['status'] == 'failed', failed
        facts = failed['report']['tests'][0]['selector_diagnosis']
        observed = [row['selector'] for row in facts['candidates']]
        assert '.borrower-input' in observed
        assert not set(observed).intersection(('.hidden-input', '.disabled-input', '.ambiguous'))
        candidates = fill_selector_candidates(original, failed, SOURCE)['loan']['candidates']
        fixed = repair_fill_selectors(original, failed, SOURCE, proposal(candidates[0]['candidate_index']))
        assert fixed is not None
        passed = harness.run(ctx, site, 'b' * 64, fixed, node_image, expected_runner=harness.identity())
        assert passed['status'] == 'passed', passed
        (site / 'index.html').write_text(html.replace("document.querySelector('.borrower-input').value", "'Wrong borrower'"))
        broken = harness.run(ctx, site, 'c' * 64, fixed, node_image, expected_runner=harness.identity())
        assert broken['status'] == 'failed', broken
        assert broken['report']['tests'][0]['failed_step'] == 2
        assert repair_fill_selectors(fixed, broken, SOURCE, proposal()) is None
    finally:
        ctx.stop_resources()
        agent_env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
