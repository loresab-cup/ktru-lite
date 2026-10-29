"""Celery app_lifecycle with Redis delivery, duplicate protection and crash recovery."""

import os
import threading
import uuid

from celery import Celery
from celery.signals import worker_init, worker_ready
from sqlalchemy import and_, or_, select, text
from sqlalchemy.exc import OperationalError

import database as db
import runtime as rt
from models import Job, now

app = Celery("ktru", broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"))
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_timeout=2,
    accept_content=["json"],
    task_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 900},
    beat_schedule={
        "recover-queued-jobs": {"task": "ktru.dispatch", "schedule": 15.0},
        "clean-finished-jobs": {"task": "ktru.cleanup", "schedule": 3600.0},
    },
)
_catalog_lock = threading.Lock()


@worker_init.connect
def prepare_worker(**kwargs):
    """A worker must load its catalog before it can report itself ready."""
    from app_lifecycle import load_catalog

    load_catalog()
    if rt.state.catalog is None:
        raise SystemExit("Worker catalog failed to load")


def ensure_catalog() -> None:
    from app_lifecycle import load_catalog

    with _catalog_lock:
        if rt.state.catalog is None:
            load_catalog()
        if rt.state.catalog is None:
            raise RuntimeError("Catalog is not ready")


def watch_cancel(job_id: uuid.UUID, stop: threading.Event) -> None:
    """The cancel button writes the status in the API process; this worker sees it here."""
    from job_store import cancelled

    while not stop.wait(0.5):
        if cancelled(job_id):
            event = rt.state.cancels.get(job_id)
            if event:
                event.set()
            return


def run_watched(job_id: uuid.UUID) -> None:
    from spec_processing import run_job

    stop = threading.Event()
    watcher = threading.Thread(target=watch_cancel, args=(job_id, stop), daemon=True)
    watcher.start()
    try:
        run_job(job_id)
    finally:
        stop.set()
        watcher.join(timeout=2)


@app.task(
    name="ktru.process",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def process_job(job_id):
    from job_store import claim_job, lock_key

    job_id = uuid.UUID(str(job_id))
    postgres = db.engine.dialect.name == "postgresql"
    with db.engine.connect() as guard:
        # One worker per job: a redelivered message finds the lock taken and does nothing
        lock = {"key": lock_key(job_id)}
        if postgres and not guard.scalar(text("SELECT pg_try_advisory_lock(:key)"), lock):
            return
        try:
            ensure_catalog()
            if claim_job(job_id, recover=True):
                run_watched(job_id)
        finally:
            if postgres:
                guard.execute(text("SELECT pg_advisory_unlock(:key)"), lock)


@app.task(name="ktru.dispatch")
def dispatch(recover=False):
    from datetime import timedelta

    from job_queue import publish

    pending = (
        Job.status.in_(("queued", "processing"))
        if recover
        else or_(
            Job.status == "queued",
            and_(Job.status == "processing", Job.started_at < now() - timedelta(seconds=900)),
        )
    )
    with db.SessionLocal() as session:
        ids = list(session.scalars(select(Job.id).where(pending)))
    for job_id in ids:
        publish(job_id)


@app.task(name="ktru.cleanup")
def cleanup():
    from app_lifecycle import clean_guests, clean_old_jobs

    return {"jobs": clean_old_jobs(), "guests": clean_guests()}


@worker_ready.connect
def recover_jobs(**kwargs):
    dispatch.delay(True)
