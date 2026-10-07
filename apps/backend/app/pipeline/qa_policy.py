"""Small QA plans, explicit manual ownership and an auditable planning receipt."""
from .contracts import browser_capabilities


def policy_context():
    return {
        'profile': 'lightweight', 'revision': 1,
        'browser_capabilities': browser_capabilities(),
        'planning_rules': [
            'Use a few coherent user journeys for small tickets, usually 1..3; this is guidance, not a cap.',
            'Every approved automated UAC needs an explicit assertion. Combine journeys without losing coverage.',
            'Manual UAC belong to the user checklist. Do not add mandatory browser cases solely to duplicate them.',
            'If all UAC are manual, retain a meaningful browser smoke test with purpose smoke and empty uac list.',
            'Build, repository gates, browser health, immutable target and user UAT remain required.',
            'Money, stock integrity, permissions and data loss need negative/regression cases proportional to risk.',
            'Do not add cosmetic DOM/label requirements. Check keyboard/accessibility behaviour when required or needed for the journey.',
            'Detect missing runner capabilities before implementation; never silently turn approved automated UAC into manual.',
            'Before scope approval, propose manual mode for subjective/external checks with a concrete user checklist and reason.',
            'A failed assertion needs diagnosis against criteria, inputs, source and runner evidence before application repair.'
        ]}


def preflight(suite, criteria, scope_version, *, new_plan=False):
    suite.check_criteria(criteria)
    if new_plan:
        suite.check_selector_contracts()
        automated = {c['id'] for c in criteria if c.get('mode', 'automated') == 'automated'}
        for test in suite.tests:
            if test.uac and not automated.intersection(test.uac):
                raise ValueError('Approved manual UAC belong to the user checklist, not a new mandatory browser case. '
                                 'Cover automated UAC; for all-manual scope keep a meaningful smoke case with empty uac list.')
    coverage = {c['id']: [test.id for test in suite.tests if c['id'] in test.uac] for c in criteria}
    return {'profile': 'lightweight', 'revision': 1, 'scope_version': scope_version,
            'status': 'planned', 'suite_digest': suite.digest,
            'capability_revision': browser_capabilities()['revision'],
            'test_count': len(suite.tests),
            'criteria': [{'id': c['id'], 'text': c['text'], 'mode': c.get('mode', 'automated'),
                          'test_ids': coverage[c['id']]} for c in criteria]}
