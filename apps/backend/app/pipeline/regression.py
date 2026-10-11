"""Project regression gate (DEV-024): accepted QA journeys guard every later ticket.

The suite is pinned by acceptance itself: the latest accepted candidate of each other
ticket keeps an immutable target whose QA suite passed (minus tests the user took over
in a QA decision). Nothing here grants QA pass, UAT or release.
"""
import re

from sqlalchemy import func, select

from app.persistence.models import Approval, Candidate, Ticket
from .contracts import BrowserTest, QaPlan, digest_of


def regression_entries(s, store, ticket_id):
    """Latest accepted candidate per other ticket of the project, in UAT-approval order."""
    from app.domain import evidence
    from app.domain.qa_resolution import waiver_for
    from app.domain.types import Invalid
    ticket = s.get(Ticket, ticket_id)
    accepted_at = (select(func.max(Approval.created_at)).where(Approval.type == 'uat',
                   Approval.candidate_id == Candidate.id).correlate(Candidate).scalar_subquery())
    rows = list(s.scalars(select(Candidate).where(Candidate.project_id == ticket.project_id,
        Candidate.ticket_id != ticket.id, Candidate.status == 'accepted')
        .order_by(func.coalesce(accepted_at, Candidate.updated_at), Candidate.id)))
    latest = {}
    for candidate in rows:
        latest.pop(candidate.ticket_id, None)
        latest[candidate.ticket_id] = candidate  # a re-accepted ticket replaces its earlier journeys
    entries = []
    for candidate in latest.values():
        evidence.target(s, store, candidate, candidate.target_artifact_id, candidate.target_digest)
        target = evidence.document(s, store, candidate.target_artifact_id)
        evidence.artifact(s, store, candidate.project_id, target.get('suite_artifact_id'), 'report')
        suite = QaPlan.model_validate(evidence.document(s, store, target['suite_artifact_id']))
        if suite.digest != target['runner_manifest_digest']:
            raise Invalid('Accepted regression suite differs from its pinned target')
        verification_id = (candidate.preview or {}).get('verification_id')
        verified = evidence.verification(s, store, candidate, verification_id)
        if set(verified.expected_test_ids) != {test.id for test in suite.tests}:
            raise Invalid('Accepted regression suite differs from its verified mandatory journeys')
        waiver = waiver_for(s, candidate, verification_id)
        excluded = set(waiver.excluded_test_ids) if waiver else set()
        tests = [t for t in suite.materialize_fixtures().tests if t.id not in excluded]
        entries.append({'ticket_id': candidate.ticket_id, 'number': s.get(Ticket, candidate.ticket_id).number,
                        'candidate_id': candidate.id, 'scope_version': candidate.scope_version,
                        'target_digest': candidate.target_digest,
                        'target_artifact_id': candidate.target_artifact_id,
                        'suite_artifact_id': target['suite_artifact_id'], 'verification_id': verified.id,
                        'evidence_artifact_ids': list(verified.evidence_artifact_ids),
                        'waiver_id': waiver.id if waiver else None,
                        'waiver_evidence_artifact_ids': list(waiver.evidence_artifact_ids) if waiver else [],
                        'suite_digest': suite.digest, 'legacy': not target.get('ui_contract'),
                        'excluded_test_ids': sorted(excluded), 'tests': tests})
    return entries


def regression_suite(entries):
    """One suite of accepted journeys; IDs are prefixed by ticket number. Returns (suite, legacy_ids, owners)."""
    tests, legacy, owners = [], [], {}
    for entry in entries:
        for test in entry['tests']:
            name = f"reg-t{entry['number']}-{test.id}"
            if len(name) > 64:
                name = f"reg-t{entry['number']}-{digest_of(test.id)[:20]}"
            owners[name] = {'ticket_id': entry['ticket_id'], 'number': entry['number'], 'test_id': test.id}
            if entry['legacy']:
                legacy.append(name)
            tests.append(BrowserTest(id=name, purpose='regression', steps=test.steps,
                                     uac=[f"{entry['ticket_id']}:{u}" for u in test.uac]))
    suite = QaPlan.model_construct(kind='qa_plan', summary=f'Project regression of {len(entries)} accepted tickets',
                                   tests=tests)
    return suite, legacy, owners


def subset(suite, test_ids):
    keep = set(test_ids)
    return QaPlan.model_construct(kind='qa_plan', summary=suite.summary,
                                  tests=[t for t in suite.tests if t.id in keep])


def changed_controls(current_contract, accepted_controls):
    """testids this ticket's contract defines differently from the accepted app (intended change)."""
    accepted = {c['testid']: c for c in accepted_controls}
    from app.agents.ui_contract import UiControl
    changed = set()
    for control in (current_contract or {}).get('controls', []):
        before = accepted.get(control['testid'])
        if before is not None and UiControl.model_validate(control).model_dump() != UiControl.model_validate(before).model_dump():
            changed.add(control['testid'])
    return changed


def touches(test, testids):
    return any(set(re.findall(r'testid=([A-Za-z][A-Za-z0-9_.:-]{0,79})', step.selector or '')) & testids
               for step in test.steps)
