"""Advisory handoffs are pinned and checked; they cannot pass/waive browser acceptance."""
from types import SimpleNamespace
import copy
import json
import pytest
from app.pipeline.contracts import CandidateSubmission, Review, TestConcern as Concern, tool_schema
from app.pipeline.test_concerns import qualify_concerns, pinned_concerns
from tests.pipeline.test_selector_repair import suite, SOURCE


def concern():
    return {'test_id': 'loan', 'step_index': 0, 'selector': '#guessed-borrower',
            'source_path': 'src/app.js', 'source_excerpt': SOURCE['src/app.js'],
            'reason': 'Borrower class is present but the guessed ID is absent.'}


class Broker:
    def read_committed_file(self, sha, path, **kw):
        assert sha == 'a' * 40
        return SOURCE.get(path, '').encode()


def test_qualified_concern_uses_exact_candidate_source_and_keeps_original_suite():
    original = suite()
    accepted, source, ignored = qualify_concerns([concern()], original, Broker(), 'a' * 40)
    assert len(accepted) == 1 and source == SOURCE and not ignored
    assert original.tests[0].steps[0].selector == '#guessed-borrower'


@pytest.mark.parametrize('field,value', [('test_id', 'missing'), ('step_index', 29),
    ('step_index', True), ('selector', '#obsolete'), ('source_path', '../escape.js'),
    ('source_path', '.git/config'), ('source_path', 'tests/app.js'),
    ('source_excerpt', 'this excerpt is fabricated'), ('step_index', 2)])
def test_invalid_or_obsolete_concern_cannot_change_suite(field, value):
    data = concern()
    data[field] = value
    accepted, _, ignored = qualify_concerns([data], suite(), Broker(), 'a' * 40)
    assert not accepted and ignored


def test_concerns_deduplicated_and_bounded():
    accepted, _, _ = qualify_concerns([concern()] * 100, suite(), Broker(), 'a' * 40)
    assert len(accepted) == 1


def test_handoff_and_review_defaults_preserve_legacy_requests_and_reject_extra_authority():
    assert CandidateSubmission(message='Short message').test_concerns == []
    assert Review(kind='review', accept=True, summary='Accepted').test_concerns == []
    assert CandidateSubmission(message='Commit', handoff='Run instructions ' * 100).handoff
    with pytest.raises(ValueError):
        CandidateSubmission(message='Too long' * 1000)
    with pytest.raises(ValueError):
        Concern.model_validate({**concern(), 'qa_pass': True})
    schema = tool_schema(CandidateSubmission)
    assert 'source_excerpt' in schema['properties']['test_concerns']['items']['properties']
    assert '$defs' not in str(schema) and '$ref' not in str(schema)


@pytest.mark.parametrize('field,value', [('candidate_id', 'other'), ('scope_version', 2),
                                       ('commit_sha', 'b' * 40), ('suite_digest', 'old'),
                                       ('intent', 'chat')])
def test_only_exact_candidate_scope_commit_suite_handoff_is_used(field, value):
    candidate = SimpleNamespace(id='candidate', scope_version=1, commit_sha='a' * 40)
    meta = {'intent': 'candidate_handoff', 'candidate_id': candidate.id, 'scope_version': 1,
            'commit_sha': candidate.commit_sha, 'suite_digest': suite().digest, 'test_concerns': [concern()]}
    assert pinned_concerns([SimpleNamespace(meta=meta)], candidate, suite()) == [concern()]
    assert not pinned_concerns([SimpleNamespace(meta={**meta, field: value})], candidate, suite())


from sqlalchemy import select
from app.persistence.models import Candidate, Message, Verification
from app.domain import Attempt
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401,E402
from tests.pipeline.test_product_loop import setup, plan, FILES


@pytest.mark.parametrize('mode', ['abstain', 'mixed_failure', 'passed'])
def test_preflight_reuses_pinned_proof_and_cannot_shortcut_normal_checks(agent_env, tmp_path, monkeypatch, mode):
    from app.pipeline.runtime import PipelineRuntime
    from app.pipeline.workspace import ProductWorkspace
    from app.pipeline.contracts import QaSelectorRepair
    from tests.pipeline.test_selector_repair import proof as observed_proof
    env = agent_env
    ticket = env.approved_ticket()
    ctx = env.ctx(env.job('qa', 'verify', ticket=ticket, stage='reply'))
    identity = env.queue.verify(ctx.lease)
    candidate = SimpleNamespace(id='candidate', project_id=env.project.id, ticket_id=ticket.id,
        scope_version=ticket.current_version, commit_sha='a' * 40, target_digest='d' * 64)
    current = suite()
    proof = observed_proof()
    if mode == 'mixed_failure':
        proof['report']['tests'].append({'id': 'other', 'status': 'failed'})
    if mode == 'passed':
        proof['status'] = proof['report']['tests'][0]['status'] = 'passed'
    calls = {'browser': 0, 'model': 0}
    def run(*args, **kw):
        calls['browser'] += 1
        return copy.deepcopy(proof)
    workspace = ProductWorkspace(env.db, env.store, env.world.w, tmp_path,
        SimpleNamespace(run=run), env.redactor)
    runtime = PipelineRuntime(env.runtime, workspace, None)
    monkeypatch.setattr('app.pipeline.runtime.FencedWorkspace', lambda *_: SimpleNamespace(broker=lambda _: Broker()))
    with env.db.write() as s:
        context = env.store.put_json(s, project_id=env.project.id, kind='report', name='fake-context.json',
                                    document={'fake': True}, meta={'producer': 'fake-test'})
        workspace._post(s, identity, 'handoff', 'Advisory fixture', [], 'candidate_handoff',
            candidate_id=candidate.id, commit_sha=candidate.commit_sha, suite_digest=current.digest,
            test_concerns=[concern()])
    def ask(*args, **kw):
        calls['model'] += 1
        return (QaSelectorRepair(kind='qa_selector_repair', summary='Uncertain intent; abstain', bindings=[]),
                {'context_artifact_id': context.id}, None)
    monkeypatch.setattr(env.runtime, '_ask', ask)
    target = {'suite_repair_count': 0, 'node_image_id': 'node', 'runner': {'fake_fixture': True}}
    for _ in range(2):
        outcome, reused = runtime._concern_preflight(ctx, identity, candidate, target, current, [], tmp_path)
        assert outcome is None and reused['status'] == proof['status']
        assert reused['preflight_evidence_artifact_ids']
    assert calls == {'browser': 1, 'model': 1 if mode == 'abstain' else 0}
    with env.db.read() as s:
        assert s.scalar(select(Verification)) is None
    candidate.target_digest = 'e' * 64
    with pytest.raises(ValueError, match='another candidate/target/concern'):
        runtime._concern_preflight(ctx, identity, candidate, target, current, [], tmp_path)
    assert calls['browser'] == 1


class ConcernDriver:
    def run(self, ctx, identity, snapshot, tools, parameters):
        if identity['role'] == 'qa':
            tools['propose_tests']({'plan': suite().model_dump()})
            return
        files = {**FILES, 'index.html': '''<input class="borrower-input">
            <button id="borrow">Borrow</button><p id="result"></p><button id="add">Add</button>
            <script>document.querySelector('#borrow').onclick=()=>{
            document.querySelector('#result').textContent=document.querySelector('.borrower-input').value;};</script>''',
            'src/app.js': SOURCE['src/app.js']}
        for name, text in files.items():
            tools['patch_file']({'path': name, 'content': text})
        tools['submit_candidate']({'message': 'Loan UI', 'handoff': 'Open the app and borrow a book.',
            'test_concerns': [concern()]})


def run_stage(env, runtime, scheduler, ticket):
    ids = scheduler.tick()
    assert ids
    ctx = env.ctx(env.get(ids[0]))
    stage = env.get(ids[0]).stage
    if stage in ('development', 'technical_review', 'qa'):
        env.world.w.bind_attempt(env.world.actor('scheduler'), ticket.id, env.world.ticket(ticket.id).revision,
                                Attempt(ctx.lease.job_id, ctx.lease.generation, ticket.current_version))
    try:
        outcome = runtime.run(ctx)
        if env.get(ids[0]).status == 'running':
            if outcome.status == 'succeeded':
                env.queue.complete(ctx.lease, outcome.result)
            else:
                env.queue.fail(ctx.lease, error=outcome.error, retryable=False)
        return outcome
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


def test_real_browser_qualified_handoff_repins_before_baseline_and_never_passes_fake_qa(agent_env, tmp_path, monkeypatch):
    env = agent_env
    env.queue.lease_s = 150
    runtime, _, scheduler = setup(env, tmp_path, ConcernDriver())
    env.script(plan(), {'kind': 'review', 'accept': True, 'summary': 'UI serves scope',
                       'test_concerns': [concern()]},
               {'kind': 'qa_selector_repair', 'summary': 'Use observed borrower control',
                'bindings': [{'test_id': 'loan', 'candidate_index': 0, 'reason': 'Same borrower input in DOM and source'}]})
    ticket = env.approved_ticket()
    for _ in range(4):
        assert run_stage(env, runtime, scheduler, ticket).status == 'succeeded'
    with env.db.read() as s:
        old = s.scalar(select(Candidate))
        old_digest = old.target_digest
        handoff = next(m for m in s.scalars(select(Message)) if m.meta.get('intent') == 'candidate_handoff')
        assert handoff.body == 'Open the app and borrow a book.'
        assert handoff.meta['test_concerns'][0]['source_excerpt'] == SOURCE['src/app.js']
    # The early correction must happen before any expensive baseline build.
    original_base = runtime.workspace.base_build
    def forbidden_base(*_):
        raise AssertionError('baseline must wait for the corrected suite')
    monkeypatch.setattr(runtime.workspace, 'base_build', forbidden_base)
    outcome = run_stage(env, runtime, scheduler, ticket)
    assert outcome.status == 'succeeded', outcome.error
    assert outcome.result['repair_kind'] == 'concern_observed_fill_selector'
    with env.db.read() as s:
        current = s.scalar(select(Candidate))
        assert current.target_digest != old_digest and current.commit_sha == old.commit_sha
        assert current.status == 'review_approved'
        assert s.scalar(select(Verification)) is None  # preflight never counts as QA pass
        target = json.loads(env.store.read_bytes(s, current.target_artifact_id))
        corrected = json.loads(env.store.read_bytes(s, target['suite_artifact_id']))
        expected = suite().model_dump()
        expected['tests'][0]['steps'][0]['selector'] = '.borrower-input'
        assert corrected == expected
    assert env.world.ticket(ticket.id).workflow['repair_cycles'] == 0
    monkeypatch.setattr(runtime.workspace, 'base_build', original_base)
    outcome = run_stage(env, runtime, scheduler, ticket)
    assert outcome.status == 'failed'  # explicit fake provider still cannot advance UAT
    with env.db.read() as s:
        verification = s.scalar(select(Verification))
        assert verification.counts['passed'] == 1 and verification.results['fake_provider'] is True
        assert verification.status == 'incomplete'
    assert env.world.ticket(ticket.id).phase == 'qa'
