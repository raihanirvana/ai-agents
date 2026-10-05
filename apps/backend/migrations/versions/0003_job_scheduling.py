"""Job scheduling: earliest claim time for backoff and quota waits (DEV-004).

Native ADD COLUMN keeps every 0001/0002 trigger and row intact.
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    op.execute("ALTER TABLE jobs ADD COLUMN available_at DATETIME")


def downgrade():
    op.execute("ALTER TABLE jobs DROP COLUMN available_at")
