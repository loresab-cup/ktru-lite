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
from models import Item, Job, now

logger = logging.getLogger("workers")


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
    except Exception as exc:
        logger.exception("catalog is not loaded")
        rt.state.catalog_error = str(exc)[:300] or type(exc).__name__


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
    with db.SessionLocal() as session:
        admin = os.getenv("ADMIN_LOGIN", "admin")
        try:
            if auth.ensure_admin(session, admin, os.getenv("ADMIN_PASSWORD", "")):
                logger.info("the first admin %s is created from ADMIN_LOGIN", admin)
        except ValueError as exc:  # a short password: the site stays closed, the log says why
            logger.error("ADMIN_LOGIN/ADMIN_PASSWORD are not accepted: %s", exc)
