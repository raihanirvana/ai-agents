"""Index recovery deadlines without hydrating job payloads on every tick."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    op.create_index('ix_jobs_lease_expiry', 'jobs', ['status', 'lease_expires_at'])
    op.execute("CREATE INDEX ix_jobs_cleanup_expiry ON jobs "
               "(julianday(json_extract(runtime_ref, '$.cleanup.expires_at'))) "
               "WHERE json_type(runtime_ref, '$.cleanup') = 'object'")


def downgrade():
    op.drop_index('ix_jobs_cleanup_expiry', table_name='jobs')
    op.drop_index('ix_jobs_lease_expiry', table_name='jobs')
