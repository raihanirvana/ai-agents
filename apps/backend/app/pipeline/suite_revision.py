"""One bounded repair admission, preserving original test intent and expectations.

DOM is diagnostic data, never a source of replacement expected values or authority.
The model proposes a suite; this validator cannot mark it passed.
"""
from copy import deepcopy
from .contracts import QaPlan, QaCoverageRepair
from .qa_repair import baseline_coverage_gaps, repair_coverage, passed_setup_prefixes, option_binding_candidates

MAX_CHANGED_STEPS = 24
MAX_CHANGED_PER_TEST = 12


def revise_suite(original, proof, proposal, criteria, source, ui_contract=None):
    if proposal.abstain:
        raise ValueError('QA abstained: evidence does not support a safe suite revision')
    if proof.get('infrastructure_failure'):
        raise ValueError('infrastructure failures cannot authorize suite revision')
    gaps = set(baseline_coverage_gaps(original, proof))
    rows = (proof.get('report') or {}).get('tests') or []
    old = {test.id: test for test in original.tests}
    if (len(rows) != len(old) or {r.get('id') for r in rows} != set(old) or
            any(r.get('status') not in ('passed', 'failed') for r in rows)):
        raise ValueError('suite revision requires complete authoritative test observations')
    affected = {r['id'] for r in rows if r['status'] == 'failed'} | gaps
    if proposal.suite is not None and proposal.tests:
        raise ValueError('provide affected tests or a complete suite, never both')
    if proposal.suite is not None:
        updated = proposal.suite.materialize_fixtures()
        mapping_ids = set(old)
    else:
        replacements = {t.id: t for t in proposal.tests}
        if (not replacements or len(replacements) != len(proposal.tests) or
                not set(replacements) <= affected):
            raise ValueError('partial revision needs unique affected test IDs only')
        document = original.model_dump()
        document['summary'] = proposal.summary[:1000]
        document['tests'] = [replacements.get(t.id, t).model_dump() for t in original.tests]
        updated = QaPlan.model_validate(document).materialize_fixtures()
        mapping_ids = set(replacements)
    updated.check_criteria(criteria)
    updated.check_selector_contracts()
    updated.check_selection_contracts()
    updated.check_csv_expectations()
    if ui_contract:
        ui_contract.check_suite(updated)
    new = {test.id: test for test in updated.tests}
    if set(new) != set(old):
        raise ValueError('revision cannot add/remove/rename mandatory tests')
    maps = {m.test_id: m.origin_indexes for m in proposal.mappings}
    if len(maps) != len(proposal.mappings) or set(maps) != mapping_ids:
        raise ValueError('one complete step mapping is required per replaced test')
    setups = {(s.test_id, s.new_step): s for s in proposal.setups}
    if len(setups) != len(proposal.setups):
        raise ValueError('duplicate setup origin')
    witnesses = {(w.test_id, w.new_step): w for w in proposal.witnesses}
    if len(witnesses) != len(proposal.witnesses):
        raise ValueError('duplicate changed-step witness')
    used_setups, used_witnesses, changed = set(), set(), 0
    bindings = {(b.test_id, b.original_step): b for b in proposal.input_bindings}
    if len(bindings) != len(proposal.input_bindings):
        raise ValueError('duplicate original-input binding')
    used_bindings = set()
    option_candidates = option_binding_candidates(original, proof)
    csv_issues = original.csv_expectation_issues()
    # Only fully passed journeys can supply prerequisite actions; no assertions copied as setup.
    prefixes = passed_setup_prefixes(original, proof)
    for key, test in old.items():
        replacement = new[key]
        if replacement.uac != test.uac or replacement.purpose != test.purpose:
            raise ValueError('test UAC and purpose are immutable during suite revision')
        if key not in affected and replacement.model_dump() != test.model_dump():
            raise ValueError('unaffected tests cannot be changed')
        mapping = maps.get(key, list(range(len(test.steps))))
        if len(mapping) != len(replacement.steps) or [i for i in mapping if i is not None] != list(range(len(test.steps))):
            raise ValueError('every original step/assertion must remain exactly once in original order')
        test_changed = 0
        copied_indexes = {}
        observations = next(r for r in rows if r['id'] == key)
        observed = {r.get('selector') for r in (observations.get('dom') or {}).get('locator_candidates', [])}
        observed.update(r.get('selector') for r in (observations.get('selector_diagnosis') or {}).get('candidates', []))
        alert = (observations.get('selector_diagnosis') or {}).get('unique_visible_alert')
        if alert:
            observed.add(alert)
        for index, origin in enumerate(mapping):
            after = replacement.steps[index].model_dump()
            pair = (key, index)
            if origin is None:
                ref = setups.get(pair)
                if key not in affected:
                    raise ValueError('insertions can only repair an observed affected test')
                if after['action'].startswith('assert_'):
                    raise ValueError('new outcome assertions cannot be introduced through setup')
                if ref is not None:
                    prefix = prefixes.get(ref.source_test_id, [])
                    step = prefix[ref.source_step] if 0 <= ref.source_step < len(prefix) else None
                    if step is None or after != step:
                        raise ValueError('setup must copy a passed prerequisite step exactly, including original inputs')
                    indexes = copied_indexes.setdefault(ref.source_test_id, [])
                    if indexes and ref.source_step <= indexes[-1]:
                        raise ValueError('copied prerequisite steps must keep original order without duplicates')
                    indexes.append(ref.source_step)
                    used_setups.add(pair)
                else:
                    witness = witnesses.get(pair)
                    if (witness is None or witness.original_step is not None or
                            witness.source_excerpt not in source.get(witness.source_path, '') or
                            after['action'] not in ('click', 'fill', 'select_option', 'press', 'check', 'uncheck',
                                                     'hover', 'focus')):
                        raise ValueError('inserted action requires a shipped-source witness and supported user action')
                    # No new inputs/expectations: use an original value for this same type of action.
                    if after.get('value') is not None and not any(
                            s.action == after['action'] and s.value == after['value'] and
                            s.select_by == replacement.steps[index].select_by for s in test.steps):
                        raise ValueError('inserted action cannot invent new input data')
                    if not ui_contract and after['selector'] not in observed:
                        raise ValueError('inserted legacy action needs a runner-observed locator')
                    used_witnesses.add(pair)
                test_changed += 1
                continue
            before = test.steps[origin].model_dump()
            if before == after:
                continue
            if key not in affected:
                raise ValueError('only observed affected tests can be revised')
            witness = witnesses.get(pair)
            if (witness is None or witness.original_step != origin or
                    witness.source_excerpt not in source.get(witness.source_path, '')):
                raise ValueError('each changed original step needs an exact shipped-source witness')
            # Assertion type, value, download/dialog expectations and every input remain immutable.
            before_without_selector = {k: v for k, v in before.items() if k != 'selector'}
            after_without_selector = {k: v for k, v in after.items() if k != 'selector'}
            canonical = deepcopy(before_without_selector)
            # Canonical parsed CSV comes from original fixture input, never the actual download.
            for cell in csv_issues:
                if cell['test_id'] == key and cell['step'] == origin:
                    canonical['download']['csv_rows'][cell['row']][cell['column']] = cell['input']
            if before_without_selector != after_without_selector:
                facts = observations.get('action_diagnosis') or {}
                converted = {**before_without_selector, 'action': 'select_option', 'select_by': 'value'}
                same_input_conversion = (origin == observations.get('failed_step') and before['action'] == 'fill' and
                        after_without_selector == converted and facts.get('tag') == 'select' and
                        facts.get('selector') == before['selector'] and facts.get('option_value') == before['value'] and
                        facts.get('matching_options') == 1 and facts.get('select_by') == 'value')
                bound_input = False
                binding = bindings.get((key, origin))
                choices = option_candidates.get(key)
                if binding and choices and 0 <= binding.original_fill_step < origin:
                    original_input = test.steps[binding.original_fill_step]
                    label = original_input.value
                    expected = {**before_without_selector, 'select_by': 'label', 'value': label}
                    bound_input = (before['action'] == 'select_option' and before['selector'] == choices['selector'] and
                        isinstance(before['value'], str) and not any(r['value'] == before['value'] for r in choices['options']) and
                        any(r['input_step'] == binding.original_fill_step for r in choices['inputs']) and
                        after_without_selector == expected)
                    if bound_input:
                        used_bindings.add((key, origin))
                if not (same_input_conversion or bound_input or canonical == after_without_selector):
                    raise ValueError('revision cannot change inputs, assertion modes, expected values or action semantics')
            if after['selector'] != before['selector']:
                if not ui_contract and after['selector'] not in observed:
                    raise ValueError('legacy selector replacement must be observed by the isolated runner')
            used_witnesses.add(pair)
            test_changed += 1
        if test_changed > MAX_CHANGED_PER_TEST:
            raise ValueError('revision exceeds 12 changed/inserted steps per test')
        changed += test_changed
    if used_setups != set(setups) or used_witnesses != set(witnesses) or used_bindings != set(bindings):
        raise ValueError('unused or unknown revision witnesses/setup origins')
    if not 1 <= changed <= MAX_CHANGED_STEPS or updated.digest == original.digest:
        raise ValueError('revision must change 1..24 steps, not repeat an unchanged failure')
    if gaps:
        # Existing coverage qualification remains authoritative, within the same proposal path.
        repair_coverage(original, proof, QaCoverageRepair(kind='qa_coverage_repair',
            summary=proposal.summary, suite=updated, witnesses=proposal.coverage_witnesses), criteria, source,
            permitted_test_repairs=affected - gaps)
    elif proposal.coverage_witnesses:
        raise ValueError('coverage witnesses require a proven baseline coverage gap')
    return updated
