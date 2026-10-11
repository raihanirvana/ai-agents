"""Labelled fake UI/UX driver and per-role telemetry (DEV-021/026). No model or provider."""
import json

from app.agents.models import ModelRequest
from app.agents.ui_spec import UiSpec
from app.pipeline.fake_ui_ux import FAKE_SPEC, FakeUiUxDriver
from app.pipeline.metrics import report
from tests.persistence import factories as f
from tests.persistence.conftest import db, db_path, store  # noqa: F401


def request(task):
    return ModelRequest(system='s', user='## Task\n' + json.dumps(task), max_output_tokens=10, model='fake')


def test_fake_spec_is_valid_labelled_and_additive_on_amendment():
    driver = FakeUiUxDriver()
    first = UiSpec.model_validate(driver(request({'name': 'ui_spec', 'previous_ui_spec': None})))
    assert first.summary.startswith('FAKE') and first.revision == 1
    amended = UiSpec.model_validate(driver(request({'name': 'ui_spec', 'spec_amendment': {'revision': 1},
                                                    'previous_ui_spec': first.model_dump()})))
    assert amended.revision == 2 and set(first.copy_map().items()) < set(amended.copy_map().items())
    assert [c.model_dump() for c in amended.components] == [c.model_dump() for c in first.components]
    assert driver.requests == 2 and FAKE_SPEC['revision'] == 1  # the template is never mutated


def test_other_roles_are_delegated():
    replies = [{'kind': 'technical_plan'}]
    driver = FakeUiUxDriver(fallback=replies)
    assert driver(request({'name': 'technical_plan'})) == {'kind': 'technical_plan'} and replies == []
    assert FakeUiUxDriver()(request({'name': 'qa_plan'})) == '{}'


def test_metrics_report_lists_every_role_including_ui_ux(db):
    with db.write() as s:
        p = f.project(s)
        t = f.ticket(s, p)
        f.job(s, p, t, stage='ui_spec', status='succeeded', usage={'cost_usd': 0.002, 'total_tokens': 900},
              runtime_ref={'role': 'ui-ux', 'payload': {'task': 'ui_spec'},
                           'telemetry': {'phases': {'model': {'count': 1, 'total_s': 3.5, 'failed': 0, 'cache_hits': 0}}}})
    result = report(db, p.id)
    assert {'po', 'ui-ux', 'technical-lead', 'developer', 'qa'} <= set(result['by_role'])
    ui = result['by_role']['ui-ux']
    assert ui['jobs'] == 1 and ui['usage']['cost_usd'] == 0.002 and ui['phases']['model']['total_s'] == 3.5
    assert result['by_role']['qa']['jobs'] == 0


def test_ticket_detail_exposes_the_current_scope_spec(db):
    from app.http.queries import ui_spec
    from app.persistence import append_message
    with db.write() as s:
        p = f.project(s)
        t = f.ticket(s, p)
        assert ui_spec(s, t) is None
        for revision, spec in ((1, FAKE_SPEC), (2, {**FAKE_SPEC, 'revision': 2})):
            append_message(s, project_id=p.id, ticket_id=t.id, thread_id='ticket:' + t.id, sender='agent:ui-ux',
                           body=f'Spec r{revision}', idempotency_key=f'spec-{revision}',
                           meta={'intent': 'ui_spec', 'scope_version': t.current_version, 'spec': spec,
                                 'no_ui_change': False, 'ui_spec_revision': revision, 'fake': True})
        append_message(s, project_id=p.id, ticket_id=t.id, thread_id='ticket:' + t.id, sender='agent:ui-ux',
                       body='old scope', idempotency_key='spec-old',
                       meta={'intent': 'ui_spec', 'scope_version': t.current_version + 1, 'spec': None,
                             'no_ui_change': True, 'ui_spec_revision': 0})
        current = ui_spec(s, t)
    assert current == {'revision': 2, 'no_ui_change': False, 'summary': 'Spec r2', 'fake': True, 'spec': {**FAKE_SPEC, 'revision': 2}}


def test_fake_driver_routes_using_task_instead_of_quoted_conversation():
    task = request({'name': 'technical_plan', 'context': {'name': 'ui_spec'}})
    driver = FakeUiUxDriver(fallback=[{'kind': 'technical_plan'}])
    assert driver(task) == {'kind': 'technical_plan'}
    assert driver.requests == 0


def test_fake_amendment_reads_previous_spec_from_task_only():
    from dataclasses import replace
    old = {**FAKE_SPEC, 'revision': 99}
    actual = request({'name': 'ui_spec', 'previous_ui_spec': FAKE_SPEC})
    actual = replace(actual, user='## Conversation\n' + json.dumps({'previous_ui_spec': old}) + '\n' + actual.user)
    assert FakeUiUxDriver()(actual)['revision'] == 2
