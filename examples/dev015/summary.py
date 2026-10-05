"""Audit a completed private DEV-015 pilot and write a public receipt without prompts or paths.

Run with the same backend environment as qualification.py. Keep the private DB,
snapshot and artifacts for independent review; this receipt does not replace them.
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.persistence import ArtifactStore, Database, pinned_artifacts
from app.persistence.columns import utcnow
from app.persistence.models import Approval, Artifact, Candidate, Job, Project, Release, Verification
from app.workspace.gitbroker import GitBroker
from qualification import fingerprint


def usage(jobs):
    keys = ('model_calls', 'tool_calls', 'input_tokens', 'output_tokens', 'total_tokens', 'cost_usd')
    return {**{k: sum(j.usage.get(k, 0) for j in jobs) for k in keys},
            'unknown': sorted({k for j in jobs for k in j.usage.get('_unknown', [])})}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--failed-root', type=Path, action='append', default=[])
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    result = json.loads((root / 'result.json').read_text())
    assert result['facts'].get('completed'), 'unfinished pilot cannot produce a completion receipt'
    facts = result['facts']
    facts['backup_restore'].pop('destination', None)
    pid = result['project_id']
    db, store = Database(root / 'app.sqlite3'), ArtifactStore(root / 'artifacts')
    with db.read() as s:
        project = s.get(Project, pid)
        source = Path(project.repo_ref)
        assert fingerprint(source) == json.loads((root / 'source-fingerprint.json').read_text())
        jobs = list(s.scalars(select(Job)))
        candidates = list(s.scalars(select(Candidate)))
        releases = list(s.scalars(select(Release)))
        assert len(releases) == 1 and releases[0].status == 'approved'
        assert project.workflow['accepted_tip'] == releases[0].accepted_tip
        broker = GitBroker(root / 'workspaces' / pid / 'repo.git', root / 'audit-git-home')
        broker._bare('fsck', '--full', '--no-reflogs')
        assert broker.accepted_sha() == project.workflow['accepted_tip']
        baseline_test = broker._bare('show', facts['source_sha'] + ':test/cart.test.js').decode().replace('\r\n', '\n')
        release_test = broker._bare('show', releases[0].accepted_tip + ':test/cart.test.js').decode().replace('\r\n', '\n')
        def normalized_fixture_test(body):
            # This controlled fixture changed only import ordering and a comment
            # during recovery. Keep every executable/assertion line and the exact
            # import set; this is not a general JavaScript equivalence checker.
            lines = [x.strip() for x in body.splitlines() if x.strip() and not x.strip().startswith('//')]
            return sorted(x for x in lines if x.startswith('import ')), [x for x in lines if not x.startswith('import ')]
        assert normalized_fixture_test(baseline_test) == normalized_fixture_test(release_test), 'repository assertions changed'
        pins = pinned_artifacts(s)
        verified = []
        for aid in sorted(pins):
            a = s.get(Artifact, aid)
            assert a is not None and a.availability == 'available', aid
            if a.storage == 'file':
                store.read_bytes(s, aid)  # checks size and digest, not just the DB flag
            else:
                broker._bare('cat-file', '-e', a.checksum + '^{commit}')
            verified.append({'id': aid, 'checksum': a.checksum})
        approvals = [{'id': a.id, 'type': a.type, 'user_id': a.user_id,
            'ticket_id': a.ticket_id, 'scope_version': a.scope_version, 'candidate_id': a.candidate_id,
            'release_id': a.release_id, 'target_digest': a.target_digest, 'evidence_ids': a.evidence_artifact_ids}
            for a in s.scalars(select(Approval))]
        assert all(a['user_id'] == 'user:dev015-qualification' for a in approvals)
        verifications = [{'id': v.id, 'candidate_id': v.candidate_id, 'target_digest': v.target_digest,
            'status': v.status, 'counts': v.counts, 'expected_test_ids': v.expected_test_ids,
            'uac_coverage': v.uac_coverage, 'evidence_ids': v.evidence_artifact_ids}
            for v in s.scalars(select(Verification))]
        final_suites = []
        for candidate in candidates:
            if candidate.status != 'accepted':
                continue
            target = json.loads(store.read_bytes(s, candidate.target_artifact_id))
            suite = json.loads(store.read_bytes(s, target['suite_artifact_id']))
            final_suites.append({'ticket_id': candidate.ticket_id, 'scope_version': candidate.scope_version,
                'suite_artifact_id': target['suite_artifact_id'], 'tests': suite['tests']})
        transaction_id = facts['preview_transaction']['candidate_id']
        transaction = next(c for c in candidates if c.id == transaction_id)
        transaction_suite = next(x for x in final_suites if x['ticket_id'] == transaction.ticket_id)
        def complete_toggle_case(test):
            steps = test['steps']
            clicks = [i for i, step in enumerate(steps) if step['action'] == 'click' and
                      'discount-toggle' in step['selector']]
            return len(clicks) >= 2 and any(step['action'] == 'assert_text' and step['value'] == '7.20'
                for step in steps[clicks[0]+1:clicks[1]]) and any(step['action'] == 'assert_text' and
                step['value'] == '8.00' for step in steps[clicks[1]+1:])
        assert any(complete_toggle_case(t) for t in transaction_suite['tests']), 'two-click semantics are missing'
        reports = []
        for aid in releases[0].evidence_artifact_ids:
            a = s.get(Artifact, aid)
            if a.path.endswith('.json'):
                body = json.loads(store.read_bytes(s, aid))
                if body.get('kind') == 'release_verification':
                    reports.append({'artifact_id': aid, 'checksum': a.checksum, 'report': {
                        k: body[k] for k in ('kind', 'status', 'target_artifact_id', 'target_digest',
                            'accepted_tip', 'commit_sha', 'counts', 'expected_test_ids', 'executed_test_ids',
                            'uac_coverage', 'missing_automated_uac', 'repo_gate', 'scope_digest',
                            'suite_digest', 'invocation_id', 'infrastructure_failure') if k in body}})
        assert len(reports) == 1 and reports[0]['report']['status'] == 'passed'
        models = {(j.result['provider'], j.result['model']) for j in jobs
                  if j.result and j.result.get('provider') and j.result.get('model')}
        models.update((m['provider'], m['model']) for j in jobs
                      for m in j.runtime_ref.get('pipeline_models', []))
        assert all(not j.runtime_ref.get('fake') and not (j.result or {}).get('fake_provider') for j in jobs)
        receipt = {'ticket': 'DEV-015', 'checked_at': utcnow().isoformat(),
            'baseline_commit': 'b12d11fae10c1a3cee762191540ed6e7143ac41a',
            'independent_review': 'NOT_REVIEWED', 'checkpoint_R8': 'OPEN',
            'manual_user_uat': False, 'approval_actor': 'qualification test user, not a human UAT session',
            'fixture_bootstrap': True, 'models': [{'provider': x, 'model': y} for x, y in sorted(models)],
            'facts': facts, 'usage': usage(jobs), 'tickets': result['tickets'],
            'candidates': result['candidates'], 'releases': result['releases'], 'messages': result['messages'],
            'approvals': approvals, 'verifications': verifications,
            'final_qa_suites': final_suites, 'two_click_sequence_audited': True,
            'release_reports': reports, 'verified_pins': verified,
            'operator_retries': [{'job_id': j.id, 'parent_job_id': j.parent_job_id,
                'authorization': j.runtime_ref['retry_authorization'], 'limits': j.limits}
                for j in jobs if j.runtime_ref.get('retry_authorization')],
            'budget_stops': [{'job_id': j.id, 'ticket_id': j.ticket_id, 'scope_version': j.scope_version,
                'limit': j.result.get('limit'), 'usage': j.usage, 'limits': j.limits}
                for j in jobs if (j.result or {}).get('reason') == 'budget_exhausted'],
            'accepted_ref_matches_db_and_release': True,
            'original_node_test_imports_and_assertions_preserved': True,
            'node_test_format_changes': 'import order and one comment only; executable/assertion lines audited',
            'source_full_inventory_unchanged_including_git': True, 'discarded_runs': []}
    db.dispose()
    for failed in args.failed_root:
        with Database(failed / 'app.sqlite3') as discarded:
            with discarded.read() as s:
                receipt['discarded_runs'].append({'name': failed.name,
                    'usage': usage(list(s.scalars(select(Job)))), 'completed': False})
    all_usage = [receipt['usage'], *(x['usage'] for x in receipt['discarded_runs'])]
    receipt['total_reported_usage_including_discarded_runs'] = {
        k: sum(x[k] for x in all_usage) for k in ('model_calls', 'tool_calls', 'input_tokens',
            'output_tokens', 'total_tokens', 'cost_usd')}
    receipt['total_reported_usage_including_discarded_runs']['unknown'] = sorted({
        k for x in all_usage for k in x['unknown']})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'pins_verified': len(verified), 'usage': receipt['usage'], 'independent_review': 'NOT_REVIEWED'}))


if __name__ == '__main__':
    main()
