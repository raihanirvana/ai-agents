"""Role ui-ux registration and the read-only design reference tool (DEV-020).

Real DB/queue with the labelled fake provider; no model output is produced or trusted.
"""
import json
import os

import pytest
from sqlalchemy import select

from app.agents import ROLES, load_agents
from app.agents.context import ContextRefused, NEEDS_APPROVED_SCOPE
from app.agents.models import ModelRegistry
from app.agents.souls import REPO_AGENTS_DIR
from app.agents.tools import NOT_WIRED, TOOL_POLICY, NotWired, read_design_reference
from app.domain import Forbidden, Invalid
from app.persistence import append_message
from app.persistence.models import Job
from app.workspace import runspec
from app.workspace.supervisor import ROLE_OPERATIONS
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401


def test_ui_ux_is_a_registered_role_with_its_own_definition():
    assert 'ui-ux' in ROLES and runspec.ROLES == ROLES
    agent = load_agents()['ui-ux']
    assert 'UiSpec' in agent.soul and 'propose_ui_spec' in agent.instructions
    instructions = agent.instructions
    principles = instructions.split('## Prinsip visual', 1)[1].split('## Aturan copy', 1)[0]
    assert len(principles.encode()) <= 3 * 1024  # summary only; full text via read_design_reference
    assert 'ui-ux' in NEEDS_APPROVED_SCOPE


def test_ui_ux_reads_and_proposes_but_never_writes_or_approves():
    policy = TOOL_POLICY['ui-ux']
    assert policy == {'read_criteria', 'read_repo', 'read_design_reference', 'propose_ui_spec', 'request_input'}
    forbidden = {'patch_file', 'write_file', 'edit_file', 'run_command', 'run_checks', 'submit_candidate',
                 'propose_tests', 'propose_ticket', 'propose_criteria', 'review_candidate'}
    assert not policy & forbidden
    assert ROLE_OPERATIONS['ui-ux'] == {'read_file', 'list_files'}
    assert 'propose_ui_spec' in NOT_WIRED  # wired with the ui_spec stage (DEV-021)
    # Developer access to the reference arrives with DEV-022, not before.
    assert 'read_design_reference' not in TOOL_POLICY['developer']


def test_model_configuration_covers_ui_ux():
    example = json.loads((REPO_AGENTS_DIR / 'models.example.json').read_text())
    assert ModelRegistry.from_dict(example).for_role('ui-ux').stream is True


def test_reference_lists_and_reads_only_pinned_text_files():
    listing = read_design_reference({'name': ''})['references']
    assert 'frontend-design/SKILL.md' in listing and 'frontend-design/LICENSE.txt' in listing
    result = read_design_reference({'name': 'frontend-design/SKILL.md'})
    assert result['content'].startswith('---\nname: frontend-design')
    assert result['sha256'] == 'd91970639e9f5c37682ac7ab60094d35f1c7c1f38d731bd56396563aee10c1d3'


@pytest.mark.parametrize('name', ['../SOUL.md', '../../developer/SOUL.md', '/etc/passwd',
                                  'frontend-design/../../SOUL.md', 'frontend-design/missing.md',
                                  'frontend-design'])
def test_reference_cannot_leave_its_folder(name):
    with pytest.raises(Invalid):
        read_design_reference({'name': name})


@pytest.mark.parametrize('args', [{}, {'name': 3}, {'name': 'x', 'path': 'y'}, {'name': 'a' * 201},
                                  None, 'name', ['name']])
def test_reference_arguments_are_strict(args):
    with pytest.raises(Invalid):
        read_design_reference(args)


def test_symlinks_and_other_file_types_are_never_served(tmp_path):
    root = tmp_path / 'references'
    (root / 'pinned').mkdir(parents=True)
    (root / 'pinned' / 'GUIDE.md').write_text('guide')
    (root / 'pinned' / 'script.js').write_text('alert(1)')
    (root / 'pinned' / 'alias.md').symlink_to('GUIDE.md')
    assert read_design_reference({'name': ''}, root=root)['references'] == ['pinned/GUIDE.md']
    for name in ('pinned/script.js', 'pinned/alias.md'):
        with pytest.raises(Invalid):
            read_design_reference({'name': name}, root=root)
    secret = tmp_path / 'secret.md'
    secret.write_text('outside')
    os.symlink(secret, root / 'pinned' / 'escape.md')
    with pytest.raises(Invalid, match='unavailable'):  # an escaping link poisons the whole tree
        read_design_reference({'name': 'pinned/GUIDE.md'}, root=root)


def test_facade_enforces_the_role_policy(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('ui-ux', 'ui_spec', ticket=ticket, stage='ui_spec'))
    try:
        result = env.tools.call(ctx, 'read_design_reference', {'name': 'frontend-design/SOURCE.md'})
        assert 'dbd4588f9e1033efb41dad4bef2f7947c8993d44' in result['content']
        with pytest.raises(NotWired, match='DEV-021'):
            env.tools.call(ctx, 'propose_ui_spec', {'spec': {}})
        with pytest.raises(Forbidden):
            env.tools.call(ctx, 'propose_tests', {'plan': {}})
    finally:
        ctx.stop_resources()
    developer = env.ctx(env.job('developer', 'implement', ticket=ticket, stage='development', lane='execution'))
    try:
        with pytest.raises(Forbidden):
            env.tools.call(developer, 'read_design_reference', {'name': 'frontend-design/SKILL.md'})
    finally:
        developer.stop_resources()


@pytest.mark.parametrize('tool', ['send_message', 'request_decision'])
def test_unwired_ui_ux_replies_are_rejected_before_persistent_effects(agent_env, tool):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('developer', 'implement', ticket=ticket, stage='development', lane='execution'))
    before = [m.id for m in env.messages() if not m.meta.get('runtime_log')]
    args = ({'to_role': 'ui-ux', 'body': 'Which copy?', 'category': 'clarification', 'needs_reply': True}
            if tool == 'send_message' else {'to_role': 'ui-ux', 'question': 'Which copy?'})
    try:
        with pytest.raises(Invalid, match='DEV-021'):
            env.tools.call(ctx, tool, args)
        # Tool audit logs/usage are retained even for rejected calls; no question is saved.
        assert [m.id for m in env.messages() if not m.meta.get('runtime_log')] == before
        assert env.get(ctx.lease.job_id).status == 'running'
        assert env.get(ctx.lease.job_id).waiting_request_id is None
        with env.db.read() as s:
            assert list(s.scalars(select(Job).where(Job.stage == 'reply'))) == []
    finally:
        ctx.stop_resources()


def test_reconciler_does_not_schedule_unwired_ui_ux_reply(agent_env):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('developer', 'implement', ticket=ticket, stage='development', lane='execution'))
    try:
        # Simulate a pending directed message saved by the original DEV-020 code.
        identity = env.queue.verify(ctx.lease)
        with env.db.write() as s:
            append_message(s, project_id=env.project.id, ticket_id=ticket.id,
                thread_id='ui-ux-legacy', sender='agent:developer', recipient='role:ui-ux',
                body='Which copy?', idempotency_key='legacy-ui-ux-question',
                meta={'category': 'clarification', 'needs_reply': True, 'expected': 'short',
                      'job_id': identity['job_id'], 'generation': identity['generation'],
                      'scope_version': identity['scope_version'], 'fake': True})
        assert env.threads.ensure_reply_jobs() == []
        with env.db.read() as s:
            assert list(s.scalars(select(Job).where(Job.stage == 'reply'))) == []
    finally:
        ctx.stop_resources()


@pytest.mark.parametrize('approved', [False, True])
def test_ui_ux_context_requires_approved_scope(agent_env, approved):
    env = agent_env
    ticket = env.approved_ticket() if approved else env.world.new()
    ctx = env.ctx(env.job('ui-ux', 'ui_spec', ticket=ticket, stage='ui_spec'))
    try:
        identity = env.queue.verify(ctx.lease)
        if approved:
            assert env.builder.build(identity, task={'name': 'ui_spec'}).manifest['role'] == 'ui-ux'
        else:
            with pytest.raises(ContextRefused, match='has approved'):
                env.builder.build(identity, task={'name': 'ui_spec'})
    finally:
        ctx.stop_resources()


@pytest.mark.parametrize('size', [64 * 1024, 64 * 1024 + 1])
def test_reference_size_limit(tmp_path, size):
    root = tmp_path / 'references'
    root.mkdir()
    (root / 'GUIDE.md').write_bytes(b'x' * size)
    if size > 64 * 1024:
        with pytest.raises(Invalid, match='cannot be read'):
            read_design_reference({'name': 'GUIDE.md'}, root=root)
    else:
        assert read_design_reference({'name': 'GUIDE.md'}, root=root)['content'] == 'x' * size
