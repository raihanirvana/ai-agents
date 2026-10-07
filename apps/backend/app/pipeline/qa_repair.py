"""Conservative contract repair from the isolated runner's observed DOM facts."""
import re

from .contracts import QaPlan

MAX_SUITE_REPAIRS = 2


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
