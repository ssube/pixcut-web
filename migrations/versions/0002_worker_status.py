"""Add durable printer-worker readiness status."""

from alembic import op
from sqlalchemy import Column, ForeignKey, String

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "printer_worker_status",
        Column("id", String, primary_key=True),
        Column("execution_mode", String, nullable=False),
        Column("state", String, nullable=False),
        Column("current_job_id", String, ForeignKey("print_jobs.id")),
        Column("last_seen_at", String, nullable=False),
        Column("message", String),
    )


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is unsupported; restore a verified backup"
    )
