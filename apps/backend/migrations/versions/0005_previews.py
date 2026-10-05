"""On-demand preview lifecycle records (DEV-011)."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None

STATUSES = "'requested', 'starting', 'ready', 'stopping', 'stopped', 'failed'"


def upgrade():
    op.create_table("previews",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("ticket_id", sa.String(), sa.ForeignKey("tickets.id"), nullable=False),
        sa.Column("candidate_id", sa.String(), sa.ForeignKey("candidates.id"), nullable=False),
        sa.Column("scope_version", sa.Integer(), nullable=False),
        sa.Column("target_artifact_id", sa.String(), nullable=False),
        sa.Column("target_digest", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("port", sa.Integer()),
        sa.Column("container_name", sa.String()),
        sa.Column("owner", sa.String()),
        sa.Column("stop_reason", sa.String()),
        sa.Column("error", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime()),
        sa.Column("stopped_at", sa.DateTime()),
        sa.ForeignKeyConstraint(["target_artifact_id", "target_digest"], ["artifacts.id", "artifacts.checksum"],
                                name="fk_previews_target_artifact_id_artifacts"),
        sa.CheckConstraint(f"status IN ({STATUSES})", name="ck_previews_status"),
        sa.CheckConstraint("port IS NULL OR port BETWEEN 1 AND 65535", name="ck_previews_port"),
        sa.CheckConstraint("revision >= 1 AND scope_version >= 1", name="ck_previews_counters"),
        sa.CheckConstraint("json_valid(details) AND json_type(details) = 'object'", name="ck_previews_details_json"))
    op.create_index("ix_previews_candidate", "previews", ["candidate_id"])
    op.create_index("ix_previews_status", "previews", ["status"])


def downgrade():
    op.drop_index("ix_previews_status", table_name="previews")
    op.drop_index("ix_previews_candidate", table_name="previews")
    op.drop_table("previews")
