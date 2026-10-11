"""Library-demo QA follow-ups: cited assertions, bounded revision, inherited UI contracts.

Pure validators and real SQLite; no model, browser or provider. Not QA/UAT evidence.
"""
import json

import pytest

from app.agents.outputs import InvalidOutput
from app.agents.ui_contract import UiContract
from app.pipeline.contracts import QaPlan
from app.pipeline.qa_repair import suite_repair_allowed
from app.pipeline.runtime import PipelineRuntime, merge_inherited_contract
from tests.persistence import factories as f
from tests.persistence.conftest import db, db_path, store  # noqa: F401

CRITERIA = [{'id': 'UAC-4', 'text': 'Setelah dikembalikan, status buku kembali tersedia'},
            {'id': 'UAC-5', 'text': 'Daftar peminjaman dapat diunduh', 'mode': 'manual'}]


def plan(*asserts, uac=('UAC-4',)):
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'Return a borrowed book', 'tests': [
        {'id': 'return-book', 'uac': list(uac), 'purpose': 'feature', 'steps': [
            {'action': 'click', 'selector': 'testid=return'}, *asserts]}]})


STATUS = {'action': 'assert_text', 'selector': 'testid=book-status', 'value': 'Tersedia',
          'uac_id': 'UAC-4', 'uac_quote': 'status buku kembali tersedia'}


def test_cited_assertion_passes_and_legacy_digest_is_unchanged():
    plan(STATUS).check_uac_quotes(CRITERIA)
    legacy = plan({'action': 'assert_text', 'selector': 'testid=book-status', 'value': 'Tersedia'})
    assert 'uac_id' not in json.dumps(legacy.model_dump()) and not legacy.uac_quoted


@pytest.mark.parametrize('extra, match', [
    # Library demo #2: an assertion no criterion asked for cannot cite one.
    ({'action': 'assert_enabled', 'selector': 'testid=borrow'}, 'needs uac_id'),
    ({'action': 'assert_enabled', 'selector': 'testid=borrow', 'uac_id': 'UAC-4', 'uac_quote': 'tombol Pinjam aktif'},
     'exact substring'),
    ({'action': 'assert_enabled', 'selector': 'testid=borrow', 'uac_id': 'UAC-4', 'uac_quote': 'buku'},
     'at least 8'),
    ({'action': 'assert_enabled', 'selector': 'testid=borrow', 'uac_id': 'UAC-9', 'uac_quote': 'status buku kembali'},
     'not one of'),
])
def test_assertion_outside_the_criterion_is_rejected(extra, match):
    with pytest.raises(ValueError, match=match):
        plan(STATUS, extra).check_uac_quotes(CRITERIA)


def test_every_automated_criterion_is_cited_and_quotes_belong_to_assertions():
    with pytest.raises(ValueError, match='UAC-4'):
        plan({**STATUS, 'uac_id': 'UAC-5', 'uac_quote': 'Daftar peminjaman'}, uac=('UAC-4', 'UAC-5')).check_uac_quotes(CRITERIA)
    with pytest.raises(ValueError, match='only valid on assert_'):
        QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [{'id': 't', 'uac': ['UAC-4'], 'steps': [
            {'action': 'click', 'selector': 'testid=x', 'uac_id': 'UAC-4', 'uac_quote': 'status buku kembali'},
            STATUS]}]})


def test_one_automatic_revision_then_user_authorized_retries_up_to_the_cap():
    assert suite_repair_allowed(0, user_authorized=False)
    assert not suite_repair_allowed(1, user_authorized=False)
    assert suite_repair_allowed(1, user_authorized=True) and suite_repair_allowed(2, user_authorized=True)
    assert not suite_repair_allowed(3, user_authorized=True)
    assert not suite_repair_allowed('1', user_authorized=True)


ROW = {'testid': 'book-item', 'role': 'listitem', 'purpose': 'one book row', 'dynamic_text': True}
BORROW = {'testid': 'borrow-name-input', 'label': 'Nama peminjam', 'purpose': 'borrower', 'scope_testid': 'book-item'}


def contract(*controls):
    return UiContract.model_validate({'revision': 1, 'controls': list(controls)})


def test_supervisor_keeps_inherited_scoped_controls_and_adds_tl_controls():
    inherited = [c.model_dump() for c in contract(ROW, BORROW).controls]
    # TL "flattens" the row child and adds a new control.
    flat = contract({**BORROW, 'scope_testid': None}, {'testid': 'return', 'role': 'button', 'name': 'Kembalikan',
                                                        'purpose': 'return a book', 'scope_testid': 'book-item'},
                    ROW)
    merged, replaced = merge_inherited_contract(flat, inherited)
    by_id = {c.testid: c for c in merged.controls}
    assert replaced == ['borrow-name-input']
    assert by_id['borrow-name-input'].scope_testid == 'book-item' and by_id['book-item'].dynamic_text
    assert by_id['return'].scope_testid == 'book-item'


def test_merge_that_breaks_the_contract_goes_back_to_tl():
    inherited = [c.model_dump() for c in contract(ROW).controls]
    many = contract(*({'testid': f'c{i}', 'role': 'button', 'name': f'C{i}', 'purpose': 'x'} for i in range(96)))
    with pytest.raises(InvalidOutput, match='merged'):
        merge_inherited_contract(many, inherited)


def test_inherited_controls_come_from_accepted_dependency_targets(db, store):
    with db.write() as s:
        p = f.project(s)
        upstream, ticket = f.ticket(s, p), f.ticket(s, p, number=2)
        target = store.put_json(s, project_id=p.id, kind='target_manifest', name='target.json',
            document={'ui_contract': contract(ROW, BORROW).model_dump()})
        cand = f.candidate(s, p, upstream, target_artifact_id=target.id, target_digest=target.checksum)
        from app.persistence.models import Dependency
        s.add(Dependency(ticket_id=ticket.id, depends_on_ticket_id=upstream.id, state='satisfied',
                         accepted_scope_version=upstream.current_version, accepted_candidate_id=cand.id,
                         integration_sha='c' * 40))
    rt = object.__new__(PipelineRuntime)
    rt.db, rt.store = db, store
    controls = rt._inherited_ui_controls({'ticket_id': ticket.id})
    assert [c['testid'] for c in controls] == ['book-item', 'borrow-name-input']
    assert controls[1]['scope_testid'] == 'book-item'
    assert rt._inherited_ui_controls({'ticket_id': upstream.id}) == []
