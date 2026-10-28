"""Celery workers with Redis delivery, duplicate protection and crash recovery."""

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
    from workers import load_catalog

    load_catalog()
    if rt.state.catalog is None:
        raise SystemExit("Worker catalog failed to load")


@app.task(
    name="ktru.process",
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def process_job(job_id):
    from pipeline import run_job
    from workers import load_catalog

    job_id = uuid.UUID(str(job_id))
    with db.engine.connect() as guard:
        locked = True
        if db.engine.dialect.name == "postgresql":
            locked = guard.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": job_id.int % (2**63)}
            )
        if not locked:
            return
        try:
            with _catalog_lock:
                if rt.state.catalog is None:
                    load_catalog()
                if rt.state.catalog is None:
                    raise RuntimeError("Catalog is not ready")
            from job_store import claim_job

            claimed = claim_job(job_id, recover=True)
            if claimed:
                stop = threading.Event()

                def watch_cancel():
                    from job_store import cancelled

                    while not stop.wait(0.5):
                        if cancelled(job_id):
                            event = rt.state.cancels.get(job_id)
                            if event:
                                event.set()
                            return

                watcher = threading.Thread(target=watch_cancel, daemon=True)
                watcher.start()
                try:
                    run_job(job_id)
                finally:
                    stop.set()
                    watcher.join(timeout=2)
        finally:
            if db.engine.dialect.name == "postgresql":
                guard.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": job_id.int % (2**63)}
                )


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
    from workers import clean_old_jobs

    return clean_old_jobs()


@worker_ready.connect
def recover_jobs(**kwargs):
    dispatch.delay(True)
