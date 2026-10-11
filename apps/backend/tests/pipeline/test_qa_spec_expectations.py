"""QA expected values come only from UI spec copy or original input (DEV-022). Pure validators."""
import pytest

from app.agents.ui_spec import UiSpec, check_expected_values
from app.pipeline.contracts import QaPlan
from tests.agents.test_ui_spec import SPEC

SPEC_MODEL = UiSpec.model_validate(SPEC)


def suite(*steps):
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 's', 'tests': [{'id': 'add', 'uac': ['UAC-1'],
        'steps': [{'action': 'fill', 'selector': 'testid=title-input', 'value': 'Laskar Pelangi'},
                  {'action': 'click', 'selector': 'testid=add-book'}, *steps]}]})


@pytest.mark.parametrize('step', [
    {'action': 'assert_text', 'selector': 'testid=catalog-title', 'value': 'Katalog Buku'},
    {'action': 'assert_text', 'selector': 'testid=catalog-title', 'value': '  katalog   BUKU '},  # case/space folded
    {'action': 'assert_contains_text', 'selector': 'testid=form-error',
     'value': 'Buku "Laskar Pelangi" ditambahkan'},  # template filled with this test's own input
    {'action': 'assert_contains_text', 'selector': 'testid=book-item', 'value': 'Laskar Pelangi'},  # original input
    {'action': 'assert_value', 'selector': 'testid=title-input', 'value': 'Laskar Pelangi'},
    {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 1},  # not a text expectation
])
def test_spec_copy_and_original_input_are_valid_expectations(step):
    check_expected_values(suite(step), SPEC_MODEL)


@pytest.mark.parametrize('value', [
    'Buku berhasil ditambahkan',             # paraphrase of the spec copy
    'Status buku kembali tersedia',          # UAC wording, not spec copy
    'Buku "Bumi Manusia" ditambahkan',       # placeholder filled with input from another test
    'Katalog',                               # fragment of a copy text
])
def test_paraphrased_or_guessed_text_is_rejected(value):
    with pytest.raises(ValueError, match='neither UI spec copy'):
        check_expected_values(suite({'action': 'assert_text', 'selector': 'testid=form-error', 'value': value}),
                              SPEC_MODEL)


def dialog(selector, message):
    return {'action': 'click_dialog', 'selector': selector,
            'dialog': {'type': 'confirm', 'message': message, 'accept': True}}


def test_click_dialog_only_for_native_feedback_with_its_copy():
    scoped = 'testid=book-item >> has_text="Laskar Pelangi" >> testid=delete-book'
    check_expected_values(suite(dialog(scoped, 'hapus buku ini dari katalog?'),
                                {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}), SPEC_MODEL)
    with pytest.raises(ValueError, match='only for actions whose UI spec feedback is native'):
        check_expected_values(suite(dialog('testid=add-book', 'Hapus buku ini dari katalog?'),
                                    {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}),
                              SPEC_MODEL)
    with pytest.raises(ValueError, match='spec feedback copy'):
        check_expected_values(suite(dialog(scoped, 'Yakin hapus?'),
                                    {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}),
                              SPEC_MODEL)


@pytest.mark.parametrize('kind', ['alert', 'prompt'])
def test_dialog_type_must_match_native_feedback(kind):
    step = dialog('testid=delete-book', 'Hapus buku ini dari katalog?')
    step['dialog']['type'] = kind
    with pytest.raises(ValueError, match='dialog type'):
        check_expected_values(suite(step, {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}),
                              SPEC_MODEL)


@pytest.mark.parametrize('action,value', [('assert_value', 'laskar pelangi'),
                                        ('assert_values', ['Laskar  Pelangi'])])
def test_input_values_preserve_case_and_whitespace_like_the_runner(action, value):
    with pytest.raises(ValueError, match='neither UI spec copy'):
        check_expected_values(suite({'action': action, 'selector': 'testid=title-input', 'value': value}), SPEC_MODEL)


def test_empty_original_input_is_a_valid_value_expectation():
    plan = suite({'action': 'assert_value', 'selector': 'testid=title-input', 'value': ''})
    plan.tests[0].steps[0].value = ''
    check_expected_values(plan, SPEC_MODEL)


def test_repeated_placeholder_uses_the_same_original_input_everywhere():
    document = SPEC_MODEL.model_dump()
    document['copy'].append({'key': 'book.echo', 'text': '{title} lalu {title}'})
    model = UiSpec.model_validate(document)
    plan = suite({'action': 'fill', 'selector': 'testid=title-input', 'value': 'Bumi Manusia'},
                 {'action': 'assert_text', 'selector': 'testid=form-error',
                  'value': 'Laskar Pelangi lalu Bumi Manusia'})
    with pytest.raises(ValueError, match='neither UI spec copy'):
        check_expected_values(plan, model)
    plan.tests[0].steps[-1].value = 'Laskar Pelangi lalu Laskar Pelangi'
    check_expected_values(plan, model)


def test_native_feedback_supports_the_contract_css_testid_locator():
    check_expected_values(suite(dialog('[data-testid="delete-book"] >> nth=0',
                                      'Hapus buku ini dari katalog?'),
                                {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}), SPEC_MODEL)


def test_native_template_must_be_filled_with_original_input():
    document = SPEC_MODEL.model_dump()
    next(entry for entry in document['copy'] if entry['key'] == 'book.delete_confirm')['text'] = 'Hapus {title}?'
    model = UiSpec.model_validate(document)
    assertion = {'action': 'assert_count', 'selector': 'testid=book-item', 'value': 0}
    check_expected_values(suite(dialog('testid=delete-book', 'Hapus Laskar Pelangi?'), assertion), model)
    with pytest.raises(ValueError, match='spec feedback copy'):
        check_expected_values(suite(dialog('testid=delete-book', 'Hapus {title}?'), assertion), model)
