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
    # 96 TL controls, one scoped to the inherited row: the restored scope parent makes 97.
    tl = UiContract.model_construct(revision=1, controls=[
        *(UiContract.model_validate({'controls': [{'testid': f'c{i}', 'role': 'button', 'name': f'C{i}',
                                                   'purpose': 'x'}]}).controls[0] for i in range(95)),
        *contract(ROW, {'testid': 'extra', 'role': 'button', 'name': 'Extra', 'purpose': 'x',
                        'scope_testid': 'book-item'}).controls])
    with pytest.raises(InvalidOutput, match='merged'):
        merge_inherited_contract(tl, inherited)


def test_only_referenced_inherited_controls_and_their_scopes_are_kept():
    # A large accepted app no longer exceeds the 96-control contract cap per ticket.
    inherited = [*(c.model_dump() for c in contract(ROW, BORROW).controls),
                 *({'testid': f'old{i}', 'role': 'button', 'name': f'Old {i}', 'purpose': 'x'} for i in range(150))]
    tl = contract({'testid': 'borrow-name-input', 'label': 'Nama peminjam', 'purpose': 'borrower'},
                  {'testid': 'old3', 'role': 'button', 'name': 'Old 3', 'purpose': 'x'})
    merged, rejected = merge_inherited_contract(tl, inherited)
    assert [c.testid for c in merged.controls] == ['book-item', 'borrow-name-input', 'old3']
    assert merged.controls[1].scope_testid == 'book-item' and rejected == ['borrow-name-input']


def test_inherited_wording_changes_only_when_this_ticket_uac_requires_it():
    button = {'testid': 'borrow', 'role': 'button', 'name': 'Pinjam', 'purpose': 'borrow', 'scope_testid': 'book-item'}
    inherited = [c.model_dump() for c in contract(ROW, button).controls]
    renamed = contract(ROW, {**button, 'name': 'Pinjamkan'})
    texts = ['Tombol Pinjamkan mencatat peminjam pada baris buku']
    merged, rejected = merge_inherited_contract(renamed, inherited, texts)
    assert merged.controls[1].name == 'Pinjamkan' and rejected == []
    merged, rejected = merge_inherited_contract(renamed, inherited, ['Status buku tersedia'])
    assert merged.controls[1].name == 'Pinjam' and rejected == ['borrow']
    # Structure never changes through wording: flattening is still refused.
    merged, rejected = merge_inherited_contract(contract(ROW, {**button, 'name': 'Pinjamkan', 'scope_testid': None}),
                                                inherited, texts)
    assert merged.controls[1].scope_testid == 'book-item' and merged.controls[1].name == 'Pinjam'


SMOKE = {'id': 'smoke-open', 'uac': [], 'purpose': 'smoke', 'steps': [
    {'action': 'navigate', 'value': '/'}, {'action': 'assert_visible', 'selector': 'testid=app'}]}
MANUAL = [{**c, 'mode': 'manual'} for c in CRITERIA]


def test_manual_profile_smoke_only_suite_is_valid():
    QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'smoke', 'tests': [SMOKE]}).check_uac_quotes(MANUAL)
    with_feature = QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [
        SMOKE, plan(STATUS).tests[0].model_dump()]})
    with_feature.check_uac_quotes(CRITERIA)


@pytest.mark.parametrize('step', [
    {'action': 'assert_enabled', 'selector': 'testid=borrow'},
    {'action': 'assert_visible', 'selector': 'testid=app', 'uac_id': 'UAC-4', 'uac_quote': 'status buku kembali'}])
def test_smoke_cannot_carry_requirements(step):
    smoke = {**SMOKE, 'steps': [SMOKE['steps'][0], step]}
    with pytest.raises(ValueError):
        QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [smoke]}).check_uac_quotes(MANUAL)


STATUS_CONTROL = {'testid': 'book-status', 'role': 'status', 'purpose': 'availability', 'scope_testid': 'book-item'}


def rows_suite(*steps):
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [{'id': 'borrow', 'uac': ['UAC-4'],
        'steps': [{'action': 'fill', 'selector': 'testid=title', 'value': 'Laskar Pelangi'}, *steps,
                  {'action': 'assert_visible', 'selector': 'testid=title'}]}]})


@pytest.mark.parametrize('step', [
    {'action': 'click', 'selector': 'testid=book-item'},                     # action on the bare row
    {'action': 'assert_text', 'selector': 'testid=book-status', 'value': 'Tersedia'},   # unscoped child
    {'action': 'assert_text', 'selector': 'testid=book-item >> testid=book-status', 'value': 'Tersedia'}])
def test_strict_rows_reject_every_ambiguous_row_locator(step):
    rows = UiContract.model_validate({'action_locators': 'testid', 'controls': [
        {'testid': 'title', 'label': 'Judul', 'purpose': 'title'}, ROW, STATUS_CONTROL]})
    with pytest.raises(ValueError, match='repeated row'):
        rows.check_suite(rows_suite(step), strict_rows=True)
    rows.check_suite(rows_suite({'action': 'assert_visible', 'selector': 'testid=title'}), strict_rows=False)


def test_strict_rows_accept_one_selected_row_and_counts():
    rows = UiContract.model_validate({'action_locators': 'testid', 'controls': [
        {'testid': 'title', 'label': 'Judul', 'purpose': 'title'}, ROW, STATUS_CONTROL]})
    rows.check_suite(rows_suite(
        {'action': 'assert_text', 'selector': 'testid=book-item >> has_text="Laskar Pelangi" >> testid=book-status',
         'value': 'Tersedia'},
        {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 1},
        {'action': 'assert_visible', 'selector': 'testid=book-item >> nth=0'}), strict_rows=True)


ADD_FORM = {'testid': 'add-book-form', 'role': 'form', 'purpose': 'add a book'}
ADD_TITLE = {'testid': 'add-title', 'label': 'Judul', 'purpose': 'title', 'scope_testid': 'add-book-form'}


def test_amendment_restores_inherited_structure_and_keeps_its_own_controls_verbatim():
    previous = contract(ROW, {**BORROW, 'label': 'Nama peminjam'})
    # Since planning, a later acceptance relabelled the borrower input.
    inherited = [c.model_dump() for c in contract(ROW, {**BORROW, 'label': 'Nama anggota'}, ADD_FORM, ADD_TITLE).controls]
    # QA asked for setup controls; TL adds them flat (the #3 failure class).
    amended = UiContract.model_validate({'revision': 2, 'controls': [
        *(c.model_dump() for c in previous.controls), {**ADD_TITLE, 'scope_testid': None}]})
    merged, _ = merge_inherited_contract(amended, inherited, pinned=[c.model_dump() for c in previous.controls])
    by_id = {c.testid: c for c in merged.controls}
    assert by_id['add-title'].scope_testid == 'add-book-form' and 'add-book-form' in by_id
    assert by_id['borrow-name-input'].label == 'Nama peminjam'  # pinned contract stays additive
    assert merged.revision == 2


def setup_suite():
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [{'id': 'borrow', 'uac': ['UAC-4'],
        'steps': [{'action': 'fill', 'selector': 'testid=add-book-form >> testid=add-title', 'value': 'Laskar'},
                  {'action': 'click', 'selector': 'testid=book-item >> has_text="Laskar" >> testid=borrow-name-input'},
                  {'action': 'assert_visible', 'selector': 'testid=book-item >> has_text="Laskar" >> testid=borrow-name-input'}]}]})


def test_qa_vocabulary_includes_pinned_accepted_setup_controls():
    from app.agents.ui_contract import with_vocabulary, used_accepted_controls
    ticket = UiContract.model_validate({'action_locators': 'testid', 'controls': [ROW, BORROW]})
    accepted = [c.model_dump() for c in contract(ADD_FORM, ADD_TITLE).controls] + [
        {'testid': f'old{i}', 'role': 'button', 'name': f'Old {i}', 'purpose': 'x'} for i in range(120)]
    with pytest.raises(ValueError, match='outside ui_contract'):
        ticket.check_suite(setup_suite(), strict_rows=True)  # previously: an amendment round trip
    vocabulary = with_vocabulary(ticket, accepted)  # no 96-control cap on vocabulary
    vocabulary.check_suite(setup_suite(), strict_rows=True)
    assert 'action_locators' in vocabulary.model_fields_set and len(vocabulary.controls) == 124
    pinned = used_accepted_controls(setup_suite(), ticket, accepted)
    assert [c['testid'] for c in pinned] == ['add-book-form', 'add-title']
    with_vocabulary(ticket, pinned).check_suite(setup_suite(), strict_rows=True)
    assert with_vocabulary(ticket, []) is ticket
