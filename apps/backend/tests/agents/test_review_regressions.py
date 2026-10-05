"""DEV-007 review regressions: real database/artifacts, labelled fake provider only."""
import json
import pytest
from sqlalchemy import select
from tests.agents.conftest import agent_env, db, db_path, store, LIMITS, SECRET, proposal, ticket_spec
from tests.agents.test_runtime import PLAN, CLARIFY
from tests.agents.test_threads import direct_ask
from app.agents import Usage
from app.persistence.models import Job, Message, Ticket
from app.workers.runtime import WaitingForInput
from app.workers.queue import StaleLease
from app.domain import Forbidden
from app.persistence import IdempotencyConflict


def test_prompt_token_report_enforces_total_budget(agent_env):
    env = agent_env
    job = env.job('po', 'breakdown', limits={**LIMITS, 'total_tokens': 100})
    ctx = env.ctx(job)
    env.script(('{}', Usage(prompt_tokens=90, completion_tokens=10)))
    env.client.complete(ctx, 'po', 'system', 'user')
    assert env.get(job.id).status == 'stopped', env.get(job.id).usage


def test_same_tool_after_retry_does_not_duplicate_ticket(agent_env):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    doc = {'title': 'Menu', 'uac': [{'id': 'U1', 'text': 'Shows menu'}]}
    first = env.tools.call(ctx, 'propose_ticket', doc)
    retry_id = env.queue.fail(ctx.lease, 'transient', retryable=True)
    second_ctx = env.ctx(env.get(retry_id))
    second = env.tools.call(second_ctx, 'propose_ticket', doc)
    assert first['ticket_id'] == second['ticket_id']


def test_retry_after_result_message_was_written_recovers(agent_env):
    env = agent_env
    answer = proposal(ticket_spec('T1'))
    env.script(answer, answer)
    ctx = env.ctx(env.job('po', 'breakdown'))
    assert env.runtime.run(ctx).status == 'succeeded'
    retry = env.queue.fail(ctx.lease, 'crash before queue.complete', retryable=True)
    result = env.runtime.run(env.ctx(env.get(retry)))
    assert result.status == 'succeeded'
    assert len(env.provider.requests) == 1
    assert len(env.messages(intent='po_breakdown')) == 1
    with env.db.read() as s:
        assert len(list(s.scalars(select(Ticket).where(Ticket.project_id == env.project.id)))) == 1


def test_cancelled_reply_cannot_resume_developer_in_write_race(agent_env, monkeypatch):
    env = agent_env
    dev_id, request_id, lead_job = direct_ask(env, env.approved_ticket())
    ctx = env.ctx(lead_job)
    original = env.threads.answer_request
    def cancel_then_answer(identity, *args, **kwargs):
        env.queue.cancel(lead_job.id, reason='cancel after facade.verify', actor='user:local')
        return original(identity, *args, **kwargs)
    monkeypatch.setattr(env.threads, 'answer_request', cancel_then_answer)
    with pytest.raises(StaleLease):
        env.tools.call(ctx, 'answer_message', {'request_id': request_id, 'answer': 'Proceed'})
    assert env.get(dev_id).status == 'waiting_input'


def test_cancel_after_prewrite_fence_cannot_store_lead_plan(agent_env, monkeypatch):
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('technical-lead', 'technical_plan', ticket=ticket))
    env.script(PLAN)
    original = env.runtime._fence
    def cancel_then_return(context):
        identity = original(context)
        env.queue.cancel(ctx.lease.job_id, reason='cancel after prewrite verify', actor='user:local')
        return identity
    monkeypatch.setattr(env.runtime, '_fence', cancel_then_return)
    with pytest.raises(StaleLease):
        env.runtime.run(ctx)
    assert not env.messages(intent='technical_plan')


def test_tools_redact_secret_before_persisting_message(agent_env):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    result = env.tools.call(ctx, 'send_message', {'category': 'note', 'body': 'Key is ' + SECRET})
    with env.db.read() as s:
        assert SECRET not in s.get(Message, result['message_id']).body


def test_snapshot_redacts_reference_metadata_too(agent_env):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    identity = env.queue.verify(ctx.lease)
    snapshot = env.builder.build(identity, task={'name': 'breakdown'},
        repo_refs=[{'path': 'src/' + SECRET + '.txt', 'snippet': 'hello'}], lease=ctx.lease, queue=env.queue)
    assert SECRET not in snapshot.user
    with env.db.read() as s:
        assert SECRET not in env.store.read_bytes(s, snapshot.artifact_id).decode()


def test_directed_message_crash_recovers_reply_job(agent_env, monkeypatch):
    env = agent_env
    ctx = env.ctx(env.job('developer', 'implement', ticket=env.approved_ticket(), stage='development',
                          lane='execution', runtime='fake'))
    ident = env.queue.verify(ctx.lease)
    original = env.threads._enqueue_reply
    def crash(*args, **kwargs):
        raise SystemExit('crash after message transaction')
    monkeypatch.setattr(env.threads, '_enqueue_reply', crash)
    with pytest.raises(SystemExit):
        env.threads.send(ident, to_role='technical-lead', thread_id='directed', category='clarification',
                        body='Which library?', needs_reply=True, idempotency_key='directed-question')
    monkeypatch.setattr(env.threads, '_enqueue_reply', original)
    env.threads.ensure_reply_jobs()
    [message] = env.messages(sender='agent:developer', kind='message')
    with env.db.read() as s:
        assert s.scalar(select(Job.id).where(Job.idempotency_key == 'reply:' + message.id)) is not None


def test_lead_needs_user_does_not_resume_developer(agent_env):
    env = agent_env
    dev_id, request_id, reply = direct_ask(env, env.approved_ticket())
    env.script({'kind': 'answer', 'outcome': 'needs_user', 'answer': 'Ask the user which behavior they want.'})
    ctx = env.ctx(reply)
    result = env.runtime.run(ctx)
    assert result.status == 'succeeded'
    assert env.get(dev_id).status == 'waiting_input'
    user_request = result.result['user_request_id']
    assert env.get(dev_id).waiting_request_id == user_request
    identity = env.queue.verify(ctx.lease)
    with env.db.read() as s:
        view = env.threads.input_request(s, user_request)
        assert (view.status, view.recipient, view.job_id) == ('open', 'user', dev_id)
        assert s.get(Message, user_request).reply_to == request_id
    with pytest.raises(Forbidden):
        env.threads.answer_request(identity, user_request, body='Guess', answer_key='agent-guess')
    first = env.threads.answer_request(env.world.user, user_request, body='Remove it', answer_key='user-choice')
    again = env.threads.answer_request(env.world.user, user_request, body='Remove it', answer_key='user-choice')
    assert first[1] is True and again == (first[0], False)
    assert env.get(dev_id).status == 'queued'


@pytest.mark.parametrize('effect', ['send', 'reply', 'decision'])
def test_each_thread_write_checks_the_live_responder(agent_env, effect):
    env = agent_env
    dev_id, request_id, lead_job = direct_ask(env, env.approved_ticket())
    ctx = env.ctx(lead_job)
    identity = env.queue.verify(ctx.lease)
    env.queue.cancel(lead_job.id, reason='revoked', actor='user:local')
    with pytest.raises(StaleLease):
        if effect == 'send':
            env.threads.send(identity, to_role=None, thread_id='t', category='note', body='stale',
                             idempotency_key='stale-send')
        elif effect == 'reply':
            env.threads.reply(identity, request_id, body='stale', key='stale-reply')
        else:
            env.threads.propose_decision(identity, title='stale', rationale='stale', key='stale-decision')
    assert env.get(dev_id).status == 'waiting_input'
    assert not env.messages(sender='agent:technical-lead')


def test_a_role_name_without_a_run_is_not_an_answer_capability(agent_env):
    env = agent_env
    dev_id, request_id, _ = direct_ask(env, env.approved_ticket())
    with pytest.raises(StaleLease):
        env.threads.answer_request({'role': 'technical-lead', 'project_id': env.project.id}, request_id,
                                   body='Proceed', answer_key='fabricated')
    assert env.get(dev_id).status == 'waiting_input'


def test_plan_retry_replays_saved_output_and_keeps_original_evidence(agent_env):
    env = agent_env
    env.script(PLAN)
    ctx = env.ctx(env.job('technical-lead', 'technical_plan', ticket=env.approved_ticket()))
    first = env.runtime.run(ctx)
    retry = env.queue.fail(ctx.lease, 'crash before completion', retryable=True)
    second = env.runtime.run(env.ctx(env.get(retry)))
    assert first.result['plan_message_id'] == second.result['plan_message_id']
    assert first.result['decision_proposal_ids'] == second.result['decision_proposal_ids']
    assert first.result['context_artifact_id'] == second.result['context_artifact_id']
    assert env.get(retry).context_artifact_id == first.result['context_artifact_id']
    assert len(env.provider.requests) == 1
    assert len(env.messages(intent='technical_plan')) == len(env.messages(intent='decision_proposal')) == 1


def test_tool_message_retry_keeps_one_reply_job_and_checks_payload(agent_env):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    args = {'to_role': 'technical-lead', 'category': 'clarification', 'body': 'Which library?', 'needs_reply': True}
    first = env.tools.call(ctx, 'send_message', args)
    retry = env.queue.fail(ctx.lease, 'crash', retryable=True)
    # Claiming the reply would interfere with this deterministic retry; hold it in its lane.
    with env.db.write() as s:
        from datetime import timedelta
        from app.persistence.columns import utcnow
        s.get(Job, first['reply_job_id']).available_at = utcnow() + timedelta(hours=1)
    second_ctx = env.ctx(env.get(retry))
    second = env.tools.call(second_ctx, 'send_message', args)
    assert second == first
    key = env.tools._key(env.queue.verify(second_ctx.lease), 'send_message', args)
    with pytest.raises(IdempotencyConflict):
        env.threads.send(env.queue.verify(second_ctx.lease), to_role='technical-lead', thread_id=f'ticket:{env.project.id}',
                         category='clarification', body='Different payload', needs_reply=True, idempotency_key=key)


def test_arbitrary_hash_in_enqueue_key_does_not_merge_roots(agent_env):
    env = agent_env
    env.script(proposal(ticket_spec('T1')), proposal(ticket_spec('T1')))
    ids = []
    for key in ('user#one', 'user#two'):
        ctx = env.ctx(env.job('po', 'breakdown', key=key))
        outcome = env.runtime.run(ctx)
        ids.extend(outcome.result['created_ticket_ids'])
        env.queue.complete(ctx.lease, outcome.result)
    assert len(set(ids)) == 2


def test_directed_outbox_keeps_lane_and_does_not_schedule_cancelled_origin(agent_env, monkeypatch):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    identity = env.queue.verify(ctx.lease)
    original = env.threads._enqueue_reply
    monkeypatch.setattr(env.threads, '_enqueue_reply', lambda *a, **k: None)
    sent = env.threads.send(identity, to_role='technical-lead', thread_id='t', category='handoff',
                            body='Long review', needs_reply=True, expected='long', idempotency_key='long-review')
    monkeypatch.setattr(env.threads, '_enqueue_reply', original)
    [reply_id] = env.threads.ensure_reply_jobs()
    assert env.get(reply_id).lane == 'execution'
    assert env.threads.ensure_reply_jobs() == []
    monkeypatch.setattr(env.threads, '_enqueue_reply', lambda *a, **k: None)
    env.threads.send(identity, to_role='technical-lead', thread_id='t', category='bug', body='Another question',
                     needs_reply=True, idempotency_key='cancelled-origin')
    env.queue.cancel(ctx.lease.job_id, reason='cancelled', actor='user:local')
    monkeypatch.setattr(env.threads, '_enqueue_reply', original)
    assert env.threads.ensure_reply_jobs() == []


def test_tool_checkpoint_and_snapshot_metadata_redact_opaque_known_secret(agent_env):
    env = agent_env
    opaque = 'fixture-opaque-provider-value'
    env.threads.redactor = env.redactor.with_secrets(opaque)
    ctx = env.ctx(env.job('developer', 'implement', ticket=env.approved_ticket(), stage='development',
                          lane='execution', runtime='fake'))
    with pytest.raises(WaitingForInput):
        env.tools.call(ctx, 'request_decision', {'question': 'Question ' + opaque, 'checkpoint': {opaque: opaque}})
    stored = env.get(ctx.lease.job_id)
    with env.db.read() as s:
        request = s.get(Message, stored.waiting_request_id)
        assert opaque not in request.body + json.dumps(request.meta)


@pytest.mark.parametrize('usage, expected, unknown', [
    (Usage(prompt_tokens=100), 100, True),
    (Usage(completion_tokens=100), 100, True),
    (Usage(prompt_tokens=90, completion_tokens=10, total_tokens=100), 100, False),
])
def test_token_lower_bounds_and_unknown_accounting(agent_env, usage, expected, unknown):
    env = agent_env
    job = env.job('po', 'breakdown', limits={**LIMITS, 'total_tokens': 100})
    ctx = env.ctx(job)
    env.script(('{}', usage))
    env.client.complete(ctx, 'po', 's', 'u')
    row = env.get(job.id)
    assert row.status == 'stopped' and row.usage['total_tokens'] == expected
    assert ('total_tokens' in row.usage.get('_unknown', [])) == unknown


def test_retry_after_user_resume_keeps_answer_and_validated_proposal(agent_env):
    env = agent_env
    env.script(CLARIFY, proposal(ticket_spec('T1')))
    ctx = env.ctx(env.job('po', 'breakdown'))
    with pytest.raises(WaitingForInput):
        env.runtime.run(ctx)
    env.threads.answer_request(env.world.user, env.get(ctx.lease.job_id).waiting_request_id,
                               body='Remove the row', answer_key='a')
    sup = env.supervisor()
    resumed_ctx = env.ctx(env.get(ctx.lease.job_id))
    _, resumed_ctx.answer = sup._snapshot(ctx.lease.job_id)
    first = env.runtime.run(resumed_ctx)
    retry = env.queue.fail(resumed_ctx.lease, 'crash after resumed result', retryable=True)
    retry_ctx = env.ctx(env.get(retry))
    _, retry_ctx.answer = sup._snapshot(retry)
    assert retry_ctx.answer == 'Remove the row'
    second = env.runtime.run(retry_ctx)
    assert second.result['created_ticket_ids'] == first.result['created_ticket_ids']
    assert len(env.provider.requests) == 2


def test_escalation_remains_blocked_after_restart_and_late_answer_after_cancel(agent_env):
    env = agent_env
    dev_id, request_id, reply = direct_ask(env, env.approved_ticket())
    env.script({'kind': 'answer', 'outcome': 'needs_user', 'answer': 'User must choose'})
    ctx = env.ctx(reply)
    result = env.runtime.run(ctx)
    question_id = result.result['user_request_id']
    # Complete/reopen through a fresh queue object: the pending user request is durable.
    env.queue.complete(ctx.lease, result.result)
    from app.agents import Threads
    from app.workers import JobQueue
    reopened = Threads(env.db, JobQueue(env.db))
    with env.db.read() as s:
        assert reopened.input_request(s, question_id).status == 'open'
    assert reopened.ensure_reply_jobs() == []
    env.queue.cancel(dev_id, reason='cancel', actor='user:local')
    _, resumed = reopened.answer_request(env.world.user, question_id, body='Remove', answer_key='late-user')
    assert not resumed and env.get(dev_id).status == 'cancelled'


def test_nonblocking_directed_question_can_escalate_to_visible_user_input(agent_env):
    env = agent_env
    ctx = env.ctx(env.job('developer', 'implement', ticket=env.approved_ticket(), stage='development',
                          lane='execution', runtime='fake'))
    sent = env.threads.send(env.queue.verify(ctx.lease), to_role='technical-lead', thread_id='chat',
                            category='clarification', body='Which behavior?', needs_reply=True, idempotency_key='direct')
    env.script({'kind': 'answer', 'outcome': 'needs_user', 'answer': 'The user must choose'})
    reply_ctx = env.ctx(env.get(sent.reply_job_id))
    result = env.runtime.run(reply_ctx)
    question_id = result.result['user_request_id']
    env.queue.complete(reply_ctx.lease, result.result)
    with env.db.read() as s:
        assert env.threads.input_request(s, question_id).status == 'open'
    assert len(env.events('input.escalated')) == 1
    _, resumed = env.threads.answer_request(env.world.user, question_id, body='Chosen', answer_key='user-choice')
    assert not resumed and env.get(ctx.lease.job_id).status == 'running'


def test_validated_output_context_stays_pinned_before_effects_and_through_cleanup(agent_env, monkeypatch):
    env = agent_env
    ctx = env.ctx(env.job('po', 'breakdown'))
    env.queue.begin_run(ctx.lease, {'test': 'cleanup-barrier'})
    env.script(proposal(ticket_spec('T1')))
    original = env.world.w.create_ticket
    def crash(*args, **kwargs):
        raise RuntimeError('crash before first effect')
    monkeypatch.setattr(env.world.w, 'create_ticket', crash)
    with pytest.raises(RuntimeError):
        env.runtime.run(ctx)
    artifact_id = env.get(ctx.lease.job_id).context_artifact_id
    assert env.queue.fail(ctx.lease, 'crash', retryable=True) is None  # cleanup precedes retry
    from app.persistence import pinned_artifacts
    with env.db.read() as s:
        assert artifact_id in pinned_artifacts(s)
    retry = env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    assert env.get(retry).context_artifact_id == artifact_id
    with env.db.read() as s:
        assert artifact_id in pinned_artifacts(s)
    monkeypatch.setattr(env.world.w, 'create_ticket', original)
    assert env.runtime.run(env.ctx(env.get(retry))).status == 'succeeded'
    assert len(env.provider.requests) == 1
