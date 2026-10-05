import copy
import pytest
from pydantic import ValidationError
from app.pipeline.contracts import QaPlan, Step, KEYS, validate_report, tool_schema

PLAN = {'kind': 'qa_plan', 'summary': 'Test total', 'tests': [{'id': 'total', 'uac': ['UAC-1'],
    'purpose': 'feature', 'steps': [{'action': 'click', 'selector': '#add'},
        {'action': 'assert_text', 'selector': '#total', 'value': '4'}]}]}


def suite():
    return QaPlan.model_validate(PLAN)


def report():
    return {'schema': 1, 'invocation_id': 'invocation', 'target_digest': 'a' * 64, 'suite_digest': suite().digest,
        'tests': [{'id': 'total', 'uac': ['UAC-1'], 'status': 'passed'}],
        'discovered': 1, 'executed': 1, 'passed': 1, 'failed': 0, 'skipped': 0}


def validate(data):
    return validate_report(data, invocation_id='invocation', target_digest='a' * 64, suite=suite())


@pytest.mark.parametrize('field,value', [('invocation_id', 'forged'), ('target_digest', 'b' * 64),
    ('suite_digest', 'c' * 64), ('discovered', 0), ('executed', 0), ('passed', 0), ('failed', 1), ('skipped', 1),
    ('tests', []), ('tests', [{'id': 'other', 'uac': ['UAC-1'], 'status': 'passed'}]),
    ('tests', [{'id': 'total', 'uac': [], 'status': 'passed'}]),
    ('tests', [{'id': 'total', 'uac': ['UAC-1'], 'status': 'skipped'}])])
def test_missing_skipped_wrong_identity_and_empty_execution_are_incomplete(field, value):
    data = report()
    data[field] = value
    assert validate(data)['status'] == 'incomplete'


def test_report_counts_are_derived_not_the_models_status():
    data = report()
    data['status'] = 'passed'
    data['tests'][0]['status'] = 'failed'
    data.update(passed=0, failed=1)
    assert validate(data)['status'] == 'failed'
    assert validate(report())['coverage'] == {'UAC-1': ['total']}


def test_coverage_is_required_and_unknown_criteria_are_refused():
    with pytest.raises(ValueError):
        suite().check_criteria([{'id': 'UAC-2', 'mode': 'automated'}])
    suite().check_criteria([{'id': 'UAC-1'}, {'id': 'UAC-manual', 'mode': 'manual'}])


def test_qa_cannot_propose_empty_tests_or_just_clicks():
    data = copy.deepcopy(PLAN)
    data['tests'][0]['steps'] = [{'action': 'click', 'selector': '#add'}]
    with pytest.raises(ValidationError):
        QaPlan.model_validate(data)
    data['tests'] = []
    with pytest.raises(ValidationError):
        QaPlan.model_validate(data)


def test_qa_tool_parameter_has_actual_properties_and_no_misplaced_local_references():
    schema = tool_schema(QaPlan)
    assert {'kind', 'summary', 'tests'} <= set(schema['properties'])
    test = schema['properties']['tests']['items']
    assert 'steps' in test['properties']
    assert 'action' in test['properties']['steps']['items']['properties']
    import json
    assert '$ref' not in json.dumps(schema) and '$defs' not in json.dumps(schema)


@pytest.mark.parametrize('key', KEYS)
def test_keyboard_steps_accept_only_supported_named_keys(key):
    assert Step(action='press', selector='#input', value=key).value == key


@pytest.mark.parametrize('key', [None, 1, '\n', '\\n', 'Control+Enter', 'javascript:alert(1)'])
def test_keyboard_steps_refuse_missing_or_arbitrary_values(key):
    with pytest.raises(ValidationError, match='supported named key'):
        Step(action='press', selector='#input', value=key)


def test_reload_step_has_no_selector_or_value():
    assert Step(action='reload').selector is None


@pytest.mark.parametrize('arguments', [{'selector': '#item'}, {'value': 'Enter'}])
def test_reload_refuses_locator_or_value(arguments):
    with pytest.raises(ValidationError, match='reload takes no'):
        Step(action='reload', **arguments)


@pytest.mark.parametrize('action', ['click', 'fill', 'press', 'assert_visible'])
def test_element_steps_still_require_a_selector(action):
    with pytest.raises(ValidationError, match='requires a selector'):
        Step(action=action)
