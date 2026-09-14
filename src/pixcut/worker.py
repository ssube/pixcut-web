"""Exclusive simulator worker. No USB dependencies or physical side effects."""

import fcntl
import time
from contextlib import contextmanager
from sqlalchemy import select, update
from . import db
from .service import uid, now


@contextmanager
def worker_lock(root):
    with (root / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another printer worker owns this data directory"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class SimulatorWorker:
    def __init__(self, service):
        self.service = service

    def recover(self):
        with self.service.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            active = (
                c.execute(
                    select(db.sheets)
                    .join(db.jobs)
                    .where(
                        db.jobs.c.execution_mode == "simulator",
                        db.sheets.c.state.in_(
                            ["creating", "transferring", "processing"]
                        ),
                    )
                )
                .mappings()
                .all()
            )
            for sheet in active:
                self.transition(
                    c, sheet, "uncertain", {"reason": "worker_restart_after_intent"}
                )

    def transition(self, c, sheet, state, evidence=None):
        evidence = {"origin": "simulator", **(evidence or {})}
        c.execute(
            update(db.sheets)
            .where(db.sheets.c.id == sheet["id"])
            .values(state=state, evidence=evidence)
        )
        self.service.event(c, sheet["job_id"], state, sheet["id"], data=evidence)
        self.service.aggregate(c, sheet["job_id"])

    def run_once(self, fault=None):
        with self.service.engine.begin() as c:
            c.exec_driver_sql("BEGIN IMMEDIATE")
            # Hold the entire dispatcher until ambiguity is explicitly resolved.
            if c.execute(
                select(db.sheets.c.id).where(db.sheets.c.state == "uncertain").limit(1)
            ).first():
                return False
            sheet = (
                c.execute(
                    select(db.sheets)
                    .join(db.jobs)
                    .where(
                        db.sheets.c.state == "queued",
                        db.jobs.c.execution_mode == "simulator",
                    )
                    .order_by(db.jobs.c.created_at, db.jobs.c.id, db.sheets.c.ordinal)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if not sheet:
                return False
            sheet = dict(sheet)
            attempt_id = uid()
            c.execute(
                db.attempts.insert().values(
                    id=attempt_id, sheet_id=sheet["id"], created_at=now()
                )
            )
            self.transition(
                c,
                sheet,
                "creating",
                {"attempt_id": attempt_id, "checkpoint": "create_intent"},
            )
        # No retry block encloses the simulated non-idempotent operation.
        if fault == "crash_after_intent":
            raise RuntimeError(
                "Injected process interruption after durable create intent"
            )
        try:
            job = self.service.get(db.jobs, sheet["job_id"])
            self.service.verify_render(job["render_id"])
            if fault == "create_rejected":
                with self.service.engine.begin() as c:
                    self.transition(c, sheet, "failed", {"reason": "create_rejected"})
                return True
            device_id = "sim-" + uid()
            with self.service.engine.begin() as c:
                c.execute(
                    update(db.attempts)
                    .where(db.attempts.c.id == attempt_id)
                    .values(device_job_id=device_id)
                )
                self.transition(c, sheet, "transferring", {"device_job_id": device_id})
            if fault == "missing_ack":
                raise RuntimeError("Missing simulated transfer acknowledgement")
            with self.service.engine.begin() as c:
                self.transition(c, sheet, "processing")
            if fault == "status_loss":
                raise RuntimeError("Lost simulated completion status")
            with self.service.engine.begin() as c:
                self.transition(
                    c,
                    sheet,
                    "completed",
                    {"completion": "simulated", "physical_output_verified": False},
                )
        except Exception as exc:
            with self.service.engine.begin() as c:
                self.transition(c, sheet, "uncertain", {"reason": str(exc)[:2000]})
        return True

    def run(self, once=False):
        self.service.worker_heartbeat("simulator", "starting")
        self.recover()
        last_heartbeat = 0.0
        try:
            while True:
                current = time.monotonic()
                if current - last_heartbeat >= 2:
                    with self.service.engine.connect() as c:
                        blocked = c.execute(
                            select(db.sheets.c.id)
                            .join(db.jobs)
                            .where(
                                db.jobs.c.execution_mode == "simulator",
                                db.sheets.c.state == "uncertain",
                            )
                            .limit(1)
                        ).scalar()
                    self.service.worker_heartbeat(
                        "simulator",
                        "blocked" if blocked else "idle",
                        message=(
                            "Resolve the uncertain preview before continuing"
                            if blocked
                            else None
                        ),
                    )
                    last_heartbeat = current
                worked = self.run_once()
                if once:
                    return
                if not worked:
                    time.sleep(0.5)
        finally:
            self.service.worker_heartbeat("simulator", "stopped")
