"""Authenticated user choices, atomic/idempotent and fenced to displayed evidence."""
from app.persistence.models import QaWaiver
from sqlalchemy import select
from tests.domain.conftest import world  # noqa: F401
from tests.domain.test_qa_manual_resolution import inconclusive
from .conftest import api, db, db_path, store  # noqa: F401


def test_inconclusive_decision_is_visible_idempotent_and_requires_csrf(api, world):
    t, _, v, args = inconclusive(world)
    detail = api.client.get('/tickets/' + t.id)
    assert detail.status_code == 200, detail.text
    resolution = detail.json()['qa_resolution']
    assert resolution['eligible'] is True
    assert resolution['target_digest'] == args['target_digest']
    body = {'expected_revision': t.revision, **args}
    path = f'/tickets/{t.id}/qa-manual-decisions'
    csrf = api.client.headers.pop('X-CSRF-Token')
    rejected = api.cmd(path, body)
    assert rejected.status_code == 403
    api.client.headers['X-CSRF-Token'] = csrf
    stale = api.cmd(path, {**body, 'target_digest': 'f' * 64})
    assert stale.status_code == 409, stale.text
    first = api.cmd(path, body, 'same-qa-choice')
    assert first.status_code == 200, first.text
    replay = api.cmd(path, body, 'same-qa-choice')
    assert replay.status_code == 200 and replay.json() == first.json()
    with world.db.read() as s:
        assert len(list(s.scalars(select(QaWaiver)))) == 1
    detail = api.client.get('/tickets/' + t.id).json()
    candidate = detail['candidates'][0]
    assert candidate['verifications'][0]['status'] == 'failed'
    assert candidate['qa_waiver']['verification_id'] == v.id
    assert candidate['preview']['verification_id'] == v.id
    assert detail['ticket']['phase'] == 'uat'


def test_project_and_ticket_presets_are_user_choices_with_scope_approval(api):
    result = api.cmd('/projects', {'name': 'Manual demo', 'qa_profile': 'manual'})
    assert result.status_code == 200, result.text
    project = result.json()['project']
    assert project['qa_profile'] == 'manual'
    ticket = api.ticket(type('Project', (), project))
    scope = api.client.get('/tickets/' + ticket.id).json()['versions'][0]
    assert scope['uac'][0]['mode'] == 'manual'
    assert ticket.phase == 'scope_review'
    result = api.cmd(f"/projects/{project['id']}/qa-profile", {
        'expected_revision': project['revision'], 'qa_profile': 'lightweight'})
    assert result.status_code == 200, result.text
    old = api.client.get('/tickets/' + ticket.id).json()['versions'][0]
    assert old['uac'] == scope['uac']
    next_ticket = api.ticket(type('Project', (), project))
    next_scope = api.client.get('/tickets/' + next_ticket.id).json()['versions'][0]
    assert next_scope['uac'][0]['mode'] == 'automated'
