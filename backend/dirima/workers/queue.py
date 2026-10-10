"""PostgreSQL SKIP LOCKED queue with leases and at-least-once handling."""

from __future__ import annotations

import os
import socket
import threading
import time
from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dirima.db import Session as SessionFactory
from dirima.db import engine
from dirima.infrastructure.logging import configure
from dirima.models import Job, WorkerHeartbeat, now
from dirima.reduction.pipeline import alert, reduce_task

LEASE_SECONDS = 90
MAX_ATTEMPTS = 4
logger = configure("worker")


def job_task_id(job: Job) -> UUID | None:
    try:
        return UUID(str(job.payload.get("task_id")))
    except ValueError:
        return None


def claim(db: Session, worker_id: str) -> Job | None:
    cutoff = now() - timedelta(seconds=LEASE_SECONDS)
    job = db.scalar(
        select(Job)
        .where(
            or_(
                (Job.status.in_(["READY", "FAILED"])) & (Job.available_at <= now()),
                (Job.status == "RUNNING") & (Job.locked_at < cutoff),
            )
        )
        .order_by(Job.available_at, Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job:
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.locked_by, job.locked_at = "DEAD", None, None
            alert(
                db,
                job.workspace_id,
                job_task_id(job),
                "REDUCTION_FAILED",
                "Job recovery exhausted retry limit",
                job.last_error or "Worker stopped before completing the job",
            )
            db.flush()
            return job
        job.status, job.locked_by, job.locked_at = "RUNNING", worker_id, now()
        job.attempts += 1
        db.flush()
    return job


def work_once(worker_id: str | None = None) -> bool:
    worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    with SessionFactory(bind=engine()) as db:
        heartbeat = db.get(WorkerHeartbeat, worker_id)
        if heartbeat:
            heartbeat.seen_at = now()
        else:
            db.add(WorkerHeartbeat(worker_id=worker_id, seen_at=now()))
        job = claim(db, worker_id)
        job_id = job.id if job else None
        db.commit()
    if not job_id:
        return False
    started = time.perf_counter()
    try:
        with SessionFactory(bind=engine()) as db:
            active = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if not active or active.status != "RUNNING" or active.locked_by != worker_id:
                return True
            if active.job_type == "reduce_task":
                complete = reduce_task(db, UUID(active.payload["task_id"]))
            else:
                raise ValueError("Unknown job type")
            active.status, active.completed_at = "COMPLETED" if complete else "DEAD", now()
            active.last_error = None if complete else "ReductionIncomplete"
            active.duration_seconds = time.perf_counter() - started
            active.locked_by, active.locked_at = None, None
            db.commit()
            logger.info(
                "job completed" if complete else "reduction incomplete",
                extra={
                    "service": "worker",
                    "event": "job.completed" if complete else "job.dead",
                    "trace_id": str(job_id),
                    "workspace_id": str(active.workspace_id),
                    "task_id": active.payload.get("task_id"),
                },
            )
    except Exception as exc:
        with SessionFactory(bind=engine()) as db:
            failed = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if failed and failed.status == "RUNNING" and failed.locked_by == worker_id:
                failed.last_error = type(exc).__name__
                failed.locked_by, failed.locked_at = None, None
                failed.status = "DEAD" if failed.attempts >= MAX_ATTEMPTS else "FAILED"
                failed.available_at = now() + timedelta(seconds=min(60, 2**failed.attempts))
                if failed.status == "DEAD":
                    alert(
                        db,
                        failed.workspace_id,
                        job_task_id(failed),
                        "REDUCTION_FAILED",
                        "Reduction failed after retries",
                        type(exc).__name__,
                    )
                db.commit()
                logger.error(
                    "job failed",
                    extra={
                        "service": "worker",
                        "event": "job.failed",
                        "trace_id": str(job_id),
                        "workspace_id": str(failed.workspace_id),
                        "task_id": failed.payload.get("task_id"),
                    },
                )
    return True


def run_forever() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    stopped = threading.Event()

    def heartbeat_loop() -> None:
        while not stopped.wait(5):
            try:
                with SessionFactory(bind=engine()) as db:
                    row = db.get(WorkerHeartbeat, worker_id)
                    if row:
                        row.seen_at = now()
                    else:
                        db.add(WorkerHeartbeat(worker_id=worker_id, seen_at=now()))
                    db.commit()
            except SQLAlchemyError:
                logger.error("heartbeat database unavailable", extra={"event": "heartbeat.failed"})

    thread = threading.Thread(target=heartbeat_loop, daemon=True)
    thread.start()
    try:
        while True:
            try:
                if not work_once(worker_id):
                    time.sleep(1)
            except SQLAlchemyError:
                logger.error("queue database unavailable", extra={"event": "queue.unavailable"})
                time.sleep(5)
    finally:
        stopped.set()
        thread.join(timeout=6)
