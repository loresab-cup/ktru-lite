"""HTTP API, the job queue and the export.

Queue = the jobs table: WORKERS background threads take a queued job with
SELECT ... FOR UPDATE SKIP LOCKED and process it in batches of BATCH rows, updating `processed`.
Pipeline of a batch: extractor (LLM or rules) -> embedder (bge-m3) -> search -> confidence.
"""

import csv
import io
import logging
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    case,
    create_engine,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, defer, mapped_column, sessionmaker

import chooser
import confidence
import embedder
import extractor
import parser
import search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://ktru:ktru@db:5432/ktru")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
WORKERS = int(os.getenv("WORKERS", "2"))
JOB_TIME_LIMIT = 270.0  # seconds: 1000 rows must be done within 5 minutes, with a margin
BATCH = 20  # small batches: finished rows reach the table while the rest are processed
POLL_SECONDS = 1.0
ITEM_STATUSES = ("auto", "needs_review", "corrected", "accepted", "failed")


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    total: Mapped[int] = mapped_column(default=0)
    processed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    include_inactive: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage_seconds: Mapped[dict] = mapped_column(JSON, default=dict)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    ktru_code: Mapped[str | None] = mapped_column(String(32))
    ktru_name: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None]
    zone: Mapped[str] = mapped_column(String(8), default="low")
    status: Mapped[str] = mapped_column(String(16), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")


sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if sqlite else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class State:
    catalog: search.Catalog | None = None
    catalog_error: bool = False
    stop = threading.Event()

    def __init__(self) -> None:
        self.cancels: dict[uuid.UUID, threading.Event] = {}  # running jobs -> "stop" flag


state = State()

# --- queue ---------------------------------------------------------------------------------


def upload_path(job: Job) -> Path:
    return UPLOAD_DIR / f"{job.id}{Path(job.filename).suffix.lower()}"


def claim_job() -> uuid.UUID | None:
    with SessionLocal() as session:
        query = (
            select(Job.id)
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job_id = session.scalars(query).first()
        if job_id is None:
            return None
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="processing", started_at=now(), processed=0, total=0, error=None)
        ).rowcount
        session.commit()
        return job_id if claimed else None


def set_job(job_id: uuid.UUID, **values) -> None:
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def finish(job_id: uuid.UUID, status: str, error: str | None, stages: dict) -> None:
    """Final status; a job the user cancelled meanwhile stays cancelled."""
    values = {"finished_at": now(), "stage_seconds": dict(stages)}
    with SessionLocal() as session:
        query = update(Job).where(Job.id == job_id)
        if status != "cancelled":
            query = query.where(Job.status == "processing")
        # The error goes only with its status: a job cancelled meanwhile shows no failure text
        session.execute(query.values(status=status, error=error, **values))
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def cancelled(job_id: uuid.UUID) -> bool:
    with SessionLocal() as session:
        return session.scalar(select(Job.status).where(Job.id == job_id)) == "cancelled"


def tick(stages: dict, stage: str, started: float) -> None:
    stages[stage] = round(stages.get(stage, 0.0) + time.monotonic() - started, 2)


def apply_candidates(item: Item, found: list, is_product: bool = True) -> None:
    """Top-5 -> the best code, confidence, zone, status and explanation of the item."""
    verdict = confidence.assess(found, is_product=is_product)
    best = found[0] if found else {}
    item.candidates = found
    item.ktru_code, item.ktru_name = best.get("code"), best.get("name")
    item.confidence, item.zone = verdict["confidence"], verdict["zone"]
    item.status, item.summary = verdict["status"], verdict["summary"]


def build_item(job_id: uuid.UUID, row: parser.Item, attributes: dict, found: list) -> Item:
    item = Item(
        job_id=job_id,
        row_number=row.row_number,
        raw_text=row.text,
        attributes={**attributes, "columns": row.columns},
    )
    try:
        apply_candidates(item, found, is_product=bool(attributes.get("name")))
    except Exception as exc:  # one broken row must not stop the job
        logger.exception("item failed")
        item.status, item.summary = "failed", f"Ошибка обработки позиции: {type(exc).__name__}"
    return item


def unit_hints(row: parser.Item, attributes: dict) -> tuple[str | None, str | None]:
    """Units of the row: the one of the «Ед. изм.» column and the one the network says it is
    bought in. The column is the customer's word and counts more."""
    column = None
    for title, value in row.columns.items():
        column = search.unit_key(value) if title.lower().startswith("ед") else None
        if column:
            break
    return column, search.unit_key(attributes.get("measure") or "")


def process_items(
    job_id: uuid.UUID, rows: list[parser.Item], include_inactive: bool, stages: dict
) -> None:
    llm = extractor.JobExtractor([row.text for row in rows], cancelled=state.cancels.get(job_id))
    try:
        for start in range(0, len(rows), BATCH):
            if cancelled(job_id):
                raise extractor.Cancelled
            process_batch(job_id, rows[start : start + BATCH], start, llm, include_inactive, stages)
    except extractor.Cancelled:
        llm.cancel()
        raise


def process_batch(
    job_id: uuid.UUID,
    batch: list[parser.Item],
    start: int,
    llm: extractor.JobExtractor,
    include_inactive: bool,
    stages: dict,
) -> None:
    started = time.monotonic()
    attributes = [llm.get(row.text) for row in batch]
    tick(stages, "extract", started)
    started = time.monotonic()
    queries = [f"{a['search_text']}. {row.text}" for a, row in zip(attributes, batch, strict=True)]
    hints = [unit_hints(row, a) for a, row in zip(attributes, batch, strict=True)]
    vectors = embedder.embed(queries)
    if vectors is None:  # Ollama is down: the page warns that these rows were found by words
        attributes = [a | {"words_only": True} for a in attributes]
    found = state.catalog.search(
        vectors,
        queries,
        include_inactive,
        units=[column or guessed for column, guessed in hints],
        column_units=[column for column, _ in hints],
    )
    tick(stages, "search", started)
    started = time.monotonic()
    items = [build_item(job_id, *args) for args in zip(batch, attributes, found, strict=True)]
    tick(stages, "confidence", started)
    with SessionLocal() as session:
        session.add_all(items)
        session.commit()
    set_job(job_id, processed=start + len(batch), stage_seconds=dict(stages))


def choose_codes(job_id: uuid.UUID, stages: dict, until: float) -> None:
    """Second opinion of a neural network for the rows the search was not sure about.

    An item changes only if it is still needs_review with the same code: edits made by the user
    while the job runs are never overwritten. The row stays for review: the confidence is the
    search's."""
    if not chooser.ENABLED or extractor.llm_state() != "on":
        return
    started = time.monotonic()
    with SessionLocal() as session:
        items = session.scalars(
            select(Item).where(Item.job_id == job_id, Item.status == "needs_review")
        ).all()
    rows = [
        {"id": item.id, "text": item.raw_text, "candidates": item.candidates}
        for item in items
        if len(item.candidates) > 1
    ]
    cancelled = state.cancels.get(job_id)
    picks = chooser.choose(rows, cancelled, until)
    with SessionLocal() as session:
        for item in items:
            code, provider = picks.get(item.id, (item.ktru_code, ""))
            if code == item.ktru_code:
                continue
            name = next(c["name"] for c in item.candidates if c["code"] == code)
            summary = f"Код выбран нейросетью ({provider}) из 5 кандидатов поиска. {item.summary}"
            session.execute(
                update(Item)
                .where(
                    Item.id == item.id,
                    Item.ktru_code == item.ktru_code,
                    Item.status == "needs_review",
                )
                .values(ktru_code=code, ktru_name=name, summary=summary[:1000])
            )
        session.commit()
    tick(stages, "choose", started)
    if cancelled is not None and cancelled.is_set():
        raise extractor.Cancelled


def run_job(job_id: uuid.UUID) -> None:
    stages: dict = {}
    started = time.monotonic()
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        path, include_inactive = upload_path(job), job.include_inactive
        session.execute(delete(Item).where(Item.job_id == job_id))  # rerun after a restart
        session.commit()
    state.cancels[job_id] = threading.Event()
    try:
        rows = parser.parse_file(path)
        tick(stages, "read", started)
        set_job(job_id, total=len(rows))
        process_items(job_id, rows, include_inactive, stages)
        choose_codes(job_id, stages, until=started + JOB_TIME_LIMIT)
        tick(stages, "total", started)
        status, error = "done", None
    except extractor.Cancelled:
        tick(stages, "total", started)
        status, error = "cancelled", None
    except parser.ParseError as exc:
        status, error = "failed", str(exc)
    except FileNotFoundError:
        status, error = "failed", "Файл задания не найден: загрузите его заново"
    except Exception:
        logger.exception("job %s failed", job_id)
        status, error = "failed", "Не удалось обработать файл, попробуйте ещё раз"
    # If this write fails (the database restarts), the file stays: after a restart the job is
    # queued again and processed from the start
    finish(job_id, status, error, stages)
    state.cancels.pop(job_id, None)
    path.unlink(missing_ok=True)


def worker_loop() -> None:
    while not state.stop.is_set():
        try:
            job_id = claim_job() if state.catalog is not None else None
            if job_id is not None:
                run_job(job_id)
                continue
        except Exception:  # the database restarts: the worker must survive and retry
            logger.exception("worker error, retrying")
        state.stop.wait(POLL_SECONDS)


def load_catalog() -> None:
    try:
        state.catalog = search.load_catalog()
    except Exception:
        logger.exception("catalog is not loaded")
        state.catalog_error = True


def init_db() -> None:
    for attempt in range(30):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception:
            if attempt == 29:
                raise
            logger.info("database is not ready, waiting")
            time.sleep(2)
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.status == "processing").values(status="queued"))
        session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    state.stop.clear()
    threading.Thread(target=load_catalog, daemon=True).start()
    for n in range(WORKERS):
        threading.Thread(target=worker_loop, name=f"worker-{n}", daemon=True).start()
    yield
    state.stop.set()


app = FastAPI(
    title="КТРУ-классификатор",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": "Неверные параметры запроса"}, status_code=422)


@app.exception_handler(Exception)
async def internal_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("request failed", exc_info=exc)
    return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)


# --- endpoints -----------------------------------------------------------------------------


def to_dict(obj: Job | Item) -> dict:
    values = {column.name: getattr(obj, column.name) for column in obj.__table__.columns}
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in values.items()}


def get_or_404(session: Session, model, key, what: str):
    try:
        obj = session.get(model, uuid.UUID(str(key)) if model is Job else key)
    except ValueError:
        obj = None
    if obj is None:
        raise HTTPException(404, f"{what} не найдено")
    return obj


def require_catalog() -> search.Catalog:
    if state.catalog is None:
        raise HTTPException(503, "Каталог КТРУ ещё загружается, подождите минуту")
    return state.catalog


@app.post("/api/jobs", status_code=202)
def create_job(
    file: Annotated[UploadFile, File()], include_inactive: Annotated[bool, Form()] = False
):
    # A plain def runs in the threadpool: reading, writing and the commit do not block the loop
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
    job = Job(id=uuid.uuid4(), filename=filename, include_inactive=include_inactive)
    path = upload_path(job)
    path.write_bytes(data)  # the name on disk is the uuid, not the user's name
    try:
        with SessionLocal() as session:
            session.add(job)
            session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"job_id": str(job.id)}


@app.get("/api/jobs")
def list_jobs(response: Response, limit: int = Query(20, ge=1, le=1000)):
    with SessionLocal() as session:
        # The page shows "N in the list", so the real number of jobs goes in a header
        response.headers["X-Total-Count"] = str(
            session.scalar(select(func.count()).select_from(Job))
        )
        jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)).all()
        # One query for all the jobs: the page of the jobs shows how much of each is ready
        rows = session.execute(
            select(Item.job_id, Item.status, func.count())
            .where(Item.job_id.in_([job.id for job in jobs]))
            .group_by(Item.job_id, Item.status)
        )
        counts: dict = {job.id: {status: 0 for status in ITEM_STATUSES} for job in jobs}
        for job_id, status, count in rows:
            counts[job_id][status] = count
        return [to_dict(job) | {"counts": counts[job.id]} for job in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        rows = session.execute(
            select(Item.status, func.count()).where(Item.job_id == job.id).group_by(Item.status)
        )
        counts = {status: 0 for status in ITEM_STATUSES} | dict(rows.all())

        def flagged(key: str) -> int:
            return session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.job_id == job.id, Item.attributes[key].as_string().is_not(None))
            )

        # fallback: the network did not give the attributes; words_only: Ollama was down
        return to_dict(job) | {
            "counts": counts,
            "fallback": flagged("fallback"),
            "words_only": flagged("words_only"),
        }


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    """Removes the job with its rows and the uploaded file; a running job must be stopped first."""
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if job.status == "processing" or job.id in state.cancels:
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.execute(delete(Item).where(Item.job_id == job.id))
        # A worker may take a queued job right now: then nothing is deleted and rows come back
        gone = session.execute(
            delete(Job).where(Job.id == job.id, Job.status != "processing")
        ).rowcount
        if not gone:
            session.rollback()
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.commit()
    upload_path(job).unlink(missing_ok=True)


def like_pattern(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


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


@app.get("/api/jobs/{job_id}/items")
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
    with SessionLocal() as session:
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


ORIGINAL_FIELDS = ("ktru_code", "ktru_name", "confidence", "zone", "status", "summary")


def remember_original(item: Item) -> None:
    """Keeps what the system proposed, once, before a person accepts or changes the code."""
    attributes = item.attributes or {}
    if "original" in attributes or item.status not in ("auto", "needs_review"):
        return
    original = {field: getattr(item, field) for field in ORIGINAL_FIELDS}
    item.attributes = {**attributes, "original": original}  # a new dict: JSON is not tracked


HISTORY_LIMIT = 10  # steps of "return the previous code"


def push_history(item: Item) -> None:
    """Keeps the code the item had before a person changes it, to bring it back later."""
    attributes = item.attributes or {}
    snapshot = {field: getattr(item, field) for field in ORIGINAL_FIELDS}
    history = [*attributes.get("history", []), snapshot][-HISTORY_LIMIT:]
    item.attributes = {**attributes, "history": history}


def restore_original(item: Item) -> None:
    attributes = dict(item.attributes or {})
    attributes.pop("history", None)  # back to the proposal of the system: the steps are gone
    original = attributes.pop("original", None)
    if original is None:  # accepted before the snapshot existed: the code did not change
        verdict = confidence.assess(item.candidates, is_product=bool(attributes.get("name")))
        original = {field: verdict[field] for field in ("confidence", "zone", "status")}
    for field, value in original.items():
        setattr(item, field, value)
    item.attributes = attributes


DIFFERENCES_SHOWN = 4  # characteristics per candidate: enough to tell neighbour codes apart


def with_differences(candidates: list[dict]) -> list[dict]:
    """Each candidate with the characteristics whose values differ between the candidates:
    five «Мыло туалетное твердое» in a row are told apart by «Масса нетто» and the like."""
    if len(candidates) < 2:
        return candidates
    differing = chooser.differing(candidates)
    return [
        c | {"differs": list(d["characteristics"].items())[:DIFFERENCES_SHOWN]}
        for c, d in zip(candidates, differing, strict=True)
    ]


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    """One position with its place in the job: the page of a code walks to the neighbours."""
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        in_job = Item.job_id == item.job_id

        def count(*conditions) -> int:
            return session.scalar(select(func.count()).select_from(Item).where(in_job, *conditions))

        def edge(pick, *conditions) -> int | None:
            return session.scalar(select(pick(Item.id)).where(in_job, *conditions))

        not_reviewed = Item.status.not_in(("accepted", "corrected"))
        shown = to_dict(item)
        shown["candidates"] = with_differences(confidence.with_confidences(item.candidates or []))
        return shown | {
            "position": count(Item.id <= item.id),
            "total": count(),
            "prev_id": edge(func.max, Item.id < item.id),
            "next_id": edge(func.min, Item.id > item.id),
            "next_unreviewed_id": edge(func.min, Item.id > item.id, not_reviewed),
        }


class Correction(BaseModel):
    ktru_code: str = Field(min_length=1, max_length=32)


@app.patch("/api/items/{item_id}")
def correct_item(item_id: int, body: Correction):
    # "10.12.10.000- 00000005" from a spreadsheet: spaces and line breaks are dropped
    code = require_catalog().get(re.sub(r"\s+", "", body.ktru_code))
    if code is None:
        raise HTTPException(400, "Такого кода нет в каталоге КТРУ")
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        remember_original(item)
        push_history(item)
        item.ktru_code, item.ktru_name, item.status = code["code"], code["name"], "corrected"
        item.confidence, item.zone, item.summary = 100.0, "high", "Код выбран вручную"
        session.commit()
        return to_dict(item)


class CharacteristicIn(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: str = Field("", max_length=100)
    value: str = Field(min_length=1, max_length=100)
    unit: str = Field("", max_length=30)


class AttributesIn(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: str = Field(min_length=1, max_length=300)
    brand: str = Field("", max_length=100)
    model: str = Field("", max_length=100)
    characteristics: list[CharacteristicIn] = Field(default_factory=list, max_length=30)


def manual_search_text(body: AttributesIn) -> str:
    """The query is built like the network's search_text: name, then «name: value unit»."""
    parts = [body.name]
    for c in body.characteristics:
        value = f"{c.value} {c.unit}".strip()
        parts.append(f"{c.name}: {value}" if c.name else value)
    return ". ".join(parts)


@app.put("/api/items/{item_id}/attributes")
def edit_attributes(item_id: int, body: AttributesIn):
    """A person fixes the attributes; the code is searched again by the fixed text."""
    catalog = require_catalog()
    query = manual_search_text(body)
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        include_inactive = session.get(Job, item.job_id).include_inactive
        vectors = embedder.embed([query])
        [found] = catalog.search(vectors, [query], include_inactive)
        remember_original(item)  # "revert" brings back what the system proposed
        push_history(item)
        old = item.attributes or {}
        # "fallback" stays: the row still counts as one the network did not answer
        keep = ("columns", "original", "history", "fallback")
        kept = {key: old[key] for key in keep if key in old}
        attributes = body.model_dump() | {"search_text": query, "source": "manual"}
        if vectors is None:
            attributes["words_only"] = True
        item.attributes = attributes | kept
        apply_candidates(item, found)
        item.status = "corrected"  # a person has looked at the row, whatever the new confidence
        item.summary = f"Атрибуты исправлены вручную. {item.summary}"
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/accept")
def accept_item(item_id: int):
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if not item.ktru_code:
            raise HTTPException(400, "У позиции нет кода: выберите его вручную")
        remember_original(item)
        item.status = "accepted"
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/undo-code")
def undo_code(item_id: int):
    """Brings back the code the item had before the last manual change (also the old status)."""
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        attributes = dict(item.attributes or {})
        history = list(attributes.get("history", []))
        if not history:
            raise HTTPException(400, "Предыдущего кода нет")
        for field, value in history.pop().items():
            setattr(item, field, value)
        item.attributes = attributes | {"history": history}
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/revert")
def revert_item(item_id: int):
    """Undoes accept or a manual code: the code the system proposed and its status come back."""
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if item.status == "failed":
            raise HTTPException(400, "Позицию не удалось обработать: вернуть нечего")
        if item.status in ("accepted", "corrected"):
            restore_original(item)
            session.commit()
        return to_dict(item)


@app.post("/api/jobs/{job_id}/accept")
def accept_job(job_id: str, min_confidence: float = Query(85.0, ge=0, le=100)):
    catalog = require_catalog()
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        items = session.scalars(
            select(Item).where(
                Item.job_id == job.id,
                Item.status.in_(("auto", "needs_review")),
                Item.confidence >= min_confidence,
            )
        ).all()
        accepted = 0
        for item in items:
            code = catalog.get(item.ktru_code or "")
            # A row that does not look like a product never goes to auto: not in bulk either
            is_product = bool((item.attributes or {}).get("name"))
            if code and code["status"] == "ACTIVE" and is_product:
                remember_original(item)
                item.status, accepted = "accepted", accepted + 1
        session.commit()
        return {"accepted": accepted}


class RevertIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=1000)
    all_accepted: bool = False  # every accepted row of the job, not only the listed ones


@app.post("/api/jobs/{job_id}/revert")
def revert_items(job_id: str, body: RevertIn):
    """Returns accepted or corrected rows to review: the code the system proposed comes back."""
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if body.all_accepted:
            conditions = [Item.status == "accepted"]
        else:
            conditions = [Item.status.in_(("accepted", "corrected")), Item.id.in_(body.ids)]
        items = session.scalars(select(Item).where(Item.job_id == job.id, *conditions)).all()
        for item in items:
            restore_original(item)
        session.commit()
        return {"reverted": len(items)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Stops a queued or running job; finished rows stay for editing and export."""
    with SessionLocal() as session:
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
    event = state.cancels.get(job.id)
    if event is not None:
        event.set()
    return to_dict(job)


MAX_LOOKUP = 500  # the catalog page lists many codes; a broad query is then worth narrowing


@app.get("/api/catalog/search")
def catalog_search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=MAX_LOOKUP),
):
    return require_catalog().lookup(q, limit)


@app.get("/api/health")
def health():
    catalog = "error" if state.catalog_error else "ready" if state.catalog else "loading"
    return {
        "status": "ok",
        "catalog": catalog,
        "codes": len(state.catalog.codes) if state.catalog else 0,
        "llm": extractor.llm_state(),
        "embeddings": embedder.state(),
        "providers": [  # names and limits only, never keys
            {"name": p.name, "model": p.model, "parallel": p.parallel, "down": p.is_down()}
            for p in extractor.PROVIDERS
            if p.configured
        ],
    }


# --- export --------------------------------------------------------------------------------

STATUS_TEXT = {
    "auto": "Автоматически",
    "needs_review": "Нужна проверка",
    "corrected": "Исправлено",
    "accepted": "Принято",
    "failed": "Ошибка",
}
EXPORT_HEADER = [
    "Наименование (извлечено)",
    "Бренд",
    "Модель",
    "Характеристики (извлечено)",
    "Код КТРУ",
    "Наименование КТРУ",
    "Уверенность, %",
    "Статус кода",
    "Статус позиции",
]
CODE_STATUS_TEXT = {"ACTIVE": "действует", "TERMINATED": "отменён", "INACTIVE": "недействующий"}


def cell(value):
    """Formula injection guard: a text starting with = + - @ gets a leading apostrophe."""
    # Leading spaces or a line break do not hide a formula from a spreadsheet
    if isinstance(value, str) and (
        value[:1] in ("\t", "\r", "\n") or value.lstrip()[:1] in ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def export_rows(items: list[Item]) -> list[list]:
    columns = list(dict.fromkeys(k for i in items for k in (i.attributes or {}).get("columns", {})))
    rows = [["Строка", *columns, *EXPORT_HEADER]]
    for item in items:
        attrs = item.attributes or {}
        code = state.catalog.get(item.ktru_code) if state.catalog and item.ktru_code else None
        characteristics = "; ".join(
            f"{c.get('name')}: {c.get('value')} {c.get('unit')}".strip(": ")
            for c in attrs.get("characteristics", [])
        )
        row = [item.row_number, *(attrs.get("columns", {}).get(c, "") for c in columns)]
        row += [attrs.get(key, "") for key in ("name", "brand", "model")]
        row += [characteristics, item.ktru_code, item.ktru_name, item.confidence]
        row += [CODE_STATUS_TEXT.get(code["status"]) if code else "", STATUS_TEXT[item.status]]
        rows.append(row)
    return [[cell(value) for value in row] for row in rows]


def to_csv(rows: list[list]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "КТРУ"
    for row in rows:
        sheet.append(row)
    for header_cell in sheet[1]:
        header_cell.font = Font(bold=True)
    sheet.freeze_panes = "B2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str, format: str = Query("xlsx", pattern="^(xlsx|csv)$")):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if job.status not in ("done", "cancelled"):
            raise HTTPException(409, "Выгрузка доступна после окончания обработки")
        # The five candidates of a row (~10 KB) are not exported: they are not read from the base
        query = select(Item).options(defer(Item.candidates)).where(Item.job_id == job.id)
        items = session.scalars(query.order_by(Item.id)).all()
    rows = export_rows(list(items))
    if format == "csv":
        content, media = to_csv(rows), "text/csv; charset=utf-8"
    else:
        content = to_xlsx(rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    # the file is named after the uploaded one; the ASCII name is for old clients
    pretty = quote(f"{Path(job.filename).stem}_ktru.{format}")
    disposition = (
        f"attachment; filename=\"ktru_{str(job.id)[:8]}.{format}\"; filename*=UTF-8''{pretty}"
    )
    return Response(content, media_type=media, headers={"Content-Disposition": disposition})
