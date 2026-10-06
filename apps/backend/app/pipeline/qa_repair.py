"""Conservative selector repair from the isolated runner's observed DOM facts."""
import re

from .contracts import QaPlan

MAX_SUITE_REPAIRS = 2


def classify_failure(proof):
    if proof.get('infrastructure_failure') or proof.get('status') == 'incomplete':
        return 'infrastructure'
    failed = [t for t in (proof.get('report') or {}).get('tests', []) if t.get('status') == 'failed']
    if any(t.get('failure_kind') == 'selector_contract' for t in failed):
        return 'test_contract'
    return 'application_or_unknown'


def repair_visible_alerts(suite, proof):
    """Only qualify ambiguous visibility checks. Keep every action/value/UAC/id."""
    if proof.get('status') != 'failed' or classify_failure(proof) != 'test_contract':
        return None
    document = suite.model_dump()
    tests = {t['id']: t for t in document['tests']}
    for result in proof['report']['tests']:
        if result.get('status') != 'failed':
            continue
        test = tests.get(result.get('id'))
        index = result.get('failed_step')
        diagnosis = result.get('selector_diagnosis') or {}
        selector = diagnosis.get('unique_visible_alert')
        if (not test or type(index) is not int or not 0 <= index < len(test['steps']) or
                type(diagnosis.get('matched_count')) is not int or not 1 < diagnosis['matched_count'] <= 100 or
                diagnosis.get('visible_count') != 1 or not isinstance(selector, str) or
                not re.fullmatch(r'#[A-Za-z_][A-Za-z0-9_-]{0,99}', selector)):
            return None
        step = test['steps'][index]
        if step['action'] != 'assert_visible' or step['selector'] != diagnosis.get('selector'):
            return None
        step['selector'] = selector
    repaired = QaPlan.model_validate(document)
    return repaired if repaired.digest != suite.digest else None
