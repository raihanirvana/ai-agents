"""Actual isolated browser against a fixture; no model or product QA approval."""
import pytest

from app.pipeline.contracts import QaPlan
from app.pipeline.harness import DockerHarness, RUNNER_IMAGE
from app.workspace import WorkspaceSupervisor
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401


def test_enter_press_dispatches_key_and_fill_does_not(agent_env, tmp_path):
    env = agent_env
    supervisor = WorkspaceSupervisor(tmp_path / 'workspaces')
    if not supervisor.sandbox.available():
        pytest.skip('actual Docker engine required')
    try:
        node_image = supervisor.sandbox.image_id('node:22.20.0-alpine')
        supervisor.sandbox.image_id(RUNNER_IMAGE)
    except Exception:
        pytest.skip('pinned Node and verification images required')
    env.queue.lease_s = 90  # bounded fixture lease for three actual container invocations
    ctx = env.ctx(env.job('technical-lead', 'technical_plan', ticket=env.approved_ticket(), stage='plan'))
    harness = DockerHarness(supervisor.sandbox)
    site = tmp_path / 'site'
    site.mkdir()
    html = '''<input id="item"><ul id="list"></ul><script>
const saved=JSON.parse(localStorage.getItem('items') || '[]');
for (const name of saved) {
  const item=document.createElement('li'); item.textContent=name;
  document.querySelector('#list').appendChild(item);
}
document.querySelector('#item').addEventListener('keydown', event => {
  if(event.key === 'Enter') {
    const item=document.createElement('li'); item.textContent=event.target.value;
    document.querySelector('#list').appendChild(item); event.target.value='';
    localStorage.setItem('items', JSON.stringify([item.textContent]));
  }
});</script>'''
    (site / 'index.html').write_text(html)
    def suite(action, value, *, reload=False):
        steps = [
            {'action': 'fill', 'selector': '#item', 'value': 'Milk'},
            {'action': action, 'selector': '#item', 'value': value},
            {'action': 'assert_count', 'selector': '#list li', 'value': 1},
            {'action': 'assert_text', 'selector': '#list li', 'value': 'Milk'},
            {'action': 'assert_value', 'selector': '#item', 'value': ''}]
        if reload:
            steps += [{'action': 'reload'},
                {'action': 'assert_count', 'selector': '#list li', 'value': 1},
                {'action': 'assert_text', 'selector': '#list li', 'value': 'Milk'}]
        return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Keyboard fixture', 'tests': [{
            'id': 'enter-add', 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': steps}]})
    try:
        # The exact erroneous plan from the demo never submits an item.
        invalid = harness.run(ctx, site, 'a' * 64, suite('fill', 'Milk\\n'), node_image,
                              expected_runner=harness.identity())
        assert invalid['status'] == 'failed', invalid
        assert invalid['counts']['executed'] == 1 and invalid['infrastructure_failure'] is False
        valid = harness.run(ctx, site, 'b' * 64, suite('press', 'Enter', reload=True), node_image,
                            expected_runner=harness.identity())
        assert valid['status'] == 'passed', valid
        assert valid['counts']['executed'] == 1 and valid['counts']['passed'] == 1
        assert valid['suite_digest'] != invalid['suite_digest']
        assert valid['invocation_id'] != invalid['invocation_id']
        (site / 'index.html').write_text(html.replace(
            "localStorage.setItem('items', JSON.stringify([item.textContent]));", ''))
        broken = harness.run(ctx, site, 'd' * 64, suite('press', 'Enter', reload=True), node_image,
                             expected_runner=harness.identity())
        assert broken['status'] == 'failed', broken
        assert broken['counts']['executed'] == 1 and broken['infrastructure_failure'] is False
        with pytest.raises(ValueError, match='new target'):
            harness.run(ctx, site, 'a' * 64, suite('press', 'Enter'), node_image,
                        expected_runner={**harness.identity(), 'runner_code_digest': 'c' * 64})
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
