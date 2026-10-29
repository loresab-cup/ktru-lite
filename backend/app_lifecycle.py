"""Background workers, retention cleanup and application startup."""

import logging
import os
import time
from datetime import timedelta

from sqlalchemy import (
    delete,
    select,
)
from sqlalchemy.exc import OperationalError

import auth
import database as db
import embedder
import job_store
import runtime as rt
import search
from models import Decision, Item, Job, now

logger = logging.getLogger("app_lifecycle")


def clean_old_jobs() -> int:
    """Deletes finished jobs older than rt.KEEP_DAYS with their rows and files; the decisions of
    people stay, they are the memory for later files. Returns how many jobs went."""
    if rt.KEEP_DAYS <= 0:
        return 0
    border = now() - timedelta(days=rt.KEEP_DAYS)
    with db.SessionLocal() as session:
        old = session.scalars(
            select(Job).where(
                Job.status.in_(("done", "failed", "cancelled")), Job.created_at < border
            )
        ).all()
        ids = [job.id for job in old]
        if not ids:
            return 0
        session.execute(delete(Item).where(Item.job_id.in_(ids)))
        session.execute(delete(Job).where(Job.id.in_(ids), Job.status != "processing"))
        session.commit()
    for job in old:
        job_store.upload_path(job).unlink(missing_ok=True)
    logger.info("deleted %s jobs older than %s days", len(ids), rt.KEEP_DAYS)
    return len(ids)


def clean_guests() -> int:
    """Removes guests without a live session (logged out, or rt.GUEST_HOURS are over; a closed
    browser has lost the cookie already) with their jobs, rows, decisions and files. A guest
    whose job is being processed waits for the next run. Returns how many guests went."""
    gone = 0
    with db.SessionLocal() as session:
        live = select(auth.LoginSession.user_id).where(auth.LoginSession.expires_at >= now())
        guests = session.scalars(
            select(auth.User).where(auth.User.is_guest, auth.User.id.not_in(live))
        ).all()
        for guest in guests:
            # Adoption locks this row too: never delete files while they are being moved.
            guest = session.scalar(
                select(auth.User)
                .where(auth.User.id == guest.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if guest is None or not guest.is_guest:
                continue
            if session.scalar(
                select(auth.LoginSession.token_hash)
                .where(
                    auth.LoginSession.user_id == guest.id,
                    auth.LoginSession.expires_at >= now(),
                )
                .limit(1)
            ):
                continue
            jobs = session.scalars(select(Job).where(Job.owner_id == guest.id)).all()
            if any(job.status == "processing" for job in jobs):
                continue
            ids = [job.id for job in jobs]
            session.execute(delete(Item).where(Item.job_id.in_(ids)))
            session.execute(delete(Decision).where(Decision.job_id.in_(ids)))
            session.execute(delete(Decision).where(Decision.owner_id == guest.id))
            removed = session.execute(
                delete(Job).where(Job.id.in_(ids), Job.status != "processing")
            ).rowcount
            if removed != len(ids):  # a worker took a queued job just now
                session.rollback()
                continue
            session.execute(delete(auth.LoginSession).where(auth.LoginSession.user_id == guest.id))
            session.delete(guest)
            session.commit()
            for job in jobs:
                job_store.upload_path(job).unlink(missing_ok=True)
            gone += 1
    if gone:
        logger.info("removed %s guests whose session is over", gone)
    return gone


def load_catalog() -> None:
    try:
        catalog = search.load_catalog()
        if catalog.vectors.shape[1] != embedder.DIM:
            raise ValueError(
                f"catalog vectors have {catalog.vectors.shape[1]} numbers, the Yandex embeddings "
                f"{embedder.DIM}: the file belongs to another model"
            )
        if db.engine.dialect.name == "postgresql":
            from vector_store import VectorStore

            catalog.vector_store = VectorStore(catalog)
            catalog.vector_store.sync()
        rt.state.catalog = catalog
        rt.state.catalog_error = None
    except Exception as exc:
        logger.exception("catalog is not loaded")
        rt.state.catalog_error = str(exc)[:300] or type(exc).__name__


def load_catalog_when_available(stop) -> None:
    """Recover after the missing catalog files have been supplied, without restarting API."""
    while not stop.is_set():
        load_catalog()
        if rt.state.catalog is not None or stop.wait(15):
            return


def init_db() -> None:
    for attempt in range(30):
        try:
            from migrate import upgrade_database

            upgrade_database(db.engine)
            break
        except OperationalError:
            if attempt == 29:
                raise
            logger.info("database is not ready, waiting")
            time.sleep(2)
    from demo import seed_on_startup

    seed_on_startup()
    with db.SessionLocal() as session:
        admin = os.getenv("ADMIN_LOGIN", "admin")
        try:
            if auth.ensure_admin(session, admin, os.getenv("ADMIN_PASSWORD", "")):
                logger.info("the first admin %s is created from ADMIN_LOGIN", admin)
        except ValueError as exc:  # a short password: the site stays closed, the log says why
            logger.error("ADMIN_LOGIN/ADMIN_PASSWORD are not accepted: %s", exc)
