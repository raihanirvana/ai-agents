import pytest

pytest.importorskip('fcntl')  # the workspace/manifest package is POSIX-only
from sqlalchemy import select
from app.persistence.models import Job, Project
from app.workspace.manifest import reference_manifest_dict


def test_onboarding_admission_is_idempotent_and_does_not_access_source_in_http(api):
    p = api.cmd('/projects', {'name': 'Existing', 'mode': 'existing', 'repo_ref': '/does/not/exist'}).json()['project']
    body = {'expected_revision': p['revision'], 'manifest': reference_manifest_dict()}
    path = '/projects/' + p['id'] + '/onboarding'
    first = api.cmd(path, body, key='same')
    assert first.status_code == 200, first.text
    assert api.cmd(path, body, key='same').json() == first.json()
    assert first.json()['project']['onboarding'] == 'queued'
    assert api.cmd(path, body).status_code == 409
    with api.db.read() as s:
        jobs = list(s.scalars(select(Job).where(Job.project_id == p['id'])))
        assert len(jobs) == 1 and jobs[0].runtime_ref['runtime'] == 'onboarding'
        assert s.get(Project, p['id']).workflow.get('accepted_tip') is None


def test_onboarding_rejects_unsupported_manifest_and_patch_without_sha(api):
    p = api.cmd('/projects', {'name': 'Existing', 'mode': 'existing', 'repo_ref': '/source'}).json()['project']
    path = '/projects/' + p['id'] + '/onboarding'
    raw = reference_manifest_dict()
    raw['runner'] = 'django'
    assert api.cmd(path, {'expected_revision': p['revision'], 'manifest': raw}).status_code == 422
    assert api.cmd(path, {'expected_revision': p['revision'], 'manifest': reference_manifest_dict(), 'patch': 'patch'}).status_code == 422
    assert api.cmd(path, {'expected_revision': 99, 'manifest': reference_manifest_dict()}).status_code == 409
    with api.db.read() as s:
        assert s.scalar(select(Job.id).where(Job.project_id == p['id'])) is None


def test_new_project_cannot_use_existing_onboarding(api):
    p = api.project()
    assert api.cmd('/projects/' + p.id + '/onboarding', {'expected_revision': p.revision,
        'manifest': reference_manifest_dict()}).status_code == 409
