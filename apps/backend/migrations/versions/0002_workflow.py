"""Persist workflow metadata and immutable dependency scope without rebuilding tables."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    # Native ADD COLUMN preserves all 0001 triggers and rows. The new CHECKs belong
    # to each column, so no SQLite batch table reconstruction is necessary.
    op.execute("ALTER TABLE projects ADD COLUMN workflow JSON NOT NULL DEFAULT '{}' "
               "CONSTRAINT ck_projects_workflow_json CHECK (json_valid(workflow) AND json_type(workflow) = 'object')")
    op.execute("ALTER TABLE tickets ADD COLUMN workflow JSON NOT NULL DEFAULT '{}' "
               "CONSTRAINT ck_tickets_workflow_json CHECK (json_valid(workflow) AND json_type(workflow) = 'object')")
    op.execute("ALTER TABLE ticket_versions ADD COLUMN scope JSON NOT NULL DEFAULT '{}' "
               "CONSTRAINT ck_ticket_versions_scope_json CHECK (json_valid(scope) AND json_type(scope) = 'object')")


def downgrade():
    # SQLite's native DROP COLUMN preserves unrelated triggers and constraints.
    op.execute("ALTER TABLE ticket_versions DROP COLUMN scope")
    op.execute("ALTER TABLE tickets DROP COLUMN workflow")
    op.execute("ALTER TABLE projects DROP COLUMN workflow")
