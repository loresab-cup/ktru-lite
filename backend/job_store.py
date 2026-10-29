"""Database operations for queue claims, progress and completion."""

import uuid
from pathlib import Path

from sqlalchemy import (
    select,
    update,
)

import database as db
import runtime as rt
from models import Job, now


def lock_key(job_id: uuid.UUID) -> int:
    """The PostgreSQL advisory lock of a job: the worker holds it while it processes the job."""
    return job_id.int % 2**63


def upload_path(job: Job) -> Path:
    return rt.UPLOAD_DIR / f"{job.id}{Path(job.filename).suffix.lower()}"


def claim_job(job_id: uuid.UUID | None = None, recover: bool = False) -> uuid.UUID | None:
    """Atomically claim a queued job; recover requires the caller's per-job advisory lock."""
    statuses = ("queued", "processing") if recover else ("queued",)
    with db.SessionLocal() as session:
        query = (
            select(Job.id)
            .where(Job.status.in_(statuses))
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job_id is not None:
            query = query.where(Job.id == job_id)
        job_id = session.scalars(query).first()
        if job_id is None:
            return None
        if rt.AUTH_REQUIRED:
            import privacy

            job = session.get(Job, job_id)
            consent = privacy.state(session, job.owner_id) if job.owner_id is not None else {}
            if not (consent.get("account") and consent.get("files")):
                job.status, job.finished_at = "cancelled", now()
                job.error = "Для обработки требуется согласие владельца файлов"
                session.commit()
                upload_path(job).unlink(missing_ok=True)
                return None
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(statuses))
            .values(status="processing", started_at=now(), processed=0, total=0, error=None)
        ).rowcount
        session.commit()
        return job_id if claimed else None


def set_job(job_id: uuid.UUID, **values) -> None:
    with db.SessionLocal() as session:
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def finish(job_id: uuid.UUID, status: str, error: str | None, stages: dict) -> None:
    """Final status; a job the user cancelled meanwhile stays cancelled."""
    values = {"finished_at": now(), "stage_seconds": dict(stages)}
    with db.SessionLocal() as session:
        query = update(Job).where(Job.id == job_id)
        if status != "cancelled":
            query = query.where(Job.status == "processing")
        # The error goes only with its status: a job cancelled meanwhile shows no failure text
        session.execute(query.values(status=status, error=error, **values))
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def cancelled(job_id: uuid.UUID) -> bool:
    with db.SessionLocal() as session:
        status = session.scalar(select(Job.status).where(Job.id == job_id))
        return status is None or status == "cancelled"
