"""Conservative contract repair from the isolated runner's observed DOM facts."""
import re
import json

from .contracts import QaPlan

MAX_SUITE_REPAIRS = 2


def repair_unsupported_text_selectors(suite, proof):
    """Replace an invalid legacy visibility selector with visibility + case-sensitive text assertions."""
    if proof.get('status') != 'failed' or proof.get('infrastructure_failure'):
        return None
    document = suite.model_dump()
    tests = {t['id']: t for t in document['tests']}
    affected = set()
    pattern = re.compile(r'(?P<base>.+?)(?<!\\):contains\((?P<text>"(?:[^"\\]|\\.)*")\)$')
    for result in proof.get('report', {}).get('tests', []):
        if result.get('status') != 'failed':
            continue
        test, index = tests.get(result.get('id')), result.get('failed_step')
        error = result.get('error', '')
        if (not test or type(index) is not int or not 0 <= index < len(test['steps'])
                or 'SyntaxError' not in error or 'not a valid selector' not in error):
            return None
        step = test['steps'][index]
        if step['action'] != 'assert_visible' or not pattern.fullmatch(step['selector'] or ''):
            return None
        affected.add(test['id'])
    if not affected:
        return None
    for test_id in affected:
        steps = []
        for step in tests[test_id]['steps']:
            match = pattern.fullmatch(step['selector'] or '')
            if match:
                if step['action'] != 'assert_visible' or ':contains(' in match['base']:
                    return None
                value = json.loads(match['text'])
                if not value:
                    return None
                steps += [{**step, 'selector': match['base']},
                          {'action': 'assert_contains_text', 'selector': match['base'], 'value': value}]
            else:
                steps.append(step)
        tests[test_id]['steps'] = steps
    repaired = QaPlan.model_validate(document)
    repaired.check_selector_contracts()
    return repaired


def passed_setup_prefixes(suite, proof):
    """Only existing UI actions preceding the first assertion of a fully passed test."""
    passed = {t['id'] for t in proof.get('report', {}).get('tests', []) if t.get('status') == 'passed'}
    prefixes = {}
    for test in suite.tests:
        if test.id not in passed:
            continue
        steps = []
        for step in test.steps:
            if step.action.startswith('assert_') or step.action == 'reload':
                break
            if step.action not in ('navigate', 'fill', 'click', 'select_option', 'press', 'check', 'uncheck'):
                break
            steps.append(step.model_dump())
        if steps:
            prefixes[test.id] = steps
    return prefixes


def repair_test_setup(suite, proof, proposal):
    """Copy proven fixture actions; preserve all original steps, assertions, IDs, UAC and purposes."""
    if proof.get('status') != 'failed' or proof.get('infrastructure_failure'):
        return None
    failed = {t['id']: t for t in proof.get('report', {}).get('tests', []) if t.get('status') == 'failed'}
    if not failed or {item.test_id for item in proposal.setups} != set(failed):
        return None
    prefixes = passed_setup_prefixes(suite, proof)
    document = suite.model_dump()
    tests = {t['id']: t for t in document['tests']}
    for item in proposal.setups:
        test = tests.get(item.test_id)
        fixture = prefixes.get(item.fixture_test_id)
        index = item.before_step
        if (not test or not fixture or not 0 <= index < len(test['steps'])
                or any(i >= len(fixture) for i in item.step_indexes)):
            return None
        # Setup belongs before the test, or immediately after a reload to reopen
        # transient UI. Never insert actions around an existing functional assertion.
        if index != 0 and (test['steps'][index - 1]['action'] != 'reload'
                           or failed[item.test_id].get('failed_step') != index):
            return None
        selected = [fixture[i] for i in item.step_indexes]
        if index != 0 and any(step['action'] != 'click' for step in selected):
            return None
        failed_step = failed[item.test_id].get('failed_step')
        if type(failed_step) is not int or not 0 <= failed_step < len(test['steps']):
            return None
        if failed_step > 0 and test['steps'][failed_step - 1]['action'] == 'reload' and index != failed_step:
            return None  # reopening belongs after reload, never before an already complete setup
        if index == 0:
            used = {(step['action'], step.get('selector')) for step in test['steps'][:failed_step]}
            if any((step['action'], step.get('selector')) in used for step in selected
                   if step['action'] != 'navigate'):
                return None  # do not create the same prerequisite twice or override an existing setup
        test['steps'] = test['steps'][:index] + selected + test['steps'][index:]
    repaired = QaPlan.model_validate(document)
    repaired.check_selector_contracts()
    return repaired if repaired.digest != suite.digest else None


def classify_failure(proof):
    if proof.get('infrastructure_failure') or proof.get('status') == 'incomplete':
        return 'infrastructure'
    failed = [t for t in (proof.get('report') or {}).get('tests', []) if t.get('status') == 'failed']
    if any(t.get('failure_kind') in ('selector_contract', 'action_contract', 'expectation_diagnosis') for t in failed):
        return 'test_contract'
    return 'application_or_unknown'


def repair_contract_errors(suite, proof):
    """Qualify a visible alert or replace fill on an observed select. Never weaken assertions."""
    if proof.get('status') != 'failed' or classify_failure(proof) != 'test_contract':
        return None
    document = suite.model_dump()
    tests = {t['id']: t for t in document['tests']}
    for result in proof['report']['tests']:
        if result.get('status') != 'failed':
            continue
        test = tests.get(result.get('id'))
        index = result.get('failed_step')
        if not test or type(index) is not int or not 0 <= index < len(test['steps']):
            return None
        # Expected values require diagnosis against scope/input, never copying
        # actual application output into a suite through automatic DOM repair.
        if result.get('failure_kind') not in ('selector_contract', 'action_contract'):
            return None
        step = test['steps'][index]
        if result.get('failure_kind') == 'action_contract':
            diagnosis = result.get('action_diagnosis') or {}
            if (step['action'] != 'fill' or diagnosis.get('action') != 'fill' or
                    diagnosis.get('tag') != 'select' or diagnosis.get('selector') != step['selector'] or
                    type(diagnosis.get('matching_options')) is not int or diagnosis['matching_options'] != 1 or
                    diagnosis.get('select_by') != 'value' or not isinstance(step['value'], str) or
                    diagnosis.get('option_value') != step['value']):
                return None
            step.update(action='select_option', select_by='value')
            continue
        diagnosis = result.get('selector_diagnosis') or {}
        selector = diagnosis.get('unique_visible_alert')
        if (not test or type(index) is not int or not 0 <= index < len(test['steps']) or
                type(diagnosis.get('matched_count')) is not int or not 1 < diagnosis['matched_count'] <= 100 or
                diagnosis.get('visible_count') != 1 or not isinstance(selector, str) or
                not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_-]{0,99}', selector)):
            return None
        if step['action'] != 'assert_visible' or step['selector'] != diagnosis.get('selector'):
            return None
        step['selector'] = selector
    repaired = QaPlan.model_validate(document)
    return repaired if repaired.digest != suite.digest else None


def repair_visible_alerts(suite, proof):
    # Compatibility for callers of the original helper; the runtime uses the
    # expanded name so the broader, bounded repair policy is explicit.
    return repair_contract_errors(suite, proof)


def repair_csv_inputs(suite, proof):
    """After QA diagnoses a test fault, fix only serialized tokens of original fill input.

    Actual download values never supply a replacement. Wrong application output
    therefore still fails the complete re-execution on the new target.
    """
    if proof.get('status') != 'failed':
        return None
    failed = [t for t in (proof.get('report') or {}).get('tests', []) if t.get('status') == 'failed']
    issues = suite.csv_expectation_issues()
    selected = []
    for test in failed:
        if (test.get('failure_kind') != 'expectation_diagnosis' or
                (test.get('expectation_diagnosis') or {}).get('comparison') != 'parsed_csv_cells'):
            return None
        cells = [issue for issue in issues if issue['test_id'] == test['id']
                 and issue['step'] == test.get('failed_step')]
        if not cells:
            return None
        selected += cells
    if not selected:
        return None
    document = suite.model_dump()
    tests = {t['id']: t for t in document['tests']}
    for cell in selected:
        tests[cell['test_id']]['steps'][cell['step']]['download']['csv_rows'][cell['row']][cell['column']] = cell['input']
    repaired = QaPlan.model_validate(document)
    return repaired if repaired.digest != suite.digest else None
