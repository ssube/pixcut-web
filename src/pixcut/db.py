import os
from pathlib import Path
import ctypes

# Load the pinned shared object before Python imports its sqlite extension.
# The matching SONAME ensures _sqlite3 resolves this library on Linux.
_runtime = Path(__file__).resolve().parents[2] / ".runtime" / "libsqlite3.so.0"
if _runtime.exists():
    ctypes.CDLL(str(_runtime), mode=ctypes.RTLD_GLOBAL)
import sqlite3
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Integer,
    ForeignKey,
    UniqueConstraint,
    JSON,
    create_engine,
    event,
)

metadata = MetaData()
artifacts = Table(
    "artifacts",
    metadata,
    Column("hash", String, primary_key=True),
    Column("mime", String, nullable=False),
    Column("size", Integer, nullable=False),
)
assets = Table(
    "assets",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
stickers = Table(
    "stickers",
    metadata,
    Column("id", String, primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("data", JSON, nullable=False),
)
projects = Table(
    "projects",
    metadata,
    Column("id", String, primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("data", JSON, nullable=False),
)
renders = Table(
    "renders",
    metadata,
    Column("id", String, primary_key=True),
    Column("project_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("data", JSON, nullable=False),
)
render_files = Table(
    "render_files",
    metadata,
    Column("render_id", ForeignKey("renders.id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("hash", ForeignKey("artifacts.hash"), nullable=False),
)
jobs = Table(
    "print_jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("created_at", String, nullable=False, index=True),
    Column("name", String, nullable=False),
    Column("render_id", ForeignKey("renders.id"), nullable=False),
    Column("reprint_of_job_id", ForeignKey("print_jobs.id")),
    Column("idempotency_key", String, unique=True, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("execution_mode", String, nullable=False),
    Column("state", String, nullable=False, index=True),
    Column("request", JSON, nullable=False),
)
sheets = Table(
    "sheet_jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("job_id", ForeignKey("print_jobs.id"), nullable=False, index=True),
    Column("ordinal", Integer, nullable=False),
    Column("page", Integer, nullable=False),
    Column("copy", Integer, nullable=False),
    Column("state", String, nullable=False, index=True),
    Column("evidence", JSON),
    UniqueConstraint("job_id", "ordinal"),
)
attempts = Table(
    "device_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("sheet_id", ForeignKey("sheet_jobs.id"), unique=True, nullable=False),
    Column("created_at", String, nullable=False),
    Column("device_job_id", String),
)
events = Table(
    "job_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("job_id", ForeignKey("print_jobs.id"), nullable=False, index=True),
    Column("sheet_id", ForeignKey("sheet_jobs.id")),
    Column("created_at", String, nullable=False),
    Column("state", String, nullable=False),
    Column("origin", String, nullable=False),
    Column("data", JSON, nullable=False),
)
worker_status = Table(
    "printer_worker_status",
    metadata,
    Column("id", String, primary_key=True),
    Column("execution_mode", String, nullable=False),
    Column("state", String, nullable=False),
    Column("current_job_id", ForeignKey("print_jobs.id")),
    Column("last_seen_at", String, nullable=False),
    Column("message", String),
)
operations = Table(
    "operations",
    metadata,
    Column("id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)


def data_dir():
    return Path(os.environ.get("PIXCUT_DATA_DIR", ".pixcut")).resolve()


def engine_for(root: Path):
    if sqlite3.sqlite_version_info < (3, 51, 3):
        raise RuntimeError(
            f"SQLite >= 3.51.3 required; loaded {sqlite3.sqlite_version}"
        )
    root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{root / 'studio.sqlite3'}",
        module=sqlite3,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        cursor = connection.cursor()
        for pragma in (
            "journal_mode=WAL",
            "synchronous=FULL",
            "foreign_keys=ON",
            "busy_timeout=5000",
        ):
            cursor.execute(f"PRAGMA {pragma}")
        for key, expected in (
            ("journal_mode", "wal"),
            ("synchronous", 2),
            ("foreign_keys", 1),
            ("busy_timeout", 5000),
        ):
            if cursor.execute(f"PRAGMA {key}").fetchone()[0] != expected:
                raise RuntimeError(f"Failed to set SQLite {key}")
        cursor.close()

    return engine
