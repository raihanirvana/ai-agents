"""User QA decisions may also own a proven coverage gap (incomplete verification).

Every other guard of 0007 is unchanged; the application layer (qa_resolution.qualify)
still requires a passing candidate run, smoke, repository gates and base evidence.
"""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = depends_on = None

_TRIGGER = """CREATE TRIGGER trg_qa_waivers_identity BEFORE INSERT ON qa_waivers BEGIN
      SELECT RAISE(ABORT, 'QA waiver must match the current reviewed candidate and failed target')
      WHERE NOT EXISTS (SELECT 1 FROM candidates c JOIN tickets t ON t.id=c.ticket_id
        JOIN verifications v ON v.candidate_id=c.id JOIN artifacts a ON a.id=NEW.diagnosis_artifact_id
        WHERE c.id=NEW.candidate_id AND c.project_id=NEW.project_id AND c.ticket_id=NEW.ticket_id
        AND c.scope_version=NEW.scope_version AND t.current_version=NEW.scope_version AND t.phase='qa'
        AND c.status='review_approved' AND c.target_artifact_id=NEW.target_artifact_id
        AND c.target_digest=NEW.target_digest AND v.id=NEW.verification_id AND v.status IN ({statuses})
        AND v.target_artifact_id=NEW.target_artifact_id AND v.target_digest=NEW.target_digest
        AND json_extract(v.results,'$.fake_provider')=0 AND json_extract(v.results,'$.infrastructure_failure')=0
        AND a.project_id=NEW.project_id AND a.availability='available'
        AND json_extract(a.metadata,'$.producer')='qa-diagnosis'
        AND json_extract(a.metadata,'$.verification_id')=v.id);
      SELECT RAISE(ABORT, 'QA waiver evidence unavailable') WHERE
        json_array_length(NEW.manual_uac_ids)=0 OR json_array_length(NEW.excluded_test_ids)=0 OR
        EXISTS (SELECT 1 FROM json_each(NEW.evidence_artifact_ids) j LEFT JOIN artifacts a ON a.id=j.value
          WHERE a.id IS NULL OR a.project_id<>NEW.project_id OR a.availability<>'available');
    END"""


def upgrade():
    op.execute('DROP TRIGGER trg_qa_waivers_identity')
    op.execute(_TRIGGER.format(statuses="'failed','incomplete'"))


def downgrade():
    if op.get_bind().exec_driver_sql(
            "SELECT count(*) FROM qa_waivers w JOIN verifications v ON v.id=w.verification_id "
            "WHERE v.status<>'failed'").scalar():
        raise RuntimeError('Cannot discard persisted user QA decisions on coverage gaps')
    op.execute('DROP TRIGGER trg_qa_waivers_identity')
    op.execute(_TRIGGER.format(statuses="'failed'"))
