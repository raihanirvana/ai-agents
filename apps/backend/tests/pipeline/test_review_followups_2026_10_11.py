"""Follow-ups from the 11 Oct review: SQL-filtered intent lookups, deterministic baseline
cache admission, early UI inventory in run_checks and legacy selector semantics.

Real SQLite/files; no provider, Docker or browser. Nothing here is QA/UAT evidence.
"""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.persistence import append_message
from app.pipeline.workspace import ProductWorkspace
from tests.persistence import factories as f
from tests.persistence.conftest import db, db_path, store  # noqa: F401


def post(s, p, t, key, **meta):
    return append_message(s, project_id=p.id, ticket_id=t.id, thread_id='ticket:' + t.id, sender='agent:qa',
                          body=key, idempotency_key=key, meta=meta)[0]


def test_latest_intent_filters_in_sql_and_ignores_runtime_logs(db):
    with db.write() as s:
        p = f.project(s)
        t = f.ticket(s, p)
        other = f.ticket(s, p, number=2)
        post(s, p, t, 'old', intent='qa_plan', scope_version=1)
        wanted = post(s, p, t, 'wanted', intent='qa_plan', scope_version=1).id
        post(s, p, t, 'next-scope', intent='qa_plan', scope_version=2)
        post(s, p, other, 'other-ticket', intent='qa_plan', scope_version=1)
        post(s, p, t, 'runtime', intent='qa_plan', scope_version=1, runtime_log=True)
        for index in range(300):  # Log volume can no longer hide an older conversation row.
            post(s, p, t, f'log-{index}', runtime_log=True, generation=1)
        post(s, p, t, 'base-a', intent='baseline_evidence', scope_version=1, base_sha='a' * 40)
        post(s, p, t, 'base-b', intent='baseline_evidence', scope_version=1, base_sha='b' * 40)
    identity = {'ticket_id': t.id, 'scope_version': 1}
    with db.read() as s:
        assert ProductWorkspace.latest_intent(s, identity, 'qa_plan').id == wanted
        assert ProductWorkspace.latest_intent(s, identity, 'baseline_evidence', base_sha='a' * 40).body == 'base-a'
        assert ProductWorkspace.latest_intent(s, identity, 'technical_plan') is None
        assert ProductWorkspace.latest_intent(s, {**identity, 'scope_version': 3}, 'qa_plan') is None


@pytest.mark.parametrize('gate, cacheable', [
    ({'status': 'passed', 'infrastructure_failure': False, 'counts': {'executed': 3}}, True),
    # An existing repository with deterministic failures is cached as evidence;
    # waiver eligibility is decided again by every caller.
    ({'status': 'failed', 'infrastructure_failure': False, 'counts': {'executed': 3, 'failed': 1}}, True),
    ({'status': 'failed', 'infrastructure_failure': True, 'counts': {'executed': 3}}, False),
    ({'status': 'failed', 'counts': {'executed': 3}}, False),
    ({'status': 'incomplete', 'infrastructure_failure': False, 'counts': {}}, False),
    ({'status': 'failed', 'infrastructure_failure': False}, False),
])
def test_baseline_cache_admits_only_deterministic_gate_outcomes(gate, cacheable):
    assert ProductWorkspace._cacheable_gate(gate) is cacheable


class _Queue:
    def verify(self, lease):
        return {'ticket_id': 't', 'scope_version': 1}


def _checks(contract):
    from app.pipeline.checks import DeveloperChecks
    workspace = SimpleNamespace(ui_contract=lambda identity: contract)
    return DeveloperChecks(SimpleNamespace(queue=_Queue(), lease=None), None, None, workspace)


def _contract():
    from app.agents.ui_contract import UiContract
    return UiContract.model_validate({'revision': 1, 'controls': [
        {'testid': 'save-button', 'role': 'button', 'name': 'Save', 'purpose': 'Save the form'},
        {'testid': 'name-input', 'role': 'textbox', 'name': 'Name', 'purpose': 'Item name'}]})


def test_run_checks_reports_missing_testids_on_own_build_before_submit(tmp_path):
    manifest = SimpleNamespace(build_output='dist')
    (tmp_path / 'dist' / 'assets').mkdir(parents=True)
    (tmp_path / 'dist' / 'index.html').write_text('<button data-testid="save-button">Save</button>')
    (tmp_path / 'dist' / 'assets' / 'app.js').write_text('const x = {"data-testid": "other"}')
    row = _checks(_contract())._ui_inventory(manifest, tmp_path)
    assert row['phase'] == 'ui_contract' and row['status'] == 'failed' and row['qa_pass'] is False
    assert row['missing_testids'] == ['name-input'] and 'run_checks again' in row['next']
    (tmp_path / 'dist' / 'assets' / 'app.js').write_text('h("input", {"data-testid": "name-input"})')
    assert _checks(_contract())._ui_inventory(manifest, tmp_path)['status'] == 'passed'


def test_run_checks_ui_inventory_fails_closed_without_output_and_skips_without_contract(tmp_path):
    manifest = SimpleNamespace(build_output='dist')
    assert _checks(None)._ui_inventory(manifest, tmp_path) is None
    row = _checks(_contract())._ui_inventory(manifest, tmp_path)
    assert row['status'] == 'failed' and 'missing' in row['reason']
    (tmp_path / 'elsewhere').mkdir()
    (tmp_path / 'dist').symlink_to(tmp_path / 'elsewhere')
    assert _checks(_contract())._ui_inventory(manifest, tmp_path)['status'] == 'failed'


def test_install_receipt_digest_ignores_tmpfs_masked_scratch(tmp_path):
    from app.pipeline.checks import DeveloperChecks
    package = tmp_path / 'node_modules' / 'pkg'
    package.mkdir(parents=True)
    (package / 'index.js').write_text('module.exports = 1')
    before = DeveloperChecks._tree(tmp_path, None)
    for scratch in ('.cache', '.vite'):
        (tmp_path / 'node_modules' / scratch / 'babel-loader').mkdir(parents=True)
        (tmp_path / 'node_modules' / scratch / 'babel-loader' / 'x.json').write_text('{}')
    assert DeveloperChecks._tree(tmp_path, None) == before
    (package / 'index.js').write_text('module.exports = 2')
    assert DeveloperChecks._tree(tmp_path, None) != before


class _Locator:
    def __init__(self, calls, path):
        self.calls, self.path = calls, path

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return _Locator(self.calls, self.path + [name])
        return call


@pytest.fixture
def runner(monkeypatch):
    api = types.ModuleType('playwright.sync_api')
    api.sync_playwright = api.expect = None
    monkeypatch.setitem(sys.modules, 'playwright', types.ModuleType('playwright'))
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', api)
    import importlib.util
    path = Path(__file__).resolve().parents[4] / 'contracts' / 'verification' / 'acceptance.py'
    spec = importlib.util.spec_from_file_location('acceptance_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_legacy_suite_keeps_playwright_engine_role_semantics(runner):
    calls = []
    runner.LEGACY_LOCATORS = True
    runner.locate(_Locator(calls, []), 'role=button[name="Save"]')
    assert calls == [('locator', ('role=button[name="Save"]',), {})]
    calls.clear()
    runner.LEGACY_LOCATORS = False
    runner.locate(_Locator(calls, []), 'role=button[name="Save"]')
    assert calls == [('get_by_role', ('button',), {'name': 'Save', 'exact': True})]
    calls.clear()
    runner.LEGACY_LOCATORS = True  # Not Playwright engines: canonical parsing in every mode.
    runner.locate(_Locator(calls, []), 'testid=save-button')
    assert calls == [('get_by_test_id', ('save-button',), {})]


def test_harness_marks_only_targets_without_ui_contract_as_legacy():
    from app.pipeline.harness import legacy_locators
    assert legacy_locators({'runner': {}}) is True
    assert legacy_locators({'ui_contract': {'revision': 1, 'controls': []}}) is False

@pytest.mark.parametrize('operation', ['digest', 'copy'])
def test_stale_file_scan_cannot_block_on_fifo(tmp_path, operation):
    import os
    from app.workspace.fsutil import TreeEntry, copy_entries, sha256_tree
    from app.workspace.errors import PathViolation
    source = tmp_path / 'source'
    destination = tmp_path / 'destination'
    source.mkdir()
    destination.mkdir()
    os.mkfifo(source / 'changed')
    entries = [TreeEntry('changed', 'file')]
    with pytest.raises(PathViolation, match='special file'):
        if operation == 'digest':
            sha256_tree(source, entries)
        else:
            copy_entries(source, entries, destination, sandbox_visible=True)
    assert not (destination / 'changed').exists()
