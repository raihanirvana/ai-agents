"""UI/UX spec stage, scheduler order and spec amendments (DEV-021).

Product DB/Git/scheduler with the labelled fake provider and fake drivers; no model,
QA pass or UAT evidence is produced. Real Docker is used only for workspace setup.
"""
import copy

import pytest
from sqlalchemy import select

from app.persistence.models import Job, Message, Project, Ticket
from app.agents.ui_spec import UiSpec
from tests.agents.conftest import agent_env, db, db_path, store  # noqa: F401
from tests.agents.test_ui_spec import SPEC
from tests.pipeline.test_contract_amendment import finish
from tests.pipeline.test_product_loop import setup


CONTROLS = [{'testid': 'add-book', 'role': 'button', 'name': 'Tambah buku', 'purpose': 'add a book'}]


def plan(revision=1, controls=CONTROLS):
    return {'kind': 'technical_plan', 'summary': 'Implement the catalogue', 'steps': [{'title': 'Catalogue'}],
            'ui_contract': {'revision': revision, 'controls': controls}}


def spec(revision=1, **changes):
    document = copy.deepcopy(SPEC)
    document.update(revision=revision, **changes)
    return document


def amended_spec():
    document = spec(2)
    document['copy'].append({'key': 'catalog.loading', 'text': 'Memuat katalog'})
    document['states'].append({'screen': 'catalog', 'kind': 'loading', 'copy_key': 'catalog.loading'})
    return document


class SpecDriver:
    """QA stand-in: asks UI/UX for an amendment first, then proposes a suite."""

    def __init__(self):
        self.calls = 0

    def run(self, ctx, identity, snapshot, tools, parameters):
        self.calls += 1
        if self.calls == 1:
            assert 'request_spec_amendment' in parameters
            response = tools['request_spec_amendment']({'reason': 'UAC-1 needs a loading state for the catalogue.'})
            assert response['spec_amendment_requested']
        else:
            tools['propose_tests']({'plan': {'kind': 'qa_plan', 'summary': 'Add a book', 'tests': [
                {'id': 'add', 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': [
                    {'action': 'click', 'selector': 'testid=add-book'},
                    {'action': 'assert_visible', 'selector': 'testid=add-book'}]}]}})
        return {}


def enable_ui_ux(env):
    with env.db.write() as s:
        project = s.get(Project, env.project.id)
        project.workflow = {**project.workflow, 'pipeline': {**project.workflow['pipeline'], 'ui_ux': True}}


def spec_messages(env, ticket):
    with env.db.read() as s:
        return [m for m in s.scalars(select(Message).where(Message.ticket_id == ticket.id).order_by(Message.seq))
                if m.meta.get('intent') == 'ui_spec']


def test_ui_spec_runs_first_and_feeds_the_technical_plan(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    env.script(spec(), plan())
    ticket = env.approved_ticket()
    job = finish(env, runtime, scheduler, 'ui_spec')
    assert job.lane == 'interactive' and job.runtime_ref['role'] == 'ui-ux'
    assert job.result['ui_spec_revision'] == 1 and job.result['no_ui_change'] is False
    [message] = spec_messages(env, ticket)
    assert message.meta['spec']['copy'][0] == {'key': 'catalog.title', 'text': 'Katalog Buku'}
    tl = finish(env, runtime, scheduler, 'technical_plan')
    assert tl.result['ui_spec_revision'] == 1
    assert 'ui_spec_policy' in str(env.provider.requests[-1])  # TL received the spec
    assert env.get(scheduler.tick()[0]).stage == 'qa_plan'


def test_no_ui_change_goes_straight_to_the_technical_plan(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    env.script({'kind': 'no_ui_change', 'reason': 'Hanya perubahan penyimpanan data.'}, plan())
    ticket = env.approved_ticket()
    job = finish(env, runtime, scheduler, 'ui_spec')
    assert job.result == {**job.result, 'ui_spec_revision': 0, 'no_ui_change': True}
    assert spec_messages(env, ticket)[0].meta['spec'] is None
    tl = finish(env, runtime, scheduler, 'technical_plan')
    assert tl.result['ui_spec_revision'] == 0 and 'ui_spec_policy' not in str(env.provider.requests[-1])


def test_projects_without_ui_ux_and_scopes_already_planned_keep_the_earlier_order(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    env.script(plan())
    env.approved_ticket()
    finish(env, runtime, scheduler, 'technical_plan')  # ui_ux not enabled
    enable_ui_ux(env)
    # Enabling UI/UX later never inserts a spec into a scope whose planning started without it.
    assert env.get(scheduler.tick()[0]).stage == 'qa_plan'


def test_qa_spec_amendment_revises_spec_then_contract_then_qa_plan(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    changed = amended_spec()
    changed['copy'][0]['text'] = 'Katalog'  # not additive: repaired in the same UI/UX job
    env.script(spec(), plan(), changed, amended_spec(), plan(2))
    ticket = env.approved_ticket()
    finish(env, runtime, scheduler, 'ui_spec')
    finish(env, runtime, scheduler, 'technical_plan')
    qa = finish(env, runtime, scheduler, 'qa_plan')
    assert qa.result['spec_amendment'] == {'reason': 'UAC-1 needs a loading state for the catalogue.',
                                           'revision': 1, 'requested_by': 'qa'}
    calls = len(env.provider.requests)
    revised = finish(env, runtime, scheduler, 'ui_spec')
    assert revised.runtime_ref['payload']['spec_amendment']['revision'] == 1
    assert revised.result['ui_spec_revision'] == 2 and len(env.provider.requests) == calls + 2
    assert 'Spec amendment is additive' in str(env.provider.requests[-1])
    tl = finish(env, runtime, scheduler, 'technical_plan')
    assert tl.result['ui_contract_revision'] == 2 and tl.result['ui_spec_revision'] == 2
    assert tl.runtime_ref['payload']['contract_amendment']['ui_spec_revision'] == 2
    qa2 = finish(env, runtime, scheduler, 'qa_plan')
    assert qa2.result['suite_artifact_id'] and qa2.result['ui_contract_revision'] == 2
    assert [m.meta['ui_spec_revision'] for m in spec_messages(env, ticket)] == [1, 2]
    development = env.get(scheduler.tick()[0])
    assert development.stage == 'development' and development.idempotency_key.endswith(
        ':spec-r2@' + development.idempotency_key.rsplit('@', 1)[1])


def test_inherited_tokens_are_kept_unless_the_uac_asks_for_a_visual_change(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    inherited = copy.deepcopy(SPEC['tokens'])
    inherited['colors'][2]['value'] = '#7A3E9D'
    accepted = {'tokens': inherited, 'copy': [],
                'components': [{**SPEC['screens'][0]['components'][3], 'repeated': True}]}
    runtime._inherited_ui_spec = lambda identity, log=None: accepted
    conflicting = spec()
    conflicting['screens'][0]['components'][3]['repeated'] = False  # flattens an accepted row
    env.script(conflicting, spec())
    env.approved_ticket()
    job = finish(env, runtime, scheduler, 'ui_spec')
    assert 'keep their accepted role/repeated/scope' in str(env.provider.requests[-1])
    with env.db.read() as s:
        message = s.get(Message, job.result['ui_spec_message_id'])
    assert message.meta['spec']['tokens'] == inherited  # supervisor restored the project tokens


def test_visual_uac_may_change_inherited_tokens(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    inherited = copy.deepcopy(SPEC['tokens'])
    inherited['colors'][2]['value'] = '#7A3E9D'
    runtime._inherited_ui_spec = lambda identity, log=None: {'tokens': inherited, 'copy': [], 'components': []}
    env.script(spec())
    env.approved_ticket({'title': 'Tema', 'description': 'Ganti tema', 'dependencies': [],
                         'uac': [{'id': 'UAC-1', 'text': 'Warna aksen katalog berubah menjadi biru'}]})
    job = finish(env, runtime, scheduler, 'ui_spec')
    with env.db.read() as s:
        assert s.get(Message, job.result['ui_spec_message_id']).meta['spec']['tokens'] == SPEC['tokens']


@pytest.mark.parametrize('mutation', ['remove_state', 'change_state', 'remove_feedback', 'change_feedback',
                                     'move_component', 'change_screen'])
def test_amendment_preserves_screens_states_and_feedback(agent_env, tmp_path, mutation):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    broken = amended_spec()
    if mutation == 'remove_state':
        broken['states'].pop(0)
    elif mutation == 'change_state':
        broken['states'][0]['copy_key'] = 'catalog.loading'
    elif mutation == 'remove_feedback':
        broken['feedback'].pop(0)
    elif mutation == 'change_feedback':
        broken['feedback'][0]['kind'] = 'native_alert'
    elif mutation == 'move_component':
        component = broken['screens'][0]['components'].pop(0)
        broken['screens'].append({'id': 'other', 'purpose': 'Other screen', 'components': [component]})
    else:
        broken['screens'][0]['purpose'] = 'Changed purpose'
    env.script(spec(), plan(), broken, amended_spec())
    ticket = env.approved_ticket()
    for stage in ('ui_spec', 'technical_plan', 'qa_plan', 'ui_spec'):
        finish(env, runtime, scheduler, stage)
    assert len(env.provider.requests) == 4  # invalid amendment repaired in the same job
    assert spec_messages(env, ticket)[-1].meta['spec'] == UiSpec.model_validate(amended_spec()).model_dump()


@pytest.mark.parametrize('uac', ['Daftar menampilkan tema buku yang tersimpan.',
                                'Tampilan daftar memuat judul dan penulis buku.',
                                'Warna dan font katalog tidak berubah.',
                                'Do not change the theme or font.'])
def test_display_uac_does_not_authorize_changing_the_project_theme(agent_env, tmp_path, uac):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    inherited = copy.deepcopy(SPEC['tokens'])
    inherited['colors'][2]['value'] = '#7A3E9D'
    runtime._inherited_ui_spec = lambda identity, log=None: {'tokens': inherited, 'copy': [], 'components': []}
    env.script(spec())
    ticket = env.approved_ticket({'title': 'Daftar', 'description': 'Daftar buku', 'dependencies': [],
                                 'uac': [{'id': 'UAC-1', 'text': uac}]})
    finish(env, runtime, scheduler, 'ui_spec')
    assert spec_messages(env, ticket)[0].meta['spec']['tokens'] == inherited


class DeveloperAmendmentDriver:
    """Developer stand-in: writes a file, asks UI/UX for an amendment, then resumes from its checkpoint."""

    def __init__(self):
        self.developer_runs, self.resumed_content = 0, None

    def run(self, ctx, identity, snapshot, tools, parameters):
        if identity['role'] == 'qa':
            tools['propose_tests']({'plan': {'kind': 'qa_plan', 'summary': 'Add a book', 'tests': [
                {'id': 'add', 'uac': ['UAC-1'], 'purpose': 'feature', 'steps': [
                    {'action': 'click', 'selector': 'testid=add-book'},
                    {'action': 'assert_visible', 'selector': 'testid=add-book'}]}]}})
            return {}
        self.developer_runs += 1
        if self.developer_runs == 1:
            tools['patch_file']({'path': 'catalog.js', 'content': 'export const draft = 1;\n'})
            response = tools['request_spec_amendment']({'reason': 'The empty catalogue needs an error state copy.'})
            assert 'Stop now' in response['next']
            assert tools['submit_candidate']({'message': 'x'})['failure_kind'] == 'spec_amendment_pending'
        else:
            self.resumed_content = tools['read_file']({'path': 'catalog.js'})['content']
        return {}


def test_developer_spec_amendment_resumes_development_from_its_checkpoint(agent_env, tmp_path):
    env = agent_env
    driver = DeveloperAmendmentDriver()
    runtime, _, scheduler = setup(env, tmp_path, driver)
    enable_ui_ux(env)
    env.script(spec(), plan(), amended_spec(), plan(2))
    env.approved_ticket()
    finish(env, runtime, scheduler, 'ui_spec')
    finish(env, runtime, scheduler, 'technical_plan')
    finish(env, runtime, scheduler, 'qa_plan')
    dev = finish(env, runtime, scheduler, 'development')
    request = dev.result['spec_amendment']
    assert request['requested_by'] == 'developer' and request['checkpoint_artifact_id']
    finish(env, runtime, scheduler, 'ui_spec')
    finish(env, runtime, scheduler, 'technical_plan')
    finish(env, runtime, scheduler, 'qa_plan')
    resumed = env.get(scheduler.tick()[0])
    assert resumed.stage == 'development'
    assert resumed.runtime_ref['payload']['resume_checkpoint'] == request['checkpoint_artifact_id']
    ctx = env.ctx(resumed)
    try:
        runtime.run(ctx)
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    assert driver.resumed_content == 'export const draft = 1;\n'


class ParallelAmendmentDriver(DeveloperAmendmentDriver):
    def __init__(self, env, *, during_wait=False):
        super().__init__()
        self.env = env
        self.qa_job = None
        self.qa_requested = False
        self.during_wait = during_wait

    def run(self, ctx, identity, snapshot, tools, parameters):
        if identity['role'] == 'qa' and self.developer_runs == 1 and not self.qa_requested:
            self.qa_requested = True
            tools['request_spec_amendment']({'reason': 'The catalogue needs a loading state and its copy.'})
            return {}
        if identity['role'] == 'developer' and self.developer_runs == 0:
            self.developer_runs += 1
            tools['patch_file']({'path': 'catalog.js', 'content': 'export const draft = 1;\n'})
            qa_ctx = self.env.ctx(self.qa_job)
            try:
                result = self.runtime.run(qa_ctx)
                self.env.queue.complete(qa_ctx.lease, result.result)
            finally:
                qa_ctx.stop_resources()
                self.env.queue.finish_cleanup(qa_ctx.lease.job_id, qa_ctx.lease.generation)
            if self.during_wait:
                # The same path is used when QA asks while submit/run_checks waits for a suite.
                self.runtime._await_qa_plan(ctx, identity, timeout_s=0)
            else:
                tools['submit_candidate']({'message': 'Preserve the draft while QA requests new copy'})
            pytest.fail('Developer must hand off to UI/UX before submitting a candidate')
        return super().run(ctx, identity, snapshot, tools, parameters)


@pytest.mark.parametrize('during_wait', [False, True])
def test_parallel_qa_amendment_checkpoints_and_pauses_the_running_developer(agent_env, tmp_path, during_wait):
    env = agent_env
    driver = ParallelAmendmentDriver(env, during_wait=during_wait)
    runtime, _, scheduler = setup(env, tmp_path, driver)
    driver.runtime = runtime
    enable_ui_ux(env)
    env.script(spec(), plan(), amended_spec(), plan(2))
    env.approved_ticket()
    finish(env, runtime, scheduler, 'ui_spec')
    finish(env, runtime, scheduler, 'technical_plan')
    driver.qa_job = env.get(scheduler.tick()[0])
    dev = finish(env, runtime, scheduler, 'development')
    assert dev.result['spec_amendment']['checkpoint_artifact_id']
    for stage in ('ui_spec', 'technical_plan', 'qa_plan'):
        finish(env, runtime, scheduler, stage)
    resumed = env.get(scheduler.tick()[0])
    assert resumed.stage == 'development'
    ctx = env.ctx(resumed)
    try:
        runtime.run(ctx)
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
    assert driver.resumed_content == 'export const draft = 1;\n'


def test_amendment_checkpoint_is_not_reused_in_a_later_repair_cycle(agent_env, tmp_path):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, DeveloperAmendmentDriver())
    enable_ui_ux(env)
    env.script(spec(), plan(), amended_spec(), plan(2))
    ticket = env.approved_ticket()
    for stage in ('ui_spec', 'technical_plan', 'qa_plan', 'development', 'ui_spec', 'technical_plan', 'qa_plan'):
        finish(env, runtime, scheduler, stage)
    resumed = env.get(scheduler.tick()[0])
    assert resumed.runtime_ref['payload']['resume_checkpoint']
    lease = env.queue.claim('review-fixture', resumed.lane, capacity=1, runtimes=(runtime.name,))
    env.queue.complete(lease, {'candidate_id': 'submitted-after-amendment'})
    with env.db.write() as s:
        row = s.get(Ticket, ticket.id)
        row.workflow = {**row.workflow, 'repair_cycles': 1}
    repair = env.get(scheduler.tick()[0])
    assert 'resume_checkpoint' not in repair.runtime_ref['payload']


def test_qa_cannot_submit_a_suite_after_requesting_a_spec_amendment(agent_env, tmp_path):
    env = agent_env
    class Driver(SpecDriver):
        def run(self, ctx, identity, snapshot, tools, parameters):
            super().run(ctx, identity, snapshot, tools, parameters)
            with pytest.raises(ValueError, match='UI spec amendment was already requested'):
                tools['propose_tests']({'plan': {'kind': 'qa_plan', 'summary': 'Too late', 'tests': []}})
    runtime, _, scheduler = setup(env, tmp_path, Driver())
    enable_ui_ux(env)
    env.script(spec(), plan())
    env.approved_ticket()
    finish(env, runtime, scheduler, 'ui_spec')
    finish(env, runtime, scheduler, 'technical_plan')
    job = finish(env, runtime, scheduler, 'qa_plan')
    assert 'spec_amendment' in job.result and 'suite_artifact_id' not in job.result


def test_qa_only_amendments_keep_the_last_unsubmitted_developer_checkpoint(agent_env, tmp_path):
    env = agent_env
    class Driver(DeveloperAmendmentDriver):
        def __init__(self):
            super().__init__()
            self.qa_runs = 0
        def run(self, ctx, identity, snapshot, tools, parameters):
            if identity['role'] == 'qa':
                self.qa_runs += 1
                if self.qa_runs == 2:
                    tools['request_spec_amendment']({'reason': 'The amended catalogue also needs a network error copy.'})
                    return {}
            return super().run(ctx, identity, snapshot, tools, parameters)
    runtime, _, scheduler = setup(env, tmp_path, Driver())
    enable_ui_ux(env)
    third = amended_spec()
    third['revision'] = 3
    third['copy'].append({'key': 'catalog.network_error', 'text': 'Koneksi gagal. Coba lagi.'})
    env.script(spec(), plan(), amended_spec(), plan(2), third, plan(3))
    env.approved_ticket()
    for stage in ('ui_spec', 'technical_plan', 'qa_plan'):
        finish(env, runtime, scheduler, stage)
    dev = finish(env, runtime, scheduler, 'development')
    for stage in ('ui_spec', 'technical_plan', 'qa_plan', 'ui_spec', 'technical_plan', 'qa_plan'):
        finish(env, runtime, scheduler, stage)
    resumed = env.get(scheduler.tick()[0])
    assert resumed.stage == 'development' and ':spec-r3' in resumed.idempotency_key
    assert resumed.runtime_ref['payload']['resume_checkpoint'] == dev.result['spec_amendment']['checkpoint_artifact_id']


@pytest.mark.parametrize('no_change', [False, True])
def test_ui_spec_retry_reuses_the_persisted_message_without_a_model_call(agent_env, tmp_path, no_change):
    env = agent_env
    runtime, _, scheduler = setup(env, tmp_path, SpecDriver())
    enable_ui_ux(env)
    env.script({'kind': 'no_ui_change', 'reason': 'Hanya penyimpanan data.'} if no_change else spec())
    ticket = env.approved_ticket()
    job = env.get(scheduler.tick()[0])
    ctx = env.ctx(job)
    try:
        first = runtime.run(ctx)
        count = len(env.provider.requests)
        second = runtime.run(ctx)
        assert second.result['ui_spec_message_id'] == first.result['ui_spec_message_id']
        assert len(env.provider.requests) == count == 1
        assert len(spec_messages(env, ticket)) == 1
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)


def test_inherited_validation_keeps_identities_beyond_the_context_excerpt(agent_env, tmp_path):
    """Synthetic domain acceptance receipts; no real QA/provider pass is claimed."""
    from app.pipeline.runtime import PipelineRuntime
    from app.pipeline.workspace import ProductWorkspace
    env = agent_env
    workspace = ProductWorkspace(env.db, env.store, env.world.w, tmp_path, None, env.redactor)
    runtime = PipelineRuntime(env.runtime, workspace, SpecDriver())
    accepted_specs = []
    for index in range(3):
        document = spec()
        prefix = f'v{index}-'
        for component in document['screens'][0]['components']:
            component['testid'] = prefix + component['testid']
            if component.get('scope_testid'):
                component['scope_testid'] = prefix + component['scope_testid']
        for feedback in document['feedback']:
            feedback['action_testid'] = prefix + feedback['action_testid']
            if feedback.get('target_testid'):
                feedback['target_testid'] = prefix + feedback['target_testid']
        extra = [{'testid': prefix + f'extra-{n}', 'role': 'status', 'purpose': 'accepted status control'}
                 for n in range(89)]
        document['screens'][0]['components'].extend(extra[:57])
        document['screens'].append({'id': 'extra', 'purpose': 'More statuses', 'components': extra[57:]})
        accepted_specs.append(document)
        ticket, _ = env.world.accepted()
        with env.db.write() as s:
            workspace._post(s, {'project_id': env.project.id, 'ticket_id': ticket.id,
                                'scope_version': ticket.current_version, 'role': 'ui-ux',
                                'job_id': f'spec-fixture-{index}', 'generation': 1, 'fake': True},
                            f'accepted-spec-{index}', 'Contract fixture accepted spec', [], 'ui_spec', spec=document)
    current = env.approved_ticket()
    inherited = runtime._inherited_ui_spec({'ticket_id': current.id})
    assert len(inherited['components']) == 288
    # The first ticket's control is omitted from the newest 200-control prompt excerpt.
    broken = copy.deepcopy(accepted_specs[0])
    broken['screens'][0]['components'][0]['role'] = 'status'
    env.script(broken, accepted_specs[0])
    runtime.workspace.configuration = lambda identity: (None, 'a' * 40)
    runtime._baseline_ui_source = lambda *a, **kw: ([], {}, [])
    ctx = env.ctx(env.job('ui-ux', 'ui_spec', ticket=current, stage='ui_spec', runtime=runtime.name))
    try:
        outcome = runtime.run(ctx)
        assert outcome.status == 'succeeded', outcome.error
        assert len(env.provider.requests) == 2
        assert '"role": "heading"' in env.provider.requests[-1].user
        assert spec_messages(env, current)[-1].meta['spec']['screens'][0]['components'][0]['role'] == 'heading'
    finally:
        ctx.stop_resources()
        env.queue.finish_cleanup(ctx.lease.job_id, ctx.lease.generation)
