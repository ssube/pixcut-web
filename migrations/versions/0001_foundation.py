"""Initial immutable revisions, artifact references, queue and event schema."""

from alembic import op
from sqlalchemy import Column, String, Integer, JSON, ForeignKey, UniqueConstraint

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "artifacts",
        Column("hash", String, primary_key=True),
        Column("mime", String, nullable=False),
        Column("size", Integer, nullable=False),
    )
    for name in ("assets", "operations"):
        op.create_table(
            name,
            Column("id", String, primary_key=True),
            Column("data", JSON, nullable=False),
        )
    for name in ("stickers", "projects"):
        op.create_table(
            name,
            Column("id", String, primary_key=True),
            Column("revision", Integer, primary_key=True),
            Column("data", JSON, nullable=False),
        )
    op.create_table(
        "renders",
        Column("id", String, primary_key=True),
        Column("project_id", String, nullable=False),
        Column("revision", Integer, nullable=False),
        Column("data", JSON, nullable=False),
    )
    op.create_table(
        "render_files",
        Column("render_id", String, ForeignKey("renders.id"), primary_key=True),
        Column("name", String, primary_key=True),
        Column("hash", String, ForeignKey("artifacts.hash"), nullable=False),
    )
    op.create_table(
        "print_jobs",
        Column("id", String, primary_key=True),
        Column("created_at", String, nullable=False),
        Column("name", String, nullable=False),
        Column("render_id", String, ForeignKey("renders.id"), nullable=False),
        Column("reprint_of_job_id", String, ForeignKey("print_jobs.id")),
        Column("idempotency_key", String, nullable=False),
        Column("request_hash", String, nullable=False),
        Column("execution_mode", String, nullable=False),
        Column("state", String, nullable=False),
        Column("request", JSON, nullable=False),
        UniqueConstraint("idempotency_key"),
    )
    op.create_table(
        "sheet_jobs",
        Column("id", String, primary_key=True),
        Column("job_id", String, ForeignKey("print_jobs.id"), nullable=False),
        Column("ordinal", Integer, nullable=False),
        Column("page", Integer, nullable=False),
        Column("copy", Integer, nullable=False),
        Column("state", String, nullable=False),
        Column("evidence", JSON),
        UniqueConstraint("job_id", "ordinal"),
    )
    op.create_table(
        "device_attempts",
        Column("id", String, primary_key=True),
        Column("sheet_id", String, ForeignKey("sheet_jobs.id"), nullable=False),
        Column("created_at", String, nullable=False),
        Column("device_job_id", String),
        UniqueConstraint("sheet_id"),
    )
    op.create_table(
        "job_events",
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("job_id", String, ForeignKey("print_jobs.id"), nullable=False),
        Column("sheet_id", String, ForeignKey("sheet_jobs.id")),
        Column("created_at", String, nullable=False),
        Column("state", String, nullable=False),
        Column("origin", String, nullable=False),
        Column("data", JSON, nullable=False),
    )
    for table, fields in {
        "print_jobs": ["created_at", "state"],
        "sheet_jobs": ["job_id", "state"],
        "job_events": ["job_id"],
    }.items():
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])


def downgrade():
    raise RuntimeError(
        "Destructive downgrade is unsupported; restore a verified backup"
    )
