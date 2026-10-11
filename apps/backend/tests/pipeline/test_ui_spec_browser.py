"""Actual isolated browser on fixtures; no real model or product QA approval."""
import hashlib

import pytest

from app.agents.ui_spec import UiSpec, check_expected_values
from app.pipeline.contracts import QaPlan
from app.pipeline.harness import DockerHarness, RUNNER_IMAGE
from app.workspace import WorkspaceSupervisor
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.agents.test_ui_spec import SPEC


def test_display_text_folds_case_but_values_and_dialog_types_remain_exact(agent_env, tmp_path):
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
    plan = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Spec browser fixture', 'tests': [{
        'id': 'copy', 'uac': ['UAC-1'], 'steps': [
            {'action': 'fill', 'selector': 'testid=title-input', 'value': 'Laskar Pelangi'},
            {'action': 'assert_text', 'selector': 'testid=catalog-title', 'value': 'Katalog Buku'},
            {'action': 'assert_contains_text', 'selector': 'testid=catalog-title', 'value': 'Katalog Buku'},
            {'action': 'assert_value', 'selector': 'testid=title-input', 'value': 'Laskar Pelangi'},
            {'action': 'click_dialog', 'selector': 'testid=delete-book',
             'dialog': {'type': 'confirm', 'message': 'Hapus buku ini dari katalog?', 'accept': True}}]}]})
    check_expected_values(plan, UiSpec.model_validate(SPEC))
    html = '''<h1 data-testid="catalog-title">KATALOG   BUKU</h1>
<input data-testid="title-input"><button data-testid="delete-book">Hapus</button>
<script>document.querySelector('button').onclick = () => confirm('HAPUS BUKU INI DARI KATALOG?');</script>'''
    cases = [
        (html, None),
        (html.replace('KATALOG   BUKU', 'KATALOG PINJAMAN'), 1),
        (html.replace('<input data-testid="title-input">',
                      '<input data-testid="title-input" oninput="this.value=this.value.toLowerCase()">'), 3),
        (html.replace('=> confirm(', '=> alert('), 4),
    ]
    try:
        for document, failed_step in cases:
            (site / 'index.html').write_text(document)
            result = harness.run(ctx, site, hashlib.sha256(document.encode()).hexdigest(), plan, node_image,
                                 expected_runner=harness.identity())
            assert result['status'] == ('passed' if failed_step is None else 'failed'), result
            assert result['counts']['executed'] == 1 and result['infrastructure_failure'] is False
            if failed_step is not None:
                assert result['report']['tests'][0]['failed_step'] == failed_step
    finally:
        ctx.stop_resources()
        agent_env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
