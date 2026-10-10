"""User resolution of complete but inconclusive QA, separate from QA pass/UAT.

Only test-contract/unknown diagnoses qualify, on complete failed runs or proven
coverage gaps (candidate passed, feature/bug assertions also passed on base). Missing evidence, infrastructure,
repo gates, smoke, stale targets, and proven application failures cannot be waived.
"""
from sqlalchemy import select, func, or_
from app.persistence.models import Candidate, Verification, TicketVersion, Job, Artifact, QaWaiver
from app.persistence import ArtifactUnavailable
from . import evidence
from .types import Invalid, Conflict


def waiver_for(s, candidate, verification_id=None):
    verification_id = verification_id or (candidate.preview or {}).get('verification_id')
    if not verification_id:
        return None
    query = select(QaWaiver).where(QaWaiver.candidate_id == candidate.id,
        QaWaiver.target_artifact_id == candidate.target_artifact_id, QaWaiver.target_digest == candidate.target_digest)
    if verification_id:
        query = query.where(QaWaiver.verification_id == verification_id)
    return s.scalar(query.order_by(QaWaiver.created_at.desc()).limit(1))


def latest_diagnosis(s, verification_id):
    """Current diagnosis: a supervisor-derived one supersedes the model output it replaces."""
    derived = func.json_extract(Artifact.meta, '$.supervisor_derived').is_not(None)
    return s.scalar(select(Artifact).where(func.json_extract(Artifact.meta, '$.producer') == 'qa-diagnosis',
        func.json_extract(Artifact.meta, '$.verification_id') == verification_id)
        .order_by(derived.desc(), Artifact.created_at.desc(), Artifact.id.desc()).limit(1))


def _diagnosis_summary(s, store, candidate, v, row, diagnosis):
    """What the user must see before owning criteria, including a withheld application claim."""
    summary = {'fault': diagnosis.fault, 'summary': diagnosis.summary,
               'supervisor_derived': row.meta.get('supervisor_derived'), 'withheld_application': None}
    origin_id = row.meta.get('derived_from')
    if not origin_id:
        return summary, []
    from app.pipeline.contracts import QaDiagnosis
    origin = evidence.artifact(s, store, candidate.project_id, origin_id, 'report')
    if (origin.meta.get('producer') != 'qa-diagnosis' or origin.meta.get('verification_id') != v.id
            or origin.meta.get('target_digest') != candidate.target_digest or origin.meta.get('fake') is not False):
        raise Invalid('Withheld diagnosis must belong to this real verification target')
    model = QaDiagnosis.model_validate(evidence.document(s, store, origin_id))
    summary['withheld_application'] = {'artifact_id': origin_id, 'fault': model.fault, 'summary': model.summary,
        'validator_issues': list(row.meta.get('application_repair_issues') or [])[:12],
        'findings': [{key: value for key, value in f.model_dump().items() if key in
                      ('test_id', 'fault', 'criterion_id', 'expected', 'observed', 'reason', 'source_path')}
                     for f in model.findings]}
    return summary, [origin_id]


def qualify(s, store, candidate, verification_id, diagnosis_id, *, historical=False):
    from app.pipeline.contracts import QaPlan, QaDiagnosis, validate_report
    v = s.get(Verification, verification_id)
    if (v is None or v.candidate_id != candidate.id or v.target_artifact_id != candidate.target_artifact_id
            or v.target_digest != candidate.target_digest):
        raise Conflict('QA decision belongs to another candidate/target')
    evidence.target(s, store, candidate, v.target_artifact_id, v.target_digest)
    if not historical:
        latest = s.scalar(select(Verification.id).where(Verification.candidate_id == candidate.id,
            Verification.target_digest == candidate.target_digest).order_by(Verification.created_at.desc(), Verification.id.desc()).limit(1))
        if latest != v.id:
            raise Conflict('QA evidence changed; reload the current verification')
        if s.scalar(select(Job.id).where(Job.ticket_id == candidate.ticket_id,
                Job.scope_version == candidate.scope_version, Job.stage == 'qa',
                or_(Job.status.in_(('queued', 'running', 'waiting_input', 'waiting_quota')),
                    func.json_type(Job.runtime_ref, '$.cleanup') == 'object')).limit(1)):
            raise Conflict('QA or its cleanup is still active; wait before a manual decision')
    if (v.status not in ('failed', 'incomplete') or v.results.get('fake_provider') is not False
            or v.results.get('infrastructure_failure') is not False):
        raise Invalid('Only complete non-fake failed tests or proven coverage gaps can receive a user QA decision')
    target = evidence.document(s, store, v.target_artifact_id)
    suite = QaPlan.model_validate(evidence.document(s, store, target['suite_artifact_id']))
    if suite.digest != v.suite_digest or suite.digest != target['runner_manifest_digest']:
        raise Conflict('QA suite differs from the immutable target')
    reports = []
    for aid in v.evidence_artifact_ids:
        row = evidence.artifact(s, store, candidate.project_id, aid)
        if row.meta.get('producer') != 'verification':
            raise Invalid('Only verification service evidence can qualify for a QA decision')
        if row.kind == 'report':
            doc = evidence.document(s, store, aid)
            if doc.get('invocation_id') == v.evidence_id and isinstance(doc.get('report'), dict):
                reports.append(doc)
    if len(reports) != 1:
        raise Invalid('The authoritative acceptance report is unavailable or ambiguous')
    proof, report = reports[0], reports[0]['report']
    commands = v.results.get('commands')
    if (not isinstance(commands, list) or len(commands) != 1 or
            commands[0].get('argv') != ['isolated-browser-runner', v.evidence_id] or
            type(commands[0].get('exit_code')) is not int or commands[0]['exit_code'] not in (0, 1) or
            proof.get('commands') != commands):
        raise Invalid('The authoritative runner command is missing or did not complete normally')
    admitted = validate_report(report, invocation_id=v.evidence_id, target_digest=v.target_digest, suite=suite)
    # incomplete qualifies only as a proven coverage gap: every candidate test ran
    # and passed, but feature/bug assertions also passed on the accepted base.
    from app.pipeline.qa_repair import baseline_coverage_gaps
    gaps = baseline_coverage_gaps(suite, proof) if v.status == 'incomplete' else []
    if v.status == 'incomplete' and not gaps:
        raise Invalid('Incomplete QA without a proven coverage gap cannot receive a user QA decision')
    if (not isinstance(target.get('runner'), dict) or proof.get('runner') != target['runner'] or
            admitted['status'] != ('failed' if v.status == 'failed' else 'passed') or admitted['counts'] != v.counts or
            v.expected_test_ids != [test.id for test in suite.tests] or
            set(admitted['executed']) != set(v.expected_test_ids) or
            admitted['coverage'] != v.uac_coverage or
            len(v.results.get('executed_test_ids', [])) != len(v.expected_test_ids) or
            set(v.results.get('executed_test_ids', [])) != set(v.expected_test_ids) or
            proof.get('status') != v.status or proof.get('infrastructure_failure') is not False or
            report.get('smoke_passed') is not True or (proof.get('required_checks') or {}).get('status') != 'passed'):
        raise Invalid('Incomplete execution, smoke, baseline or repository gate failure cannot be waived')
    diagnosis_row = evidence.artifact(s, store, candidate.project_id, diagnosis_id, 'report')
    if (diagnosis_row.meta.get('producer') != 'qa-diagnosis' or diagnosis_row.meta.get('verification_id') != v.id
            or diagnosis_row.meta.get('target_digest') != candidate.target_digest or diagnosis_row.meta.get('fake') is not False):
        raise Invalid('Diagnosis must belong to this real verification target')
    if not historical:
        latest = latest_diagnosis(s, v.id)
        if latest is None or latest.id != diagnosis_id:
            raise Conflict('QA diagnosis changed; reload before deciding')
    diagnosis = QaDiagnosis.model_validate(evidence.document(s, store, diagnosis_id))
    shown, origin_ids = _diagnosis_summary(s, store, candidate, v, diagnosis_row, diagnosis)
    failed = ({test['id'] for test in report['tests'] if test['status'] == 'failed'} if v.status == 'failed'
              else set(gaps))
    if (diagnosis.fault not in ('test', 'unknown') or {f.test_id for f in diagnosis.findings} != failed
            or any(f.fault in ('application', 'infrastructure') for f in diagnosis.findings)):
        raise Invalid('Proven application/infrastructure failures cannot receive a manual QA decision')
    criteria = s.scalar(select(TicketVersion).where(TicketVersion.ticket_id == candidate.ticket_id,
                                                   TicketVersion.version == candidate.scope_version))
    suite.check_criteria(criteria.uac)
    automatic = {u['id'] for u in criteria.uac if u.get('mode', 'automated') == 'automated'}
    manual = set()
    for test in suite.tests:
        if test.id in failed:
            if test.purpose == 'smoke' or not test.uac or not set(test.uac).issubset(automatic):
                raise Invalid('Smoke and unclassified tests cannot be waived')
            manual.update(test.uac)
    return {'verification': v, 'proof': proof, 'manual_uac_ids': sorted(manual), 'excluded_test_ids': sorted(failed),
            'evidence_ids': list(dict.fromkeys([*v.evidence_artifact_ids, *origin_ids, diagnosis_id])),
            'criteria': [u for u in criteria.uac if u['id'] in manual], 'diagnosis': shown,
            # Decisions recorded before withheld model diagnoses were pinned (2e5e8ac).
            'legacy_evidence_ids': list(dict.fromkeys([*v.evidence_artifact_ids, diagnosis_id]))}


def validate_persisted(s, store, candidate, verification_id):
    w = waiver_for(s, candidate, verification_id)
    if w is None:
        raise Invalid('QA requires passed harness evidence or an exact user QA decision')
    try:
        qualified = qualify(s, store, candidate, verification_id, w.diagnosis_artifact_id, historical=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise Invalid('Pinned QA decision evidence is malformed or unavailable') from exc
    if (w.project_id != candidate.project_id or w.ticket_id != candidate.ticket_id or
            w.scope_version != candidate.scope_version or w.manual_uac_ids != qualified['manual_uac_ids'] or
            w.excluded_test_ids != qualified['excluded_test_ids'] or
            w.evidence_artifact_ids not in (qualified['evidence_ids'], qualified['legacy_evidence_ids'])):
        raise Invalid('QA user decision differs from its pinned evidence')
    for aid in w.evidence_artifact_ids:
        evidence.artifact(s, store, candidate.project_id, aid)
    return qualified['verification']


def available(s, store, ticket):
    """Public candidate for a user decision; never an authorization itself."""
    c = s.get(Candidate, ticket.workflow.get('candidate_id')) if ticket.workflow.get('candidate_id') else None
    if ticket.phase != 'qa' or c is None or c.status != 'review_approved':
        return None
    v = s.scalar(select(Verification).where(Verification.candidate_id == c.id,
        Verification.target_digest == c.target_digest).order_by(Verification.created_at.desc(), Verification.id.desc()).limit(1))
    if not v or v.status not in ('failed', 'incomplete'):
        return None
    a = latest_diagnosis(s, v.id)
    if a is None:
        active = s.scalar(select(Job.id).where(Job.ticket_id == ticket.id, Job.scope_version == c.scope_version,
            Job.stage == 'qa', Job.status.in_(('queued', 'running', 'waiting_input', 'waiting_quota'))).limit(1))
        return {'eligible': False, 'reason': 'Menunggu diagnosis QA untuk target ini.' if active else
                'Diagnosis QA tidak tersedia dan tidak ada job QA aktif; jalankan ulang QA untuk target ini.'}
    try:
        q = qualify(s, store, c, v.id, a.id)
    except (Invalid, Conflict, ValueError, ArtifactUnavailable, KeyError, TypeError) as exc:
        return {'eligible': False, 'reason': str(exc)}
    return {'eligible': True, 'candidate_id': c.id, 'verification_id': v.id,
            'target_artifact_id': c.target_artifact_id, 'target_digest': c.target_digest,
            'diagnosis_artifact_id': a.id, 'evidence_ids': q['evidence_ids'], 'criteria': q['criteria'],
            'diagnosis': q['diagnosis']}
