"""Conservative contract repair from the isolated runner's observed DOM facts."""
import re
import json

from .contracts import QaPlan

MAX_SUITE_REPAIRS = 2


def baseline_coverage_gaps(suite, proof):
    """Only completed, authoritative base executions can expose surrogate feature tests."""
    baseline = (proof.get('baseline') or {}).get('execution') or {}
    if (baseline.get('status') not in ('passed', 'failed')
            or baseline.get('infrastructure_failure') or proof.get('infrastructure_failure')):
        return []
    report = baseline.get('report') or {}
    results = report.get('tests') or []
    expected = {test.id for test in suite.tests}
    if (len(results) != len(expected) or {row.get('id') for row in results} != expected
            or report.get('discovered') != len(expected) or report.get('executed') != len(expected)
            or report.get('skipped') != 0
            or any(row.get('status') not in ('passed', 'failed') for row in results)):
        return []
    statuses = {row['id']: row['status'] for row in results}
    return [test.id for test in suite.tests
            if test.purpose in ('feature', 'bug') and statuses[test.id] == 'passed']


def repair_coverage(suite, proof, proposal, criteria, source):
    """Permit new journeys only for proven coverage gaps; keep other cases and UAC fixed.

    Source witnesses make the proposed behaviour reviewable. They do not establish
    correctness: the new target must pass all gates and fresh base/candidate runs.
    """
    affected = set(baseline_coverage_gaps(suite, proof))
    if not affected:
        raise ValueError('coverage repair requires completed baseline evidence')
    repaired = proposal.suite.materialize_fixtures()
    repaired.check_criteria(criteria)
    repaired.check_selector_contracts()
    repaired.check_selection_contracts()
    repaired.check_csv_expectations()
    old = {test.id: test for test in suite.tests}
    new = {test.id: test for test in repaired.tests}
    if set(old) != set(new):
        raise ValueError('coverage repair must retain every original test ID')
    for key, original in old.items():
        updated = new[key]
        if updated.uac != original.uac or updated.purpose != original.purpose:
            raise ValueError('coverage repair cannot change test UAC or purpose')
        if key not in affected and updated.model_dump() != original.model_dump():
            raise ValueError('coverage repair cannot alter unaffected/regression cases')
        if key in affected and updated.steps == original.steps:
            raise ValueError('coverage repair must replace the surrogate journey')
    required = {(key, uac) for key in affected for uac in old[key].uac}
    witnesses = set()
    for witness in proposal.witnesses:
        pair = (witness.test_id, witness.criterion_id)
        if pair not in required or pair in witnesses:
            raise ValueError('coverage witness must map each affected test/UAC exactly once')
        test = new[witness.test_id]
        if not 0 <= witness.action_step < witness.assertion_step < len(test.steps):
            raise ValueError('coverage witness must link an action to a later assertion')
        if test.steps[witness.action_step].action not in (
                'click', 'click_dialog', 'fill', 'select_option', 'press', 'check', 'uncheck',
                'double_click', 'upload_file', 'download', 'reload'):
            raise ValueError('coverage witness needs an actual user action')
        if not test.steps[witness.assertion_step].action.startswith('assert_'):
            raise ValueError('coverage witness needs an explicit behaviour assertion')
        if witness.source_excerpt not in source.get(witness.source_path, ''):
            raise ValueError('coverage witness must quote supplied shipped source exactly')
        witnesses.add(pair)
    if witnesses != required or repaired.digest == suite.digest:
        raise ValueError('coverage repair needs witnesses for every affected test/UAC')
    return repaired


def fill_selector_candidates(suite, proof, source):
    """Bound proposals to unique editable DOM controls with literal source declarations."""
    if proof.get('status') != 'failed' or proof.get('infrastructure_failure'):
        return {}
    literals = set()
    for content in source.values():
        for literal in re.findall(r'''["']([^"'\n]{1,200})["']''', content):
            literals.update(literal.split())
    tests = {test.id: test for test in suite.tests}
    candidates = {}
    for result in (proof.get('report') or {}).get('tests', []):
        if result.get('status') != 'failed':
            continue
        test, index = tests.get(result.get('id')), result.get('failed_step')
        facts = result.get('selector_diagnosis') or {}
        if (test is None or type(index) is not int or not 0 <= index < len(test.steps)
                or test.steps[index].action != 'fill'
                or facts.get('contract') != 'missing_fill_selector'
                or facts.get('selector') != test.steps[index].selector or facts.get('matched_count') != 0):
            continue
        rows = facts.get('candidates')
        if not isinstance(rows, list) or len(rows) > 160:
            continue
        valid = []
        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            selector = row.get('selector')
            if (not isinstance(selector, str) or not re.fullmatch(r'[.#][A-Za-z_][A-Za-z0-9_-]{0,99}', selector)
                    or selector[1:] not in literals or row.get('matched_count') != 1
                    or any(row.get(flag) is not True for flag in ('visible', 'enabled', 'editable'))
                    or row.get('tag') not in ('input', 'textarea')
                    or (row.get('tag') == 'input' and row.get('type') not in ('text', 'search', 'email', 'tel', 'url', 'number'))):
                continue
            valid.append({'candidate_index': row_index, **row})
        if valid:
            candidates[test.id] = {'failed_step': index, 'original_selector': test.steps[index].selector,
                                   'input': test.steps[index].value, 'candidates': valid}
    return candidates


def repair_fill_selectors(suite, proof, source, proposal):
    candidates = fill_selector_candidates(suite, proof, source)
    failed = {r['id'] for r in (proof.get('report') or {}).get('tests', []) if r.get('status') == 'failed'}
    if not failed or {b.test_id for b in proposal.bindings} != failed or not failed.issubset(candidates):
        return None
    document = suite.model_dump()
    tests = {test['id']: test for test in document['tests']}
    for binding in proposal.bindings:
        facts = candidates[binding.test_id]
        choices = {row['candidate_index']: row for row in facts['candidates']}
        if binding.candidate_index not in choices:
            return None
        # Only the failed fill's selector changes. Every input, assertion,
        # subsequent action, test ID and UAC remains byte-for-byte equivalent.
        tests[binding.test_id]['steps'][facts['failed_step']]['selector'] = choices[binding.candidate_index]['selector']
    return QaPlan.model_validate(document)


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
    baseline = (proof.get('baseline') or {}).get('execution') or {}
    report = baseline.get('report') or {}
    rows = report.get('tests') or []
    gaps = proof.get('coverage_gap_test_ids') or []
    if (not proof.get('infrastructure_failure') and not baseline.get('infrastructure_failure') and gaps
            and baseline.get('status') in ('passed', 'failed')
            and report.get('discovered') == report.get('executed') == len(rows) and report.get('skipped') == 0
            and len({row.get('id') for row in rows}) == len(rows)
            and all(row.get('status') in ('passed', 'failed') for row in rows)
            and set(gaps) <= {row.get('id') for row in rows if row.get('status') == 'passed'}):
        return 'test_contract'
    if proof.get('infrastructure_failure') or proof.get('status') == 'incomplete':
        return 'infrastructure'
    failed = [t for t in (proof.get('report') or {}).get('tests', []) if t.get('status') == 'failed']
    if any(t.get('failure_kind') in ('selector_contract', 'action_contract', 'expectation_diagnosis') for t in failed):
        return 'test_contract'
    return 'application_or_unknown'


def option_binding_candidates(suite, proof):
    """Expose only real dropdown labels that match earlier, unchanged fixture inputs."""
    tests = {test.id: test for test in suite.tests}
    candidates = {}
    for result in (proof.get('report') or {}).get('tests', []):
        if result.get('status') != 'failed':
            continue
        diagnosis = result.get('action_diagnosis') or {}
        index, test = result.get('failed_step'), tests.get(result.get('id'))
        if (diagnosis.get('contract') != 'unavailable_option_value' or not test
                or type(index) is not int or not 0 <= index < len(test.steps)):
            continue
        step = test.steps[index]
        if (step.action != 'select_option' or step.select_by not in (None, 'value')
                or diagnosis.get('selector') != step.selector or diagnosis.get('requested') != step.value):
            continue
        options = diagnosis.get('options')
        if (diagnosis.get('options_complete') is not True or not isinstance(options, list)
                or len(options) > 200):
            continue
        inputs = []
        for i, original in enumerate(test.steps[:index]):
            if original.action != 'fill' or not isinstance(original.value, str) or not original.value:
                continue
            matching = [row for row in options if isinstance(row, dict) and row.get('enabled') is True
                        and row.get('label') == original.value and isinstance(row.get('value'), str)]
            if len(matching) == 1:
                inputs.append({'input_step': i, 'label': original.value})
        if inputs:
            candidates[test.id] = {'failed_step': index, 'selector': step.selector, 'inputs': inputs,
                                   'options': options}
    return candidates


def repair_option_bindings(suite, proof, proposal):
    """Correct guessed values using original fixture labels observed in the authoritative DOM report."""
    if proof.get('status') != 'failed' or proof.get('infrastructure_failure'):
        return None
    failed = {test['id'] for test in (proof.get('report') or {}).get('tests', []) if test.get('status') == 'failed'}
    candidates = option_binding_candidates(suite, proof)
    selected = {binding.test_id for binding in proposal.bindings}
    if selected != failed or not selected.issubset(candidates):
        return None
    # Every actual failed selection must be repaired; future selections of the
    # same control may also bind the already-created labels, without changing inputs.
    if any(not any(binding.test_id == test_id and binding.step_index == item['failed_step']
                   for binding in proposal.bindings) for test_id, item in candidates.items() if test_id in selected):
        return None
    document = suite.model_dump()
    tests = {test['id']: test for test in document['tests']}
    for binding in proposal.bindings:
        test, facts = tests[binding.test_id], candidates[binding.test_id]
        index, origin = binding.step_index, binding.label_input_step
        if not 0 <= origin < index < len(test['steps']):
            return None
        step, original = test['steps'][index], test['steps'][origin]
        if (step['action'] != 'select_option' or step['selector'] != facts['selector']
                or step.get('select_by') not in (None, 'value') or not isinstance(step['value'], str)
                or any(row.get('value') == step['value'] for row in facts['options'])
                or original['action'] != 'fill' or not any(item['input_step'] == origin for item in facts['inputs'])):
            return None
        step.update(select_by='label', value=original['value'])
    return QaPlan.model_validate(document)


def application_repair_issues(suite, proof, diagnosis, criteria, source):
    """A model attribution alone cannot turn a malformed test into an application repair."""
    tests = {test.id: test for test in suite.tests}
    results = {test['id']: test for test in (proof.get('report') or {}).get('tests', []) if test.get('status') == 'failed'}
    automated = {criterion['id'] for criterion in criteria if criterion.get('mode', 'automated') == 'automated'}
    csv_issues = {(issue['test_id'], issue['step']) for issue in suite.csv_expectation_issues()}
    issues = []
    if {finding.test_id for finding in diagnosis.findings} != set(results):
        issues.append('diagnosis must cover every failed test exactly once')
    for finding in diagnosis.findings:
        test, result = tests.get(finding.test_id), results.get(finding.test_id)
        if not test or not result:
            issues.append(f'{finding.test_id}: failed evidence is missing')
            continue
        if finding.criterion_id not in automated or finding.criterion_id not in test.uac:
            issues.append(f'{finding.test_id}: an approved automated criterion must be cited')
        excerpt = finding.source_excerpt
        if (not excerpt or len(excerpt.strip()) < 12 or finding.source_path not in source
                or excerpt not in source[finding.source_path]):
            issues.append(f'{finding.test_id}: an exact relevant shipped-source excerpt is required')
        index = result.get('failed_step')
        if type(index) is not int or not 0 <= index < len(test.steps):
            issues.append(f'{finding.test_id}: failing browser step is unavailable')
            continue
        step = test.steps[index]
        if result.get('failure_kind') == 'selector_contract':
            issues.append(f'{finding.test_id}: unresolved selector ambiguity belongs to QA')
        if result.get('failure_kind') == 'action_contract':
            facts = result.get('action_diagnosis') or {}
            values = step.value if isinstance(step.value, list) else [step.value]
            # A deliberately specified static option may genuinely be missing
            # from the rendered app. Require its literal declaration in the
            # cited shipped code; guessed record IDs cannot qualify this way.
            declared_static = (facts.get('contract') == 'unavailable_option_value'
                and step.action == 'select_option' and step.select_by == 'value'
                and excerpt and all(isinstance(value, str) and (
                    json.dumps(value, ensure_ascii=False) in excerpt or repr(value) in excerpt)
                    for value in values))
            if not declared_static:
                issues.append(f'{finding.test_id}: unresolved action/fixture binding belongs to QA')
        if (finding.test_id, index) in csv_issues:
            issues.append(f'{finding.test_id}: CSV expectation disagrees with original input')
        if step.action == 'select_option' and step.select_by is None:
            issues.append(f'{finding.test_id}: selection mode/fixture binding was never specified')
        if ':contains(' in (step.selector or '') and 'SyntaxError' in result.get('error', ''):
            issues.append(f'{finding.test_id}: unsupported selector is a test defect')
    return issues


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
