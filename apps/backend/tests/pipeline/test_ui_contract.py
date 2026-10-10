import pytest
from app.agents.ui_contract import UiContract
from app.pipeline.contracts import QaPlan


def contract(**kwargs):
    return UiContract.model_validate({'controls': [
        {'testid': 'list', 'role': 'list', 'purpose': 'items', 'dynamic_text': True},
        {'testid': 'delete', 'role': 'button', 'name': 'Delete', 'purpose': 'delete', 'scope_testid': 'list'},
        {'testid': 'select', 'role': 'combobox', 'purpose': 'select'}], **kwargs})


def suite(selector, *, select_value='Seed', action='click'):
    return QaPlan.model_validate({'kind': 'qa_plan', 'summary': 'fixture', 'tests': [
        {'id': 'case', 'uac': ['UAC-1'], 'steps': [
            {'action': 'select_option', 'selector': 'testid=select', 'value': select_value},
            {'action': action, 'selector': selector},
            {'action': 'assert_visible', 'selector': 'testid=list'}]}]})


def test_historical_shape_and_action_vocabulary_remain_compatible():
    c = contract()
    assert 'action_locators' not in c.model_dump()
    c.check_suite(suite('testid=list >> role=button[name="Delete"]'))


@pytest.mark.parametrize('selector', ['role=combobox', 'testid=list >> role=button[name="Delete"]'])
def test_new_action_policy_rejects_semantic_control(selector):
    with pytest.raises(ValueError, match='actions require'):
        contract(action_locators='testid').check_suite(suite(selector))


@pytest.mark.parametrize('value', ['Seed', ['Seed', 'Other']])
def test_dynamic_select_inputs_and_scoped_testid_action(value):
    contract(action_locators='testid', revision=2).check_suite(
        suite('testid=list >> has_text="Seed" >> testid=delete', select_value=value))


def test_new_policy_allows_semantic_assertions():
    contract(action_locators='testid').check_suite(
        suite('testid=list >> role=button[name="Delete"]', action='assert_visible'))


def test_unknown_input_is_not_dynamic_vocabulary():
    with pytest.raises(ValueError, match='outside ui_contract'):
        contract(action_locators='testid').check_suite(suite('testid=list >> has_text="Generated" >> testid=delete'))
