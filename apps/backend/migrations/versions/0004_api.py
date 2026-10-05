"""Persistent local sessions, fenced runtime credentials and atomic API receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = depends_on = None


def upgrade():
    op.create_table("local_sessions",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime()))
    op.create_table("runtime_credentials",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("owner", sa.String(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("generation >= 1", name="ck_runtime_credentials_generation"))
    op.create_table("api_commands",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("actor_key", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("actor_key", "idempotency_key", name="uq_api_commands_actor_key_idempotency_key"),
        sa.CheckConstraint("json_valid(response) AND json_type(response) = 'object'", name="ck_api_commands_response_json"))


def downgrade():
    op.drop_table("api_commands")
    op.drop_table("runtime_credentials")
    op.drop_table("local_sessions")
