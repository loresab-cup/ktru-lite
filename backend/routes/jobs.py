"""Specification upload, job lists, progress, cancellation and row lists."""

import uuid
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import (
    case,
    delete,
    func,
    or_,
    select,
    text,
    update,
)

import access
import database as db
import job_queue
import parser
import privacy
import runtime as rt
from auth import User
from job_store import upload_path
from models import Decision, Item, Job, now
from routes.common import get_or_404, like_pattern, to_dict

router = APIRouter()


@router.post("/api/jobs", status_code=202)
def create_job(
    file: Annotated[UploadFile, File()], include_inactive: Annotated[bool, Form()] = False
):
    # A plain def runs in the threadpool: reading, writing and the commit do not block the loop
    owner = access.owner_id()
    if rt.AUTH_REQUIRED:
        with db.SessionLocal() as session:
            privacy.require_processing(session, owner)
    filename = Path(file.filename or "file").name[:200]
    data = file.file.read(parser.MAX_FILE_BYTES + 1)
    if len(data) > parser.MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ")
    if not data:
        raise HTTPException(400, "Файл пуст")
    try:
        parser.check_signature(Path(filename).suffix.lower(), data[:8])
    except parser.ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = Job(
        id=uuid.uuid4(),
        filename=filename,
        include_inactive=include_inactive,
        owner_id=owner,
    )
    path = upload_path(job)
    path.write_bytes(data)  # the name on disk is the uuid, not the user's name
    try:
        with db.SessionLocal() as session:
            if rt.AUTH_REQUIRED:
                if session.scalar(select(User).where(User.id == owner).with_for_update()) is None:
                    raise HTTPException(401, "Войдите в систему")
                privacy.require_processing(session, owner)
            session.add(job)
            session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    job_queue.publish(job.id)
    return {"job_id": str(job.id)}


JOB_ORDER = {
    "new": (Job.created_at.desc(),),
    "old": (Job.created_at.asc(),),
    "name": (func.lower(Job.filename).asc(), Job.created_at.desc()),
    "name_desc": (func.lower(Job.filename).desc(), Job.created_at.desc()),
}


@router.get("/api/jobs")
def list_jobs(
    response: Response,
    limit: int = Query(20, ge=1, le=1000),
    q: str = Query("", max_length=200),
    sort: Literal["new", "old", "name", "name_desc"] = "new",
):
    """Jobs by file name (case does not matter), newest first or as asked."""
    found = select(Job).where(access.job_scope())
    if q.strip():
        found = found.where(Job.filename.ilike(like_pattern(q.strip()), escape="\\"))
    with db.SessionLocal() as session:
        # The page shows "N in the list", so the real number of matching jobs goes in a header
        response.headers["X-Total-Count"] = str(
            session.scalar(select(func.count()).select_from(found.subquery()))
        )
        jobs = session.scalars(found.order_by(*JOB_ORDER[sort]).limit(limit)).all()
        # One query for all the jobs: the page of the jobs shows how much of each is ready
        rows = session.execute(
            select(Item.job_id, Item.status, func.count())
            .where(Item.job_id.in_([job.id for job in jobs]))
            .group_by(Item.job_id, Item.status)
        )
        counts: dict = {job.id: {status: 0 for status in rt.ITEM_STATUSES} for job in jobs}
        for job_id, status, count in rows:
            counts[job_id][status] = count
        return [to_dict(job) | {"counts": counts[job.id]} for job in jobs]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        rows = session.execute(
            select(Item.status, func.count()).where(Item.job_id == job.id).group_by(Item.status)
        )
        counts = {status: 0 for status in rt.ITEM_STATUSES} | dict(rows.all())

        def flagged(key: str) -> int:
            return session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.job_id == job.id, Item.attributes[key].as_string().is_not(None))
            )

        # fallback: the network did not give the attributes; words_only: no vectors
        return to_dict(job) | {
            "counts": counts,
            "fallback": flagged("fallback"),
            "words_only": flagged("words_only"),
        }


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    """Removes the job with its rows and the uploaded file; a running job must be stopped first."""
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if session.get_bind().dialect.name == "postgresql":
            unlocked = session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": job.id.int % (2**63)}
            )
            if not unlocked:
                raise HTTPException(409, "Обработчик ещё останавливается: повторите удаление позже")
        if job.status == "cancelled" and job.id in rt.state.cancels:
            # stopped by the user a moment ago: the worker finishes its batch first
            raise HTTPException(409, "Задание останавливается: удалите его через несколько секунд")
        if job.status == "processing" or job.id in rt.state.cancels:
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.execute(delete(Item).where(Item.job_id == job.id))
        session.execute(delete(Decision).where(Decision.job_id == job.id))
        # A worker may take a queued job right now: then nothing is deleted and rows come back
        gone = session.execute(
            delete(Job).where(Job.id == job.id, Job.status != "processing")
        ).rowcount
        if not gone:
            session.rollback()
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.commit()
    upload_path(job).unlink(missing_ok=True)


STATUS_ORDER = (
    "needs_review",
    "failed",
    "auto",
    "corrected",
    "accepted",
)  # what needs a look first


def order_by(sort: str, order: str) -> list:
    """ORDER BY of the list: by code, confidence or status; the row id keeps the ties stable."""
    if sort == "code":
        key = Item.ktru_code
    elif sort == "confidence":
        key = Item.confidence
    elif sort == "status":
        key = case({s: i for i, s in enumerate(STATUS_ORDER)}, value=Item.status, else_=9)
    else:
        return [Item.id]
    return [(key.desc() if order == "desc" else key.asc()).nulls_last(), Item.id]


@router.get("/api/jobs/{job_id}/items")
def list_items(
    job_id: str,
    status: str = "",
    q: str = Query("", max_length=200),
    sort: str = Query("", pattern="^(code|confidence|status)?$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    candidates: bool = True,  # the list of cards does not show them: ~10 KB less per row
):
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        query = select(Item).where(Item.job_id == job.id)
        if status:
            query = query.where(Item.status.in_(status.split(",")))
        if q.strip():
            pattern = like_pattern(q.strip())
            query = query.where(
                or_(
                    Item.raw_text.ilike(pattern, escape="\\"),
                    Item.ktru_code.ilike(pattern, escape="\\"),
                    Item.ktru_name.ilike(pattern, escape="\\"),
                )
            )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(query.order_by(*order_by(sort, order)).limit(limit).offset(offset))
        rows = [to_dict(item) for item in items]
        if not candidates:
            for row in rows:
                del row["candidates"]
        return {"total": total, "items": rows}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Stops a queued or running job; finished rows stay for editing and export."""
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        was = job.status
        session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status.in_(("queued", "processing")))
            .values(status="cancelled", finished_at=now())
        )
        session.commit()
        session.refresh(job)
    if was == "queued":
        upload_path(job).unlink(missing_ok=True)  # no worker will take it any more
    event = rt.state.cancels.get(job.id)
    if event is not None:
        event.set()
    return to_dict(job)
