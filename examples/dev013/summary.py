"""Export a small sanitized qualification summary; authoritative artifacts stay in the local DB/store."""
import argparse
import json
from pathlib import Path
from sqlalchemy import select
from app.persistence import Database, ArtifactStore
from app.persistence.models import Project, Candidate, Verification, Approval, Artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = json.loads((args.root / 'result.json').read_text())
    db, store = Database(args.root / 'app.sqlite3'), ArtifactStore(args.root / 'artifacts')
    try:
        with db.read() as s:
            p = s.get(Project, result['project_id'])
            c = s.get(Candidate, result['candidates'][-1]['id'])
            v = s.get(Verification, result['verifications'][-1]['id'])
            baseline = json.loads(store.read_bytes(s, result['onboarding_report']))
            report = next(json.loads(store.read_bytes(s, aid)) for aid in v.evidence_artifact_ids
                          if s.get(Artifact, aid).kind == 'report'
                          and json.loads(store.read_bytes(s, aid)).get('invocation_id'))
            approvals = list(s.scalars(select(Approval).where(Approval.candidate_id == c.id, Approval.type == 'uat')))
            summary = {'ticket': 'DEV-013', 'checked_on': '2026-10-05', 'independent_review': 'NOT_REVIEWED',
                'scenario': 'Existing coffee app exported from DEV-010 verified candidate; add receipt feature via real Hermes/provider.',
                'source': {'pilot_candidate_sha': result['source_pilot_candidate'], 'fixture_sha': result['source_fixture_sha'],
                    'dirty': result['source_dirty'], 'unchanged_full_inventory_including_git': result['source_unchanged'],
                    'refs_digest': baseline['refs_digest'], 'config_digest': baseline['config_digest'],
                    'dirty_status': baseline['source_status']},
                'baseline': {'report_id': result['onboarding_report'], 'status': baseline['status'],
                    'repo_tests': baseline['baseline']['gate']['counts'], 'start_healthy': baseline['baseline']['start']['healthy'],
                    'command_exit_codes': [r['exit_code'] for r in baseline['commands']],
                    'note': 'Qualification ran before the separate node/npm version probes were added; final runtime probes are covered by Docker tests.'},
                'pipeline': {'provider': result['provider'], 'model': result['model'], 'real_provider': True,
                    'usage': result['usage'], 'phase': result['phase'], 'candidate_id': c.id,
                    'candidate_sha': c.commit_sha, 'accepted_tip': p.workflow['accepted_tip'],
                    'target_artifact_id': c.target_artifact_id, 'target_digest': c.target_digest,
                    'verification_id': v.id, 'counts': v.counts, 'uac_coverage': v.uac_coverage,
                    'evidence_ids': v.evidence_artifact_ids, 'required_checks': report['required_checks'],
                    'baseline_browser_status': report['baseline']['execution']['status'],
                    'candidate_browser_status': report['status'], 'integrator': c.integration},
                'approval': {'fixture_uat_approval': result['fixture_uat_approval'], 'manual_user_uat': False,
                    'accepted_by_production_integrator': result['accepted_by_production_integrator'],
                    'approval_ids': [a.id for a in approvals], 'actors': [a.user_id for a in approvals],
                    'note': 'Scope/UAT commands came from a named test-user in an isolated qualification DB. This is not manual user acceptance or DEV-015 pilot completion.'},
                'artifact_location_note': 'Private WSL qualification-01 SQLite, managed Git and artifact store retained locally; no secrets/DB/build bundles committed.'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    finally:
        db.dispose()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
