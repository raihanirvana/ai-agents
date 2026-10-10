"""Explicit user QA decisions; never rewrite failed verification or grant UAT."""
from alembic import op
from app.persistence.models import QaWaiver

revision = '0007'
down_revision = '0006'
branch_labels = depends_on = None


def upgrade():
    QaWaiver.__table__.create(op.get_bind())
    op.execute("""CREATE TRIGGER trg_qa_waivers_identity BEFORE INSERT ON qa_waivers BEGIN
      SELECT RAISE(ABORT, 'QA waiver must match the current reviewed candidate and failed target')
      WHERE NOT EXISTS (SELECT 1 FROM candidates c JOIN tickets t ON t.id=c.ticket_id
        JOIN verifications v ON v.candidate_id=c.id JOIN artifacts a ON a.id=NEW.diagnosis_artifact_id
        WHERE c.id=NEW.candidate_id AND c.project_id=NEW.project_id AND c.ticket_id=NEW.ticket_id
        AND c.scope_version=NEW.scope_version AND t.current_version=NEW.scope_version AND t.phase='qa'
        AND c.status='review_approved' AND c.target_artifact_id=NEW.target_artifact_id
        AND c.target_digest=NEW.target_digest AND v.id=NEW.verification_id AND v.status='failed'
        AND v.target_artifact_id=NEW.target_artifact_id AND v.target_digest=NEW.target_digest
        AND json_extract(v.results,'$.fake_provider')=0 AND json_extract(v.results,'$.infrastructure_failure')=0
        AND a.project_id=NEW.project_id AND a.availability='available'
        AND json_extract(a.metadata,'$.producer')='qa-diagnosis'
        AND json_extract(a.metadata,'$.verification_id')=v.id);
      SELECT RAISE(ABORT, 'QA waiver evidence unavailable') WHERE
        json_array_length(NEW.manual_uac_ids)=0 OR json_array_length(NEW.excluded_test_ids)=0 OR
        EXISTS (SELECT 1 FROM json_each(NEW.evidence_artifact_ids) j LEFT JOIN artifacts a ON a.id=j.value
          WHERE a.id IS NULL OR a.project_id<>NEW.project_id OR a.availability<>'available');
    END""")
    for operation in ('UPDATE', 'DELETE'):
        op.execute(f"CREATE TRIGGER trg_qa_waivers_no_{operation.lower()} BEFORE {operation} ON qa_waivers "
                   "BEGIN SELECT RAISE(ABORT, 'QA user decisions are immutable'); END")
    op.execute('DROP TRIGGER trg_candidates_verified_needs_pass')
    op.execute("""CREATE TRIGGER trg_candidates_verified_needs_pass BEFORE UPDATE OF status ON candidates
      WHEN NEW.status='verified' AND OLD.status<>'verified' BEGIN
      SELECT RAISE(ABORT, 'verified requires a passed verification or an exact user QA decision') WHERE
      NEW.target_artifact_id IS NULL OR NOT EXISTS (SELECT 1 FROM verifications v
        WHERE v.candidate_id=NEW.id AND v.target_artifact_id=NEW.target_artifact_id
        AND v.target_digest=NEW.target_digest AND (v.status='passed' OR EXISTS (
          SELECT 1 FROM qa_waivers w WHERE w.verification_id=v.id AND w.candidate_id=NEW.id
          AND w.target_artifact_id=NEW.target_artifact_id AND w.target_digest=NEW.target_digest)));
    END""")


def downgrade():
    if op.get_bind().exec_driver_sql('SELECT count(*) FROM qa_waivers').scalar():
        raise RuntimeError('Cannot discard persisted user QA decisions')
    op.execute('DROP TRIGGER trg_candidates_verified_needs_pass')
    import runpy
    from pathlib import Path
    legacy = runpy.run_path(str(Path(__file__).with_name('0001_initial_schema.py')))
    legacy['_trigger']("trg_candidates_verified_needs_pass", "BEFORE UPDATE OF status", "candidates",
             "SELECT RAISE(ABORT, 'verified requires a passed verification for the current target') WHERE "
             "NEW.target_artifact_id IS NULL OR NOT EXISTS (SELECT 1 FROM verifications v WHERE v.candidate_id = NEW.id "
             "AND v.target_artifact_id = NEW.target_artifact_id AND v.target_digest = NEW.target_digest "
             "AND v.status = 'passed');",
             "NEW.status = 'verified' AND OLD.status <> 'verified'")
    op.drop_table('qa_waivers')
